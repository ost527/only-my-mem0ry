#!/usr/bin/env python3
"""Re-embed an existing mem0 Chroma store with a DIFFERENT embedding model.

Why this exists: switching MEM0_EMBEDDER_MODEL on a populated store is unsafe on
its own -- old vectors were produced by the old model, so new query vectors live
in a different space and ranking breaks. This script re-embeds every stored memory
with the new model so the whole store is consistent again.

Pick the model with data, not vibes: compare candidates on YOUR retrieval with
    .venv/bin/python server/eval_recall.py                       # current model
    MEM0_EMBEDDER_MODEL=<candidate> MEM0_EMBEDDER_DIMS=<dims> \\
        .venv/bin/python server/eval_recall.py                   # candidate
Good local, multilingual-friendly options (the store may be bilingual):
  - sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2  (384 dims)
  - intfloat/multilingual-e5-small                               (384 dims)
English-only, strong retrieval: BAAI/bge-small-en-v1.5           (384 dims)

USAGE -- stop the backend first so we have exclusive access to the store:
    launchctl kill TERM gui/$(id -u)/com.mem0mcp.server   # or close all clients
    MEM0_EMBEDDER_MODEL=intfloat/multilingual-e5-small \\
        MEM0_EMBEDDER_DIMS=384 .venv/bin/python server/migrate_reembed.py

A backup is written to <chroma_path>.bak.<timestamp> before migrating.

AFTER migrating: set the SAME MEM0_EMBEDDER_MODEL (and MEM0_EMBEDDER_DIMS) for the
backend so it embeds queries with the new model too -- e.g. re-run install.sh with
those env vars, or edit launchd/com.mem0mcp.server.plist.template. If the backend
and the store disagree on the model, search quality collapses.
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
NEW_MODEL = os.environ.get("MEM0_EMBEDDER_MODEL")
NEW_DIMS = os.environ.get("MEM0_EMBEDDER_DIMS")

if not NEW_MODEL:
    sys.exit("Set MEM0_EMBEDDER_MODEL to the target model, e.g.\n"
             "  MEM0_EMBEDDER_MODEL=intfloat/multilingual-e5-small MEM0_EMBEDDER_DIMS=384 \\\n"
             "      .venv/bin/python server/migrate_reembed.py")

# Refuse to run while the backend is up: concurrent Chroma access is unsafe (and it
# holds the single-writer lock).
_s = socket.socket()
_s.settimeout(0.3)
try:
    _s.connect((HOST, PORT))
    _s.close()
    sys.exit(f"Backend is running on {HOST}:{PORT}. Stop it first:\n"
             f"  launchctl kill TERM gui/$(id -u)/com.mem0mcp.server")
except OSError:
    pass

if not os.path.isdir(PATH):
    sys.exit(f"No Chroma store at {PATH}")

import chromadb
from sentence_transformers import SentenceTransformer

client = chromadb.PersistentClient(path=PATH)
cols = [getattr(c, "name", c) for c in client.list_collections()]
if NAME not in cols:
    sys.exit(f"collection '{NAME}' not found (have: {cols})")

col = client.get_collection(NAME)
data = col.get(include=["metadatas", "documents"])
ids, metas, docs = data["ids"], data["metadatas"], data["documents"]
n = len(ids)
if n == 0:
    sys.exit("empty collection; nothing to re-embed")

# mem0 stores the memory text in metadata["data"]; fall back to the document.
metas = [mtd or {} for mtd in metas]
docs = docs or [None] * n
texts = [(metas[i].get("data") or docs[i] or "") for i in range(n)]
if any(not t for t in texts):
    missing = sum(1 for t in texts if not t)
    sys.exit(f"{missing}/{n} memories have no recoverable text; aborting to avoid data loss")

print(f"re-embedding {n} memories with '{NEW_MODEL}' ...")
model = SentenceTransformer(NEW_MODEL)
embs = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
dim = int(embs.shape[1])
if NEW_DIMS and int(NEW_DIMS) != dim:
    sys.exit(f"model produced {dim}-dim vectors but MEM0_EMBEDDER_DIMS={NEW_DIMS}; "
             f"set MEM0_EMBEDDER_DIMS={dim} (and use the same for the backend)")

backup = f"{PATH}.bak.{int(time.time())}"
shutil.copytree(PATH, backup)
print("backup:", backup)

# Recreate the collection (cosine) and re-add with the new embeddings, preserving
# ids + metadata so memory IDs and user_id scoping are unchanged.
client.delete_collection(NAME)
new = client.create_collection(NAME, metadata={"hnsw:space": "cosine"})
kw = dict(ids=ids, embeddings=embs.tolist(), metadatas=metas)
if any(d is not None for d in docs):
    kw["documents"] = [d if d is not None else texts[i] for i, d in enumerate(docs)]
new.add(**kw)
assert new.count() == n, f"count mismatch: {new.count()} != {n}"

print(f"done: {n} memories re-embedded to {dim} dims with '{NEW_MODEL}'. Backup at {backup}")
print("NEXT: point the backend at the SAME model so queries match the new vectors:")
print(f"  MEM0_EMBEDDER_MODEL={NEW_MODEL} MEM0_EMBEDDER_DIMS={dim} ./install.sh")
print("  (or edit launchd/com.mem0mcp.server.plist.template), then restart the backend.")
