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
import time
import socket
import shutil

PATH = os.path.abspath(os.path.expanduser(os.environ.get("MEM0_CHROMA_PATH", "~/.mem0-mcp/chroma")))
NAME = os.environ.get("MEM0_COLLECTION", "mem0")
HOST = os.environ.get("MEM0_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MEM0_MCP_PORT", "8765"))

# Refuse to run while the backend is up: concurrent Chroma access is unsafe.
_s = socket.socket()
_s.settimeout(0.3)
try:
    _s.connect((HOST, PORT))
    _s.close()
    sys.exit(f"Backend is running on {HOST}:{PORT}. Stop it first:\n"
             f"  launchctl kill TERM gui/$(id -u)/com.mem0mcp.server")
except OSError:
    pass

import chromadb

if not os.path.isdir(PATH):
    sys.exit(f"No Chroma store at {PATH}")

backup = f"{PATH}.bak.{int(time.time())}"
shutil.copytree(PATH, backup)
print("backup:", backup)

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

client.delete_collection(NAME)
new = client.create_collection(NAME, metadata={"hnsw:space": "cosine"})
kw = dict(ids=ids, embeddings=embs, metadatas=metas)
if docs and any(d is not None for d in docs):
    kw["documents"] = docs
new.add(**kw)
assert new.count() == n, f"count mismatch: {new.count()} != {n}"

print(f"done: {n} memories now use cosine. Backup kept at {backup}")
print("Restart the backend (or just use a client) to pick it up.")
