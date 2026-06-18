"""Shared, lightweight store/meta utilities used by the server, the migration
scripts, and the HTML viewer.

Design constraint: this module imports ONLY the stdlib at top level (no mem0,
Chroma, or sentence-transformers), so importing it is cheap and side-effect-free
and it can be unit-tested without the embedder. Functions that need Chroma
(backup/recreate during migrations) take an already-constructed client as an
argument, so chromadb is never imported here.
"""
import os
import re
import json
import glob
import time
import shutil
import socket
import logging

logger = logging.getLogger("mem0-mcp.store")


def expand(p: str) -> str:
    """Absolute, user-expanded path."""
    return os.path.abspath(os.path.expanduser(p))


def atomic_write(path: str, text: str) -> None:
    """Write text durably: write to a temp file then atomically rename over the
    target, so a reader never sees a half-written file."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ---- pin/usage sidecar (memory_meta.json) ------------------------------------

def load_meta(path: str) -> dict:
    """Load the pin/usage sidecar, tolerating a missing or corrupt file. Always
    returns a dict with at least {"pinned": [...], "access": {...}, "tags": {...},
    "types": {...}}."""
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    meta.setdefault("pinned", [])
    meta.setdefault("access", {})
    meta.setdefault("tags", {})
    meta.setdefault("types", {})
    return meta


def save_meta(path: str, meta: dict) -> None:
    """Persist the sidecar atomically. Best-effort: a write failure is logged, not
    raised, because stats/pin bookkeeping must never crash a memory operation."""
    try:
        atomic_write(path, json.dumps(meta, ensure_ascii=False, indent=1))
    except OSError as e:
        logger.warning("could not persist memory meta %s: %s", path, e)


# ---- tags (lightweight labels for scoping search) ----------------------------

def normalize_tags(tags) -> list:
    """Normalize a tag spec into a sorted, deduped, lowercased list. Accepts a
    comma/space-separated string or a list; drops empties and a leading '#'.
    e.g. "Proj-32min, #infra infra" -> ["infra", "proj-32min"]."""
    if not tags:
        return []
    parts = []
    items = [tags] if isinstance(tags, str) else list(tags)
    for it in items:
        parts.extend(re.split(r"[,\s]+", str(it)))
    seen = set()
    for p in parts:
        t = p.strip().lstrip("#").strip().lower()
        if t:
            seen.add(t)
    return sorted(seen)


# ---- memory type (single semantic category per memory) -----------------------
# A controlled vocabulary (memanto-style) so the agent can categorize WHAT a
# memory is and later scope recall to one kind -- e.g. "show me my decisions" or
# "recall the user's preferences". Unlike tags (free-form, many per memory), a
# memory has at most ONE type, drawn from this fixed set, so the categorization
# stays consistent and filterable. Stored in the sidecar (memory_meta.json) like
# tags, so it survives mem0's update() and never affects embeddings/ranking.
MEMORY_TYPES = (
    "fact",          # an objective, verifiable statement
    "preference",    # how the user likes things done
    "decision",      # a choice that was made (and ideally why)
    "instruction",   # a standing directive the agent should follow
    "goal",          # a desired future outcome
    "commitment",    # a promise/obligation with a due expectation
    "relationship",  # how entities (people, systems, projects) relate
    "context",       # background/situational information
    "event",         # something that happened at a point in time
    "learning",      # an insight/lesson derived from experience
    "observation",   # a noted state of the world (less certain than a fact)
    "artifact",      # a concrete output/asset (file, path, link, snippet)
    "error",         # a recorded mistake/failure to avoid repeating
)


def normalize_type(mem_type) -> "str | None":
    """Normalize a memory type to one of MEMORY_TYPES.

    Returns:
      - ""   when the input is empty/None (i.e. "no type"),
      - the canonical lowercase type when it is recognized (leading '#' and
        surrounding whitespace tolerated, e.g. " #Decision " -> "decision"),
      - None when the input is non-empty but NOT a recognized type, so callers
        can reject it (or warn) with the valid list.

    Pure and deterministic (no aliases/fuzzy matching) so behaviour is
    predictable; the caller surfaces MEMORY_TYPES on a None result."""
    if not mem_type:
        return ""
    s = str(mem_type).strip().lstrip("#").strip().lower()
    if not s:
        return ""
    return s if s in MEMORY_TYPES else None


# ---- core (always-on) memory mirror ------------------------------------------

def render_core_file(items: list) -> str:
    """Render the always-on CORE_MEMORY.md mirror from resolved core items
    ([{id, memory}]). Pure (no I/O) so it is easy to test."""
    body = ("\n".join(f"- {it['memory']}  (id: {it['id']})" for it in items)
            if items else "(core memory is empty -- pin_memory adds entries)")
    return (
        "# Core memory (always-on) — local-mem0-mcp\n"
        "<!-- Auto-generated; do not edit. Manage with pin_memory / unpin_memory. -->\n\n"
        f"{body}\n"
    )


def core_used(items: list) -> int:
    """Total characters consumed by the given core items (for budget checks)."""
    return sum(len(it["memory"]) for it in items)


# ---- backend liveness (used by proxy + migration guards) ---------------------

def is_backend_up(host: str, port: int, timeout: float = 0.3) -> bool:
    """True if something is listening on host:port (the shared HTTP backend)."""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ---- offline migration helpers (Chroma client passed in by the caller) -------

def backup_store(path: str) -> str:
    """Copy the Chroma store dir to <path>.bak.<timestamp> and return the backup
    path. Call before any in-place migration."""
    backup = f"{path}.bak.{int(time.time())}"
    shutil.copytree(path, backup)
    return backup


def prune_old_backups(path: str, keep: int) -> list:
    """Delete all but the newest `keep` '<path>.bak.<ts>' backups, returning the
    removed paths. No-op when keep is falsy or <= 0 (the default behaviour: keep
    everything). Opt-in; the migration scripts call it via MEM0_BACKUP_KEEP."""
    if not keep or keep <= 0:
        return []
    # 10-digit unix timestamps -> lexical sort == chronological (oldest first).
    backups = sorted(glob.glob(f"{path}.bak.*"))
    removed = []
    for b in backups[:-keep]:
        try:
            shutil.rmtree(b)
            removed.append(b)
        except OSError:
            pass
    return removed


def recreate_collection_cosine(client, name: str, ids, embeddings, metadatas, documents=None):
    """Drop and recreate the named collection with cosine distance, re-adding the
    given vectors/payloads (preserving ids + metadata). `client` is an already-open
    chromadb client, so this module never imports chromadb. Returns the new
    collection; the caller is responsible for any count assertion."""
    client.delete_collection(name)
    col = client.create_collection(name, metadata={"hnsw:space": "cosine"})
    kw = dict(ids=ids, embeddings=embeddings, metadatas=metadatas)
    if documents is not None:
        kw["documents"] = documents
    col.add(**kw)
    return col
