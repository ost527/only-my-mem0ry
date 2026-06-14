#!/usr/bin/env python3
"""Upgrade an existing mem0 Chroma collection from L2 (mem0's default) to cosine
distance, for better semantic-search ranking. Reuses the existing embeddings
(no re-embedding) -- only the index metric changes.

New installs already start in cosine (the server handles empty stores), so you
only need this for stores created by older versions.

USAGE -- stop the backend first so we have exclusive access to the store:
    launchctl kill TERM gui/$(id -u)/com.mem0mcp.server   # or close all clients
    .venv/bin/python server/migrate_cosine.py

A backup is written to <chroma_path>.bak.<timestamp> before migrating.
"""
import os
import sys

from mem0_store import (
    expand, is_backend_up, backup_store, prune_old_backups, recreate_collection_cosine,
)

PATH = expand(os.environ.get("MEM0_CHROMA_PATH", "~/.mem0-mcp/chroma"))
NAME = os.environ.get("MEM0_COLLECTION", "mem0")
HOST = os.environ.get("MEM0_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MEM0_MCP_PORT", "8765"))

# Refuse to run while the backend is up: concurrent Chroma access is unsafe.
if is_backend_up(HOST, PORT):
    sys.exit(f"Backend is running on {HOST}:{PORT}. Stop it first:\n"
             f"  launchctl kill TERM gui/$(id -u)/com.mem0mcp.server")

import chromadb  # noqa: E402  (deferred: skip the heavy import if the guard above exits)

if not os.path.isdir(PATH):
    sys.exit(f"No Chroma store at {PATH}")

backup = backup_store(PATH)
print("backup:", backup)
# Opt-in: keep only the newest MEM0_BACKUP_KEEP backups (0/unset = keep all).
for _old in prune_old_backups(PATH, int(os.environ.get("MEM0_BACKUP_KEEP", "0") or "0")):
    print("pruned old backup:", _old)

client = chromadb.PersistentClient(path=PATH)
cols = [getattr(c, "name", c) for c in client.list_collections()]
if NAME not in cols:
    sys.exit(f"collection '{NAME}' not found (have: {cols})")

col = client.get_collection(NAME)
if (col.metadata or {}).get("hnsw:space") == "cosine":
    print("already cosine; nothing to do")
    sys.exit(0)

data = col.get(include=["embeddings", "metadatas", "documents"])
ids, embs, metas, docs = data["ids"], data["embeddings"], data["metadatas"], data["documents"]
try:
    embs = embs.tolist()
except AttributeError:
    embs = [list(e) for e in embs]
n = len(ids)
if n == 0:
    sys.exit("empty collection; aborting")
print(f"migrating {n} memories to cosine...")

documents = docs if (docs and any(d is not None for d in docs)) else None
new = recreate_collection_cosine(client, NAME, ids, embs, metas, documents)
assert new.count() == n, f"count mismatch: {new.count()} != {n}"

print(f"done: {n} memories now use cosine. Backup kept at {backup}")
print("Restart the backend (or just use a client) to pick it up.")
