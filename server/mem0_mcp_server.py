#!/usr/bin/env python3
"""
Local Mem0 MCP server — mem0 storage + the CALLING AGENT as the intelligence.

mem0's headline value is "smart memory": extract facts from text, then
automatically ADD / UPDATE / DELETE related memories (dedup, conflict
resolution, merge). That normally needs an LLM. This server has **no LLM** and
needs **no API key and no local model**: the MCP client driving these tools is
*already* a capable LLM, so IT performs that reasoning and calls the primitives
below. Embeddings + storage stay local (sentence-transformers + Chroma).

Intended workflow (the agent performs it; encoded in the tool descriptions):
  1. Extract atomic facts from the user's text (one clear fact per memory).
  2. For each fact, `search_memories` to find related / duplicate / contradicting ones.
  3. Reconcile:
       - new info            -> add_memory
       - refines/merges one  -> update_memory(id, merged_text)
       - outdated/contradicted -> delete_memory(id)
`add_memory` also returns nearby existing memories so step 3 is easy even if you
skip the explicit search.

Env vars (all optional):
  MEM0_EMBEDDER_MODEL    HF sentence-transformers model (default: intfloat/multilingual-e5-small)
  MEM0_EMBEDDER_DIMS     embedding dims (default: 384)
  MEM0_CHROMA_PATH       Chroma persist dir (default: ~/.mem0-mcp/chroma)
  MEM0_COLLECTION        collection name (default: mem0)
  MEM0_DEFAULT_USER      default user_id (default: developer_workspace)
  MEM0_RELATED_TOPK      how many nearest existing memories add_memory surfaces for
                         reconciliation (default: 3)
  MEM0_SEARCH_TOPK       how many results search_memories returns (default: 10)
  MEM0_CORE_BUDGET       max total chars of pinned (core) memories (default: 4000)
  MEM0_CORE_FILE         always-on core file mirror (default: <store parent>/CORE_MEMORY.md)
  MEM0_META_FILE         pin/usage-stats sidecar (default: <store parent>/memory_meta.json)
  MEM0_MCP_TRANSPORT     'stdio' (default) or 'http'
  MEM0_MCP_HOST          http host (default: 127.0.0.1)
  MEM0_MCP_PORT          http port (default: 8765)
  MEM0_IDLE_TIMEOUT      seconds of no MCP activity before the HTTP backend exits
                         to free RAM (default: 600; 0 disables). The per-client
                         stdio proxy keeps it warm while a client is open.
"""
import os
import time
import fcntl
import signal
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from mem0 import Memory

from mem0_instructions import INSTRUCTIONS
from mem0_store import (
    expand as _expand,
    atomic_write,
    load_meta,
    save_meta,
    render_core_file,
    core_used,
    normalize_tags,
    normalize_type,
    MEMORY_TYPES,
)
from mem0_retrieval import bm25_rank, rrf_merge, fuse_rescue, cluster_by_pairs

logger = logging.getLogger("mem0-mcp")


CHROMA_PATH = _expand(os.environ.get("MEM0_CHROMA_PATH", "~/.mem0-mcp/chroma"))
os.makedirs(CHROMA_PATH, exist_ok=True)

# Single Chroma writer, enforced at the OS level. The in-process _store_lock only
# serializes calls WITHIN this backend; it cannot protect against a *second*
# backend process opening the same store (the worst corruption/data-loss vector).
# An advisory file lock (fcntl.flock) on a lockfile inside the store dir closes
# that gap: a second writer simply refuses to start.
_SINGLE_WRITER_LOCKFILE = os.path.join(CHROMA_PATH, ".writer.lock")
_single_writer_fh = None  # held open for the process lifetime; OS frees it on exit


def _acquire_single_writer_lock(retry_seconds: float = 10.0) -> None:
    """Acquire an exclusive, non-blocking advisory lock BEFORE Chroma is opened so
    a second backend can never open the same store concurrently. On contention we
    retry briefly to ride out the restart race where an old backend is still
    exiting (launchd KeepAlive=false won't relaunch us, but the proxy re-kickstarts
    on the next call); if still locked, we log and exit rather than become a second
    writer. The fd is held for the whole process; the OS releases it on exit."""
    global _single_writer_fh
    fh = open(_SINGLE_WRITER_LOCKFILE, "w")
    deadline = time.monotonic() + max(0.0, retry_seconds)
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                fh.close()
                logger.error(
                    "another mem0 backend already holds the writer lock on %s; "
                    "refusing to start a second Chroma writer", CHROMA_PATH,
                )
                raise SystemExit(1)
            time.sleep(0.25)
    fh.truncate(0)
    fh.write(str(os.getpid()))
    fh.flush()
    _single_writer_fh = fh
    logger.info("acquired single-writer lock on %s (pid %d)", CHROMA_PATH, os.getpid())


DEFAULT_USER = os.environ.get("MEM0_DEFAULT_USER", "developer_workspace")
# How many nearest existing memories add_memory surfaces for reconciliation.
RELATED_TOPK = int(os.environ.get("MEM0_RELATED_TOPK", "3"))

# Idle auto-shutdown: the HTTP backend exits after this many seconds with no MCP
# activity, so RAM is freed once every client (proxy) has disconnected. The
# per-client stdio proxy keeps it warm while a client is open. 0 disables.
IDLE_TIMEOUT = float(os.environ.get("MEM0_IDLE_TIMEOUT", "600"))
_idle_enabled = False           # set True for the HTTP backend in __main__
_last_activity = time.monotonic()


