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
  MEM0_EMBEDDER_MODEL    HF sentence-transformers model (default: sentence-transformers/all-MiniLM-L6-v2)
  MEM0_EMBEDDER_DIMS     embedding dims (default: 384)
  MEM0_CHROMA_PATH       Chroma persist dir (default: ~/.mem0-mcp/chroma)
  MEM0_COLLECTION        collection name (default: mem0)
  MEM0_DEFAULT_USER      default user_id (default: developer_workspace)
  MEM0_RELATED_TOPK      how many nearest existing memories add_memory surfaces for
                         reconciliation (default: 3)
  MEM0_SEARCH_TOPK       how many results search_memories returns (default: 10)
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

logger = logging.getLogger("mem0-mcp")


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


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
            "model": os.environ.get("MEM0_EMBEDDER_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
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

mcp = FastMCP("Local-Mem0-MCP", lifespan=_lifespan)
mcp.add_middleware(_ActivityMiddleware())


def _results(resp):
    return resp.get("results", []) if isinstance(resp, dict) else (resp or [])


# How many results search_memories returns.
SEARCH_TOPK = int(os.environ.get("MEM0_SEARCH_TOPK", "10"))


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


def _semantic_search(query: str, uid: str, limit: int):
    """Rank memories by ascending vector distance (lower = more similar).

    We query the vector store directly instead of using m.search(), because
    mem0 2.0.4's Chroma path returns the raw L2 *distance* as the score while
    its score_and_rank() treats that as a similarity and clamps every result to
    1.0 -- which destroys ranking (all results tie, relevant ones get dropped).
    Sorting by the distance ourselves restores correct nearest-first ranking.
    """
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


@mcp.tool()
def add_memory(text: str, user_id: str = "") -> str:
    """Store a memory. YOU (the calling LLM) supply the intelligence:
    - Extract atomic facts from the user's text and add each as its own memory
      (one clear, self-contained fact per call).
    - Prefer calling search_memories first to find related/duplicate/contradicting
      memories. This tool also returns nearby existing memories.
    - Keep memory consistent (mem0-style): if your new fact UPDATES or merges an
      existing one, call update_memory(id, ...); if it CONTRADICTS/obsoletes one,
      call delete_memory(id). Only add when it is genuinely new.
    If user_id is omitted, the default user is used."""
    uid = user_id or DEFAULT_USER
    try:
        with _store_lock:
            # Nearest existing memories (ranked by the corrected distance search).
            related = _semantic_search(text, uid, RELATED_TOPK)
            added = _results(m.add(text, user_id=uid, infer=False))
        new_id = added[0].get("id", "N/A") if added else "N/A"

        out = [f"✅ Stored (id: {new_id}): {text}"]
        if related:
            out.append("\n🔎 Nearest existing memories — if your new fact "
                       "duplicates / updates / contradicts any, reconcile it:")
            for r in related:
                out.append(f"  • [id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}")
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
        return f"✅ Updated memory '{memory_id}': {text}"
    except Exception as e:
        return f"❌ Update failed: {e}"


@mcp.tool()
def search_memories(query: str, user_id: str = "") -> str:
    """Search past memories relevant to a query/keyword. Returns memory IDs so you
    can update_memory / delete_memory them during reconciliation."""
    try:
        with _store_lock:
            results = _semantic_search(query, (user_id or DEFAULT_USER), SEARCH_TOPK)
        if not results:
            return "🔍 No results."
        out = f"🔍 Results for '{query}':\n\n"
        for i, r in enumerate(results, 1):
            out += f"{i}. [id: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}\n"
        return out
    except Exception as e:
        return f"❌ Search failed: {e}"


@mcp.tool()
def list_memories(user_id: str = "") -> str:
    """List all stored memories for the (default) user."""
    try:
        with _store_lock:
            results = _results(m.get_all(filters={"user_id": (user_id or DEFAULT_USER)}))
        if not results:
            return "📋 No memories stored."
        out = f"📋 Memories (total {len(results)}):\n\n"
        for i, r in enumerate(results, 1):
            out += f"{i}. [ID: {r.get('id', 'N/A')}] {r.get('memory', '(empty)')}\n"
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
        return f"✅ Deleted memory '{memory_id}'."
    except Exception as e:
        return f"❌ Delete failed: {e}"


if __name__ == "__main__":
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