def _touch() -> None:
    global _last_activity
    _last_activity = time.monotonic()


class _ActivityMiddleware(Middleware):
    """Refresh the activity timestamp on every incoming MCP message -- on both
    arrival and completion, so a long-running operation can't let the idle timer
    expire while it is still in flight."""
    async def __call__(self, context, call_next):
        _touch()
        try:
            return await call_next(context)
        finally:
            _touch()


async def _idle_watchdog() -> None:
    interval = min(30.0, max(5.0, IDLE_TIMEOUT / 4))
    while True:
        await asyncio.sleep(interval)
        if time.monotonic() - _last_activity <= IDLE_TIMEOUT:
            continue
        # Data-loss safety: never *start* idle-exit while a store operation is in
        # flight. A non-blocking acquire that fails means a tool holds the lock
        # (mid read/write), so defer shutdown to the next cycle rather than risk
        # interrupting it. (uvicorn's SIGTERM path also drains in-flight requests,
        # so this is belt-and-suspenders.)
        if not _store_lock.acquire(blocking=False):
            continue
        _store_lock.release()
        # Graceful shutdown; launchd won't relaunch (KeepAlive=false).
        signal.raise_signal(signal.SIGTERM)
        return


@asynccontextmanager
async def _lifespan(server):
    task = None
    if _idle_enabled and IDLE_TIMEOUT > 0:
        _touch()
        task = asyncio.create_task(_idle_watchdog())
    try:
        yield {}
    finally:
        if task:
            task.cancel()


config = {
    # Inert stub: mem0's Memory() constructs an LLM client at init, but this
    # server never calls it (every write uses infer=False; the agent does the
    # reasoning). Dummy key + dead local base_url => never reachable, never used.
    "llm": {
        "provider": "openai",
        "config": {
            "model": "unused",
            "openai_base_url": "http://127.0.0.1:1/v1",
            "api_key": "unused-agent-is-the-llm",
        },
    },
    "embedder": {
        "provider": "huggingface",
        "config": {
            "model": os.environ.get("MEM0_EMBEDDER_MODEL", "intfloat/multilingual-e5-small"),
            "embedding_dims": int(os.environ.get("MEM0_EMBEDDER_DIMS", "384")),
        },
    },
    "vector_store": {
        "provider": "chroma",
        "config": {
            "collection_name": os.environ.get("MEM0_COLLECTION", "mem0"),
            "path": CHROMA_PATH,
        },
    },
}

# Enforce single-writer BEFORE opening Chroma -- but only when this file is run as
# the backend entry point. Importing the module (tooling/tests, e.g. anything that
# just wants the tool defs) must not take the lock or block on a running backend.
if __name__ == "__main__":
    _acquire_single_writer_lock()

m = Memory.from_config(config)


def _ensure_cosine_for_new_store() -> None:
    """For a brand-new (empty) store, (re)create the collection with cosine
    distance so semantic ranking is optimal out of the box. Never touches a
    populated store -- existing installs upgrade via server/migrate_cosine.py.
    (mem0 2.0.4 creates Chroma collections with the default L2 space.)"""
    try:
        vs = m.vector_store
        cname = getattr(vs, "collection_name", os.environ.get("MEM0_COLLECTION", "mem0"))
        if (vs.collection.metadata or {}).get("hnsw:space") != "cosine" and vs.collection.count() == 0:
            vs.client.delete_collection(cname)
            vs.collection = vs.client.create_collection(cname, metadata={"hnsw:space": "cosine"})
            logger.info("initialized empty Chroma collection '%s' with cosine distance", cname)
    except Exception as e:
        logger.debug("cosine-ensure skipped: %s", e)


_ensure_cosine_for_new_store()

mcp = FastMCP("Local-Mem0-MCP", lifespan=_lifespan, instructions=INSTRUCTIONS)
mcp.add_middleware(_ActivityMiddleware())


def _results(resp):
    return resp.get("results", []) if isinstance(resp, dict) else (resp or [])


# How many results search_memories returns.
SEARCH_TOPK = int(os.environ.get("MEM0_SEARCH_TOPK", "10"))

# Near-duplicate detection (pure cosine over stored vectors; NO LLM). Used to nudge
# reconciliation: add_memory warns when a new entry is near-identical to an existing
# one, and curate_memories surfaces duplicate clusters to merge. The default is tuned
# for the default e5 embedder, whose cosine sims run high (median ~0.83 on a real
# store), so the threshold is high; retune if you change MEM0_EMBEDDER_MODEL.
_DUP_THRESHOLD = float(os.environ.get("MEM0_DUP_THRESHOLD", "0.92"))
# Skip the O(n^2) duplicate scan above this many memories (curate_memories only).
_DUP_MAX_DOCS = int(os.environ.get("MEM0_DUP_MAX_DOCS", "2000"))


# Serialize ALL store access (reads AND writes) across every client. FastMCP runs
# these sync tools in a worker threadpool (run_in_thread=True), so two tool calls
# can execute concurrently; and because every per-client proxy forwards to this one
# backend process, this single in-process lock makes every memory operation mutually
# exclusive -- no concurrent add/search/update/delete can corrupt the shared Chroma
# index or interleave a non-atomic embed->store. Data-loss safety first: we serialize
# reads too (a query during an index mutation can crash some HNSW builds). It is a
# plain Lock: mutual exclusion only, not FIFO fairness (arrival order is not needed).
# Hold it ONLY at the tool-body level; helpers like _semantic_search must stay
# lock-free so add_memory (search + add) doesn't deadlock on a non-reentrant Lock.
_store_lock = threading.Lock()


def _dense_search(query: str, uid: str, limit: int):
    """Dense vector ranking: memories by ascending vector distance (lower = more
    similar). We query the vector store directly instead of m.search() because
    mem0 2.0.4's Chroma path returns the raw distance as the score while its
    score_and_rank() clamps every result to 1.0 -- destroying ranking (all tie,
    relevant ones drop). Sorting by distance ourselves restores nearest-first."""
    try:
        emb = m.embedding_model.embed(query, "search")
        raw = m.vector_store.search(
            query=query, vectors=emb, top_k=max(limit, 1), filters={"user_id": uid}
        )
    except Exception:
        return []
    items = []
    for r in (raw or []):
        payload = getattr(r, "payload", None) or {}
        items.append({
            "id": getattr(r, "id", None),
            "memory": payload.get("data", ""),
            "score": getattr(r, "score", None),
        })
    items.sort(key=lambda x: x["score"] if x["score"] is not None else float("inf"))
    return items[:limit]


# ---- lexical (BM25) + hybrid fusion ------------------------------------------
# Why hybrid: developer memories are full of exact identifiers -- file paths,
# env-var names, IPs, function names (e.g. ~/.ssh/oracle/oracle-32min.key,
# COUPANG_SEARCH_RESULT_PRICE_ENABLED, 168.107.21.193, crawlSearchResultCard).
# Pure dense (semantic) retrieval often misses exact tokens; a lexical BM25 signal
# nails them. The pure tokenizer/BM25/fusion primitives live in mem0_retrieval;
# the env-driven knobs below configure them. All local, deterministic, no extra dep.
_HYBRID = os.environ.get("MEM0_HYBRID_SEARCH", "1") not in ("0", "false", "False", "")
_RRF_K = int(os.environ.get("MEM0_RRF_K", "60"))
# Fusion mode: "rescue" (default, non-regressing -- dense order is preserved and
# lexical only appends exact matches dense missed) or "rrf" (aggressive Reciprocal
# Rank Fusion that can reorder dense results -- verify with server/eval_recall.py
# on your own data before enabling, since it may demote a strong dense match).
_FUSION = os.environ.get("MEM0_FUSION", "rescue").strip().lower()
# Cap the lexical scan so a huge store can't slow a search; dense still covers it.
_BM25_MAX_DOCS = int(os.environ.get("MEM0_BM25_MAX_DOCS", "5000"))
_BM25_K1 = 1.5
_BM25_B = 0.75

# mem0 2.0.4's Memory.get_all() silently defaults top_k to 20, which would
# truncate the BM25 corpus, listings, and curation to 20 memories. Always pass an
# explicit generous limit through this helper instead.
_GET_ALL_TOPK = max(_BM25_MAX_DOCS, 10000)


def _get_all(uid: str):
    """ALL memories for uid as [{id, memory, created_at, ...}] (no 20-row cap)."""
    return _results(m.get_all(filters={"user_id": uid}, top_k=_GET_ALL_TOPK))


def _semantic_search(query: str, uid: str, limit: int):
    """Hybrid retrieval (default ON): combine dense vector ranking with a lexical
    BM25 ranking so both semantic paraphrases and exact-identifier matches surface.
    The tokenizer/BM25/fusion primitives are in mem0_retrieval (pure, unit-tested).
    Fusion is `rescue` by default (non-regressing) or `rrf` (aggressive) via
    MEM0_FUSION. Falls back to dense-only if the lexical path fails or hybrid is
    disabled (MEM0_HYBRID_SEARCH=0). Returns [{id, memory, score}]."""
    limit = max(limit, 1)
    # Pull more candidates than `limit` from each signal so fusion has room to work.
    cand = max(limit * 4, 20)
    dense = _dense_search(query, uid, cand)
    if not _HYBRID:
        return dense[:limit]
    try:
        corpus = _get_all(uid)
        lexical = bm25_rank(query, corpus, cand, k1=_BM25_K1, b=_BM25_B, max_docs=_BM25_MAX_DOCS)
    except Exception:
        return dense[:limit]
    if not lexical:
        return dense[:limit]
    if _FUSION == "rrf":
        return rrf_merge([dense, lexical], limit, _RRF_K)
    return fuse_rescue(dense, lexical, limit)


def _duplicate_clusters(uid: str, threshold: float, max_docs: int):
    """Likely-duplicate memory clusters, via cosine similarity over the stored
    embeddings (pure vector math; NO LLM, no new model call -- reuses the vectors
    already in Chroma). Returns [[{id, memory}, ...], ...] for clusters of >=2.
    Best-effort: returns [] on any error or if the store exceeds max_docs (the
    O(n^2) scan is for the occasional curation pass, not the hot path)."""
    try:
        import numpy as np
        data = m.vector_store.collection.get(include=["embeddings", "metadatas"])
    except Exception as e:
        logger.debug("duplicate-cluster scan skipped: %s", e)
        return []
    ids_all = data.get("ids") or []
    embs_all = data.get("embeddings")
    metas_all = data.get("metadatas") or []
    if embs_all is None or len(ids_all) < 2:
        return []
    sel = [k for k in range(len(ids_all)) if (metas_all[k] or {}).get("user_id") == uid]
    if len(sel) < 2 or len(sel) > max_docs:
        return []
    ids = [ids_all[k] for k in sel]
    text_by_id = {ids_all[k]: (metas_all[k] or {}).get("data", "") for k in sel}
    try:
        V = np.asarray([embs_all[k] for k in sel], dtype=float)
        norms = np.linalg.norm(V, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        V = V / norms
        sims = V @ V.T
    except Exception as e:
        logger.debug("duplicate-cluster math skipped: %s", e)
        return []
    n = len(ids)
    pairs = [(ids[i], ids[j]) for i in range(n) for j in range(i + 1, n)
             if sims[i, j] >= threshold]
    return [[{"id": cid, "memory": text_by_id.get(cid, "")} for cid in cl]
            for cl in cluster_by_pairs(pairs)]


# ---- core memory (pinned, bounded, always-on) + usage stats -------------------
# Retrieval-based memory has one structural weakness: the agent must *decide* to
# search. A small curated set of PINNED memories closes that gap, Hermes-style:
# it is mirrored to a markdown file (CORE_FILE) the user references from an
# always-on rules file (AGENTS.md / CLAUDE.md / .cursorrules), so those facts
# enter context every session deterministically -- no tool call, no retrieval
# roulette. Core is strictly bounded (CORE_BUDGET chars): pinning beyond the
# budget is refused, so the always-on block can never bloat every session.
# Pin state and usage stats live in a JSON sidecar (META_PATH), NOT in Chroma
# payloads: mem0's update() rebuilds payload metadata (a pinned flag would be
# silently dropped), and stats bookkeeping must never mutate the vector index.
_STATE_DIR = os.path.dirname(CHROMA_PATH)
META_PATH = _expand(os.environ.get("MEM0_META_FILE", os.path.join(_STATE_DIR, "memory_meta.json")))
CORE_FILE = _expand(os.environ.get("MEM0_CORE_FILE", os.path.join(_STATE_DIR, "CORE_MEMORY.md")))
CORE_BUDGET = int(os.environ.get("MEM0_CORE_BUDGET", "4000"))


def _load_meta() -> dict:
    """Load the pin/usage sidecar at META_PATH (see mem0_store.load_meta)."""
    return load_meta(META_PATH)


def _save_meta(meta: dict) -> None:
    """Persist the pin/usage sidecar to META_PATH (best-effort; see mem0_store)."""
    save_meta(META_PATH, meta)


def _bump_access(meta: dict, ids) -> None:
    """Mutate `meta` in place: bump per-memory usage stats (retrieval count +
    last-used date) for each id. Pure mutation, no I/O -- the caller persists. This
    lets a tool that already holds `meta` record access without a second load."""
    ids = [i for i in ids if i]
    if not ids:
        return
    today = time.strftime("%Y-%m-%d")
    for mid in ids:
        ent = meta["access"].get(mid) or {}
        ent["count"] = int(ent.get("count", 0)) + 1
        ent["last"] = today
        meta["access"][mid] = ent


def _record_access(ids) -> None:
    """Load, bump per-memory usage stats, and persist. Best-effort: stats only feed
    curation hints and must never fail or slow a search."""
    ids = [i for i in ids if i]
    if not ids:
        return
    try:
        meta = _load_meta()
        _bump_access(meta, ids)
        _save_meta(meta)
    except Exception as e:
        logger.debug("access-stats update skipped: %s", e)


def _fmt_tags(tagmap: dict, mid) -> str:
    """Trailing '  #a #b' string for a memory's tags, or '' if it has none."""
    tg = tagmap.get(mid) or []
    return ("  " + " ".join(f"#{t}" for t in tg)) if tg else ""


def _fmt_type(typemap: dict, mid) -> str:
    """Leading ' [fact]' label for a memory's semantic type, or '' if untyped."""
    t = typemap.get(mid)
    return f" [{t}]" if t else ""


def _memory_text(memory_id: str):
    """Text of one memory straight from the vector store (any user_id), or None
    if the id no longer exists."""
    try:
        rec = m.vector_store.get(memory_id)
        return (getattr(rec, "payload", None) or {}).get("data")
    except Exception:
        return None


def _core_items(meta: dict) -> list:
    """Resolve pinned ids to [{id, memory}], dropping (and persisting away) ids
    whose memory was deleted out-of-band."""
    items, stale = [], []
    for mid in meta.get("pinned", []):
        text = _memory_text(mid)
        if text is None:
            stale.append(mid)
        else:
            items.append({"id": mid, "memory": text})
    if stale:
        meta["pinned"] = [i for i in meta["pinned"] if i not in stale]
        _save_meta(meta)
    return items


def _sync_core_file(items: list) -> None:
    """Mirror core memories to CORE_FILE so always-on rules files can include
    them. Must be called after every mutation that can change core content."""
    try:
        atomic_write(CORE_FILE, render_core_file(items))
    except OSError as e:
        logger.warning("could not sync core memory file %s: %s", CORE_FILE, e)


@mcp.tool()
def add_memory(text: str, user_id: str = "", tags: str = "", mem_type: str = "") -> str:
    """Store a memory. Call this THE MOMENT a durable, reusable fact appears --
    a decision, preference, config value, path/identifier, environment quirk, or
    recurring command -- not only at the end of a task. Never store secrets
    (passwords, API keys, tokens). YOU (the calling LLM) supply the intelligence:
    - Extract atomic facts from the user's text and add each as its own memory
      (one clear, self-contained fact per call).
    - Prefer calling search_memories first to find related/duplicate/contradicting
      memories. This tool also returns nearby existing memories.
    - Keep memory consistent (mem0-style): if your new fact UPDATES or merges an
      existing one, call update_memory(id, ...); if it CONTRADICTS/obsoletes one,
      call delete_memory(id). Only add when it is genuinely new.
    If user_id is omitted, the default user is used. Optionally pass `tags`
    (comma/space-separated, e.g. a project name like "32min") to scope later
    search_memories(tags=...); tags live in the sidecar and survive update_memory.
    Optionally pass `mem_type` -- ONE semantic category that says what this memory
    IS, from: fact, preference, decision, instruction, goal, commitment,
    relationship, context, event, learning, observation, artifact, error -- so you
    can later scope recall by kind (search_memories(mem_type=...)). An unrecognized
    mem_type is ignored with a warning (the memory is still stored); you can set it
    later with set_memory_type."""
    uid = user_id or DEFAULT_USER
    norm_type = normalize_type(mem_type)
    type_warning = None
    if norm_type is None:                     # non-empty but not a recognized type
        type_warning = (f"⚠️ Ignored unknown type '{mem_type}'. Valid types: "
                        f"{', '.join(MEMORY_TYPES)}. Stored WITHOUT a type — set "
                        f"one later with set_memory_type(id, type).")
        norm_type = ""
    try:
        with _store_lock:
            # Dense nearest EXISTING memories (computed BEFORE the add) so we can
            # flag near-duplicates and nudge reconciliation instead of piling up.
            related = _dense_search(text, uid, RELATED_TOPK)
            added = _results(m.add(text, user_id=uid, infer=False))
            new_id = added[0].get("id", "N/A") if added else "N/A"
            norm = normalize_tags(tags)
            if (norm or norm_type) and new_id != "N/A":
                meta = _load_meta()
                if norm:
                    meta["tags"][new_id] = norm
                if norm_type:
                    meta["types"][new_id] = norm_type
                _save_meta(meta)

        def _sim(r):
            s = r.get("score")
            return None if s is None else 1.0 - s  # cosine distance -> similarity

        typestr = f" [{norm_type}]" if norm_type else ""
        tagstr = (" " + " ".join(f"#{t}" for t in norm)) if norm else ""
        out = [f"✅ Stored (id: {new_id}){typestr}{tagstr}: {text}"]
        if type_warning:
            out.append(type_warning)
        top_sim = _sim(related[0]) if related else None
        if top_sim is not None and top_sim >= _DUP_THRESHOLD:
            dup = related[0]
            out.append(f"\n⚠️ LIKELY DUPLICATE of [id: {dup.get('id', 'N/A')}] "
                       f"(cosine {top_sim:.2f}): {dup.get('memory', '(empty)')}")
            out.append("→ This new entry looks redundant. Prefer reconciling over keeping "
                       "both: update_memory(that id, merged_text) to fold them together, "
                       "then delete_memory the leftover (this new id or the old one).")
        if related:
            out.append("\n🔎 Nearest existing memories (cosine) — if your new fact "
                       "duplicates / updates / contradicts any, reconcile it:")
            for r in related:
                sim = _sim(r)
                simstr = f"{sim:.2f}" if sim is not None else "?"
                out.append(f"  • [sim {simstr}] [id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}")
            out.append("→ update_memory(id, merged_text) to refine/merge, or "
                       "delete_memory(id) to remove an outdated one.")
        return "\n".join(out)
    except Exception as e:
        return f"❌ Save failed: {e}"


@mcp.tool()
def update_memory(memory_id: str, text: str) -> str:
    """Replace an existing memory's content (by id) with refined or merged text.
    Use this during reconciliation when new information updates/merges an existing
    memory, so you don't create duplicates."""
    try:
        with _store_lock:
            m.update(memory_id, text)
            meta = _load_meta()
            if memory_id in meta["pinned"]:
                _sync_core_file(_core_items(meta))
        return f"✅ Updated memory '{memory_id}': {text}"
    except Exception as e:
        return f"❌ Update failed: {e}"


@mcp.tool()
def search_memories(query: str, user_id: str = "", tags: str = "", mem_type: str = "") -> str:
    """Search the user's long-term memory (shared across all their LLM clients).
    Call this FIRST at the start of a task (with its key terms) and BEFORE asking
    the user for information they may have provided before -- recalling is cheaper
    than re-asking. Optionally pass `tags` (comma/space-separated) to scope results
    to memories carrying ANY of those tags (e.g. a project name), and/or `mem_type`
    to scope to ONE semantic category (fact, preference, decision, instruction,
    goal, commitment, relationship, context, event, learning, observation,
    artifact, error). Filters combine (AND across the two dimensions). Returns
    memories with IDs (plus 📌, a [type] label, and #tags) so you can update_memory
    / delete_memory them."""
    try:
        uid = user_id or DEFAULT_USER
        want = set(normalize_tags(tags))
        want_type = normalize_type(mem_type)
        if want_type is None:
            return (f"❌ Unknown memory type '{mem_type}'. Valid types: "
                    f"{', '.join(MEMORY_TYPES)} (or omit mem_type).")
        with _store_lock:
            meta = _load_meta()
            tagmap = meta.get("tags", {})
            typemap = meta.get("types", {})
            if want or want_type:
                # Pull a larger pool so the post-filter still fills SEARCH_TOPK.
                pool = _semantic_search(query, uid, max(SEARCH_TOPK * 10, 100))
                results = []
                for r in pool:
                    mid = r.get("id")
                    if want and not (want & set(tagmap.get(mid) or [])):
                        continue
                    if want_type and typemap.get(mid) != want_type:
                        continue
                    results.append(r)
                    if len(results) >= SEARCH_TOPK:
                        break
            else:
                results = _semantic_search(query, uid, SEARCH_TOPK)
            _bump_access(meta, [r.get("id") for r in results])
            _save_meta(meta)
            pinned = set(meta["pinned"])
        scope_bits = []
        if want_type:
            scope_bits.append(f"type: {want_type}")
        if want:
            scope_bits.append("tags: " + ", ".join(sorted(want)))
        scope = (" [" + "; ".join(scope_bits) + "]") if scope_bits else ""
        if not results:
            return f"🔍 No results.{scope}"
        out = f"🔍 Results for '{query}'{scope}:\n\n"
        for i, r in enumerate(results, 1):
            mid = r.get("id")
            pin = " 📌" if mid in pinned else ""
            out += (f"{i}. [id: {mid or 'N/A'}]{pin}{_fmt_type(typemap, mid)} "
                    f"{r.get('memory', '(empty)')}{_fmt_tags(tagmap, mid)}\n")
        return out
    except Exception as e:
        return f"❌ Search failed: {e}"


@mcp.tool()
def list_memories(user_id: str = "") -> str:
    """List all stored memories for the (default) user. Pinned (core) memories are
    marked with 📌, the semantic [type] is shown when set, and any tags as #tag."""
    try:
        with _store_lock:
            results = _get_all(user_id or DEFAULT_USER)
            meta = _load_meta()
            pinned = set(meta["pinned"])
            tagmap = meta.get("tags", {})
            typemap = meta.get("types", {})
        if not results:
            return "📋 No memories stored."
        out = f"📋 Memories (total {len(results)}):\n\n"
        for i, r in enumerate(results, 1):
            mid = r.get("id")
            pin = " 📌" if mid in pinned else ""
            out += (f"{i}. [ID: {mid or 'N/A'}]{pin}{_fmt_type(typemap, mid)} "
                    f"{r.get('memory', '(empty)')}{_fmt_tags(tagmap, mid)}\n")
        return out
    except Exception as e:
        return f"❌ List failed: {e}"


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Delete a memory by its ID. Use during reconciliation to remove an outdated
    or contradicted memory."""
    try:
        with _store_lock:
            m.delete(memory_id)
            meta = _load_meta()
            was_pinned = memory_id in meta["pinned"]
            if was_pinned:
                meta["pinned"] = [i for i in meta["pinned"] if i != memory_id]
            meta["access"].pop(memory_id, None)
            meta["tags"].pop(memory_id, None)
            meta["types"].pop(memory_id, None)
            _save_meta(meta)
            if was_pinned:
                _sync_core_file(_core_items(meta))
        return f"✅ Deleted memory '{memory_id}'."
    except Exception as e:
        return f"❌ Delete failed: {e}"


@mcp.tool()
def tag_memory(memory_id: str, tags: str = "") -> str:
    """Set (replace) the tags on an existing memory. Tags are lightweight labels --
    typically a project name (e.g. "32min") or area (e.g. "infra") -- used to scope
    search_memories(tags=...). Pass a comma/space-separated string; an EMPTY string
    clears all tags. Tags live in the sidecar (not the vector store), so they
    survive update_memory and never affect embeddings."""
    try:
        with _store_lock:
            if _memory_text(memory_id) is None:
                return f"❌ No memory with id '{memory_id}'."
            norm = normalize_tags(tags)
            meta = _load_meta()
            if norm:
                meta["tags"][memory_id] = norm
            else:
                meta["tags"].pop(memory_id, None)
            _save_meta(meta)
        if norm:
            return f"🏷️  Tagged '{memory_id}': {' '.join('#' + t for t in norm)}"
        return f"🏷️  Cleared all tags on '{memory_id}'."
    except Exception as e:
        return f"❌ Tag failed: {e}"


@mcp.tool()
def set_memory_type(memory_id: str, mem_type: str = "") -> str:
    """Set (or clear) the semantic TYPE of an existing memory -- ONE category that
    says what the memory IS, used to scope recall with search_memories(mem_type=...).
    Valid types: fact, preference, decision, instruction, goal, commitment,
    relationship, context, event, learning, observation, artifact, error. Pass an
    EMPTY string to clear the type. The type lives in the sidecar (not the vector
    store), so it survives update_memory and never affects embeddings."""
    try:
        norm_type = normalize_type(mem_type)
        if norm_type is None:
            return (f"❌ Unknown memory type '{mem_type}'. Valid types: "
                    f"{', '.join(MEMORY_TYPES)} (empty string clears the type).")
        with _store_lock:
            if _memory_text(memory_id) is None:
                return f"❌ No memory with id '{memory_id}'."
            meta = _load_meta()
            if norm_type:
                meta["types"][memory_id] = norm_type
            else:
                meta["types"].pop(memory_id, None)
            _save_meta(meta)
        if norm_type:
            return f"🗂️  Set type of '{memory_id}' to [{norm_type}]."
        return f"🗂️  Cleared the type on '{memory_id}'."
    except Exception as e:
        return f"❌ Set type failed: {e}"


@mcp.tool()
def pin_memory(memory_id: str) -> str:
    """Pin a memory into CORE memory: the small always-on set that is mirrored to a
    file the user includes in always-on rules (AGENTS.md / CLAUDE.md), so it reaches
    EVERY session without retrieval. Pin only identity-level durable facts needed in
    most sessions (environment, key paths, core preferences, project identity).
    Core is strictly bounded; if the budget is exceeded, unpin or shorten an entry
    first. The memory stays searchable either way."""
    try:
        with _store_lock:
            text = _memory_text(memory_id)
            if text is None:
                return f"❌ No memory with id '{memory_id}'."
            meta = _load_meta()
            if memory_id in meta["pinned"]:
                return f"📌 Already pinned: [id: {memory_id}] {text}"
            items = _core_items(meta)
            used = core_used(items)
            if used + len(text) > CORE_BUDGET:
                listing = "\n".join(
                    f"  • ({len(it['memory'])} chars) [id: {it['id']}] {it['memory']}"
                    for it in items
                )
                return (f"❌ Core budget exceeded: {used}+{len(text)} > {CORE_BUDGET} chars. "
                        f"Core loads into EVERY session, so it must stay small. "
                        f"Unpin or shorten one of:\n{listing}")
            meta["pinned"].append(memory_id)
            _save_meta(meta)
            items.append({"id": memory_id, "memory": text})
            _sync_core_file(items)
        return (f"📌 Pinned to core ({used + len(text)}/{CORE_BUDGET} chars used): {text}\n"
                f"Core file: {CORE_FILE} — reference it from an always-on rules file "
                f"(AGENTS.md / CLAUDE.md / .cursorrules) so it loads every session.")
    except Exception as e:
        return f"❌ Pin failed: {e}"


@mcp.tool()
def unpin_memory(memory_id: str) -> str:
    """Remove a memory from CORE (always-on) memory. The memory itself stays stored
    and searchable; it just stops loading into every session. Use when a core fact
    no longer earns its always-on slot, or to free core budget."""
    try:
        with _store_lock:
            meta = _load_meta()
            if memory_id not in meta["pinned"]:
                return f"❌ Memory '{memory_id}' is not pinned."
            meta["pinned"] = [i for i in meta["pinned"] if i != memory_id]
            _save_meta(meta)
            _sync_core_file(_core_items(meta))
        return f"✅ Unpinned '{memory_id}' (still stored and searchable)."
    except Exception as e:
        return f"❌ Unpin failed: {e}"


# ---- low-friction recall: MCP prompt + resources -----------------------------
# Tools require the agent to *decide* to search; these make recall first-class so a
# user (or the agent) can pull memory into context with one action. The per-client
# proxy mirrors prompts/resources, so every connected client gets them too.

@mcp.prompt()
def load_context(query: str = "") -> str:
    """Pull relevant long-term memories into the conversation as context. Invoke at
    the START of a task -- optionally with a topic/query -- so the agent recalls what
    it already knows instead of asking you to re-explain. With no query, all stored
    memories are listed."""
    uid = DEFAULT_USER
    with _store_lock:
        core = _core_items(_load_meta())
        if query.strip():
            results = _semantic_search(query, uid, SEARCH_TOPK)
            _record_access([r.get("id") for r in results])
            head = f"Relevant memories for '{query}' (most relevant first):"
        else:
            results = _get_all(uid)[:SEARCH_TOPK]
            head = "Stored memories:"
    core_ids = {it["id"] for it in core}
    results = [r for r in results if r.get("id") not in core_ids]
    if not core and not results:
        return ("No stored memories yet. As you work, call add_memory to save durable "
                "facts (decisions, configs, paths, preferences) for next time.")
    lines = []
    if core:
        lines += ["Core memory (always-on):", ""]
        lines += [f"- [id: {it['id']}] 📌 {it['memory']}" for it in core]
        lines += [""]
    if results:
        lines += [head, ""]
        lines += [f"- [id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}" for r in results]
    lines += ["",
              "Treat these as established context. If any is now wrong/outdated, "
              "reconcile with update_memory/delete_memory; save new durable facts with "
              "add_memory."]
    return "\n".join(lines)


@mcp.prompt()
def curate_memories() -> str:
    """Run a maintenance pass over long-term memory, driven by YOU (the agent).
    Invoke periodically -- or whenever memory feels noisy -- to merge duplicates,
    drop stale facts, tighten wording, and keep the always-on core small and
    current. Usage stats and likely-duplicate clusters are provided as hints."""
    uid = DEFAULT_USER
    with _store_lock:
        results = _get_all(uid)
        meta = _load_meta()
        existing = {r.get("id") for r in results}
        stale_stats = [mid for mid in list(meta["access"])
                       if mid not in existing and _memory_text(mid) is None]
        if stale_stats:
            for mid in stale_stats:
                meta["access"].pop(mid, None)
            _save_meta(meta)
        core_ids = {it["id"] for it in _core_items(meta)}
        clusters = _duplicate_clusters(uid, _DUP_THRESHOLD, _DUP_MAX_DOCS)
    if not results:
        return "Nothing to curate: no memories stored."
    lines = [
        f"Memory curation pass — {len(results)} memories, {len(core_ids)} pinned "
        f"to core (budget {CORE_BUDGET} chars).",
        "",
        "Inventory (📌 = pinned to core; [type] = semantic category; "
        "used = times retrieved, last = last retrieval):",
        "",
    ]
    typemap = meta.get("types", {})
    for r in results:
        mid = r.get("id")
        st = meta["access"].get(mid) or {}
        created = (r.get("created_at") or "?")[:10]
        pin = " 📌" if mid in core_ids else ""
        lines.append(f"- [id: {mid}]{pin}{_fmt_type(typemap, mid)} (created {created}, "
                     f"used {st.get('count', 0)}x, last {st.get('last') or 'never'}) "
                     f"{r.get('memory', '(empty)')}")
    if clusters:
        lines += [
            "",
            f"🔁 Likely-duplicate clusters (cosine ≥ {_DUP_THRESHOLD}) — prime merge "
            "candidates; confirm they are truly redundant before merging:",
        ]
        for cl in clusters:
            lines.append(f"  • {len(cl)} similar:")
            for it in cl:
                lines.append(f"      [id: {it['id']}] {it['memory'][:110]}")
    lines += [
        "",
        "Curate now, one change at a time, using the tools:",
        "1. MERGE near-duplicates (see the 🔁 clusters above when present): "
        "update_memory(keep_id, merged_text) then delete_memory(other_id). Skip a "
        "cluster whose members are genuinely distinct facts.",
        "2. DELETE memories that are wrong, obsolete, superseded, or one-off trivia.",
        "3. REWRITE vague or bloated entries into one atomic, self-contained fact (update_memory).",
        "4. TYPE: set_memory_type on memories whose [type] is missing or wrong "
        "(fact, preference, decision, instruction, goal, commitment, relationship, "
        "context, event, learning, observation, artifact, error) so recall can be "
        "scoped by kind.",
        "5. CORE: pin_memory the few durable facts needed in most sessions (a high "
        "'used' count is a hint); unpin_memory core entries that no longer earn their "
        "always-on slot. Stay within the budget.",
        "6. Low usage alone is NOT a reason to delete: keep facts that are still true and durable.",
        "Finish with a short summary of what changed.",
    ]
    return "\n".join(lines)


def _answer_context(question: str, uid: str = "") -> str:
    """Build a grounded-answer prompt: retrieve the most relevant memories for
    `question` and frame them so the CALLING LLM answers FROM them (with [id]
    citations) instead of guessing. Pure retrieval + framing -- there is NO
    server-side LLM call (the client is the brain). Kept as a plain function so it
    is unit-testable independently of the MCP prompt wrapper."""
    uid = uid or DEFAULT_USER
    with _store_lock:
        results = _semantic_search(question, uid, SEARCH_TOPK)
        _record_access([r.get("id") for r in results])
    if not results:
        return (f"No stored memory is relevant to: {question!r}\n\n"
                "Tell the user nothing is saved on this yet — do NOT guess — and offer "
                "to add_memory the answer once it is known.")
    lines = [
        "Answer the question using ONLY the memories below. Do not use outside "
        "knowledge. Cite the [id] of every memory you rely on.",
        "",
        f"Question: {question}",
        "",
        "Relevant memories (most relevant first):",
        "",
    ]
    lines += [f"- [id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}" for r in results]
    lines += [
        "",
        "Rules: be concise and cite [id]s; if the memories are insufficient or "
        "contradict each other, say so explicitly instead of guessing; if any is "
        "outdated, reconcile it with update_memory / delete_memory.",
    ]
    return "\n".join(lines)


@mcp.prompt()
def answer(question: str) -> str:
    """Answer a question grounded ONLY in stored long-term memory. The server
    retrieves the most relevant memories and frames them; YOU (the calling LLM)
    write the answer from them, citing each [id] you use -- this is the local,
    no-LLM server's equivalent of a RAG `answer` (retrieval here, generation by
    you). If the memories don't contain the answer, say so rather than guessing."""
    return _answer_context(question)


@mcp.resource("memory://all")
def memory_all() -> str:
    """All stored memories for the default user, as readable text (with IDs)."""
    uid = DEFAULT_USER
    with _store_lock:
        results = _get_all(uid)
    if not results:
        return "No memories stored."
    return "\n".join(f"[id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}" for r in results)


@mcp.resource("memory://core")
def memory_core() -> str:
    """Core (always-on) memories: the pinned set, also mirrored to the core file."""
    with _store_lock:
        items = _core_items(_load_meta())
    if not items:
        return "Core memory is empty. Pin identity-level durable facts with pin_memory(id)."
    return "\n".join(f"[id: {it['id']}] {it['memory']}" for it in items)


@mcp.resource("memory://search/{query}")
def memory_search(query: str) -> str:
    """Hybrid-ranked memories relevant to {query}; read memory://search/<your terms>."""
    uid = DEFAULT_USER
    with _store_lock:
        results = _semantic_search(query, uid, SEARCH_TOPK)
        _record_access([r.get("id") for r in results])
    if not results:
        return f"No results for '{query}'."
    return "\n".join(f"[id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}" for r in results)


if __name__ == "__main__":
    # Refresh the core file at startup so it reflects the store even after
    # offline changes (migrations, restores) or a deleted/missing file.
    try:
        _sync_core_file(_core_items(_load_meta()))
    except Exception as e:
        logger.warning("core file startup sync failed: %s", e)
    transport = os.environ.get("MEM0_MCP_TRANSPORT", "stdio")
    if transport == "http":
        _idle_enabled = True   # enable idle auto-shutdown for the shared backend
        mcp.run(
            transport="http",
            host=os.environ.get("MEM0_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MEM0_MCP_PORT", "8765")),
        )
    else:
        mcp.run()
