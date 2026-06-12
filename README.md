# local-mem0-mcp

**English** | [한국어](README.ko.md)

**A fully local, zero-config [Mem0](https://github.com/mem0ai/mem0) memory server for MCP clients on macOS.**
No LLM, no API key, no cloud — and no switch to flip. It starts when your IDE/CLI
opens and shuts itself off (freeing RAM) when you're done.

> Unofficial community tool — not affiliated with mem0ai.

---

## Highlights

- 🧠 **No LLM in the loop.** Your MCP client is *already* a capable LLM, so it
  does the "smart memory" reasoning (extract facts, dedup, merge, resolve
  conflicts) and calls simple primitives. No second model, no API key, no cost.
- 💾 **100% local.** Embeddings run on-device (`all-MiniLM-L6-v2`); memories live
  in a local **Chroma** store at `~/.mem0-mcp/chroma`. Works offline.
- ⚡ **Auto-managed lifecycle.** Launching a client starts the backend on demand;
  closing the last client lets it idle-exit and free ~200 MB. No manual toggle.
- 🤝 **Multi-client safe.** Kiro, Claude Desktop, Cursor, … all share **one**
  backend process — a single Chroma writer, no duplicate servers, no zombies.

---

## How it fits together

```
  ┌────────────┐  stdio   ┌───────────────┐   HTTP 127.0.0.1:8765   ┌─────────────────────┐
  │ MCP client │─spawns──▶│  mem0_proxy   │────────────────────────▶│  mem0 backend (one) │
  │ (Kiro/IDE) │◀─tools───│ (per client)  │  forwards + keepalive   │  embed + Chroma     │
  └────────────┘          └───────────────┘                         └─────────────────────┘
        │ close ─▶ proxy dies ─▶ backend idle-exits (frees RAM)              ▲ single writer
   more clients ── each spawns its own lightweight proxy ────────────────────┘ (shared backend)
```

Your client launches a tiny **stdio proxy**. The proxy starts the **shared HTTP
backend** on demand and forwards every tool call to it, keeping it warm while you
work. When the last client closes, the backend idle-exits on its own.

---

## Requirements

- **macOS 12+**
- **Python 3.10+** (`python3`)

That's it — no Xcode, no API keys, no external services. The embedding model
downloads once on first use (~90 MB), then runs fully offline.

---

## Install

```bash
git clone https://github.com/ost527/local-mem0-mcp.git
cd local-mem0-mcp
./install.sh
```

`install.sh` creates a virtualenv, installs deps (mem0ai, fastmcp, chromadb,
sentence-transformers), and registers a single **on-demand** `launchd` agent for
the backend. It prints the exact MCP config snippet to copy. Tune defaults via
env vars:

```bash
MEM0_MCP_PORT=8800 MEM0_IDLE_TIMEOUT=900 ./install.sh
```

---

## Connect your MCP client

Add this to your client's MCP config (e.g. `~/.kiro/settings/mcp.json`, Claude
Desktop, Cursor) — point it at the **stdio proxy** (use the absolute paths
`install.sh` prints):

```json
{
  "mcpServers": {
    "local-mem0-mcp": {
      "command": "/ABS/PATH/local-mem0-mcp/.venv/bin/python3",
      "args": ["/ABS/PATH/local-mem0-mcp/server/mem0_proxy.py"]
    }
  }
}
```

Restart the client. The first memory call takes a few seconds (the backend cold-
starts and loads the embedder); after that it's instant.

---

## Tools

| Tool | What it does |
|------|--------------|
| `add_memory(text, user_id?)` | Store a fact verbatim. Returns the nearest existing memories so you can reconcile. |
| `update_memory(id, text)` | Replace/merge an existing memory (avoid duplicates). |
| `delete_memory(id)` | Remove an outdated or contradicted memory. |
| `search_memories(query, user_id?)` | Semantic search; returns memories **with IDs**. |
| `list_memories(user_id?)` | List everything stored (with IDs). |

---

## How memory works (the client is the brain)

Mem0's value is "smart memory": pull out the durable facts, then add / update /
delete so memory stays deduplicated and consistent. That normally needs an LLM —
but **your MCP client is one**, so it does the reasoning and drives these tools:

1. **Extract** the atomic facts worth keeping from the conversation.
2. **`search_memories`** for related / duplicate / contradicting entries.
3. **Reconcile**: `add_memory` (new) · `update_memory` (refine/merge) ·
   `delete_memory` (obsolete).

To make step 3 easy, `add_memory` also returns the nearest existing memories.
Under the hood the server uses mem0's `infer=False` path — embed and store
verbatim — so writes are instant and deterministic, with no model call.

---

## Lifecycle (auto start / stop)

1. Your IDE/CLI launches → it spawns `server/mem0_proxy.py` (stdio) as a child.
2. The proxy runs `launchctl kickstart` to start the shared backend if it isn't
   already up, then forwards tool calls and sends a periodic keepalive.
3. You close the client → the proxy dies → with nothing keeping it warm, the
   backend **idle-exits** after `MEM0_IDLE_TIMEOUT` seconds and frees its RAM.
   (It waits for any in-flight memory operation to finish first, so a write is
   never cut off mid-flight.)
4. Open any client again → the proxy starts the backend again.

Every proxy forwards to the **same** backend, so there is exactly one Chroma
writer even with several clients open at once.

---

## Configuration

**Backend** (`server/mem0_mcp_server.py`; set in
`launchd/com.mem0mcp.server.plist.template`, then re-run `install.sh`, or pass to
`install.sh`):

| Var | Default | Notes |
|-----|---------|-------|
| `MEM0_IDLE_TIMEOUT` | `600` | seconds of inactivity before the backend exits; `0` disables |
| `MEM0_EMBEDDER_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | local embedder |
| `MEM0_EMBEDDER_DIMS` | `384` | must match the model |
| `MEM0_CHROMA_PATH` | `~/.mem0-mcp/chroma` | vector store location |
| `MEM0_COLLECTION` | `mem0` | Chroma collection name |
| `MEM0_DEFAULT_USER` | `developer_workspace` | default `user_id` |
| `MEM0_RELATED_TOPK` | `3` | nearest memories `add_memory` surfaces |
| `MEM0_SEARCH_TOPK` | `10` | results `search_memories` returns |
| `MEM0_MCP_PORT` | `8765` | backend HTTP port (must match the proxy) |

**Proxy** (`server/mem0_proxy.py`; set via the `env` block of your MCP config):

| Var | Default | Notes |
|-----|---------|-------|
| `MEM0_MCP_PORT` | `8765` | backend port to reach / kickstart |
| `MEM0_SERVER_LABEL` | `com.mem0mcp.server` | launchd label to start on demand |
| `MEM0_PROXY_KEEPALIVE` | `clamp(IDLE/3, 5, 120)` | seconds between keepalive pings |
| `MEM0_BACKEND_READY_TIMEOUT` | `40` | seconds to wait for the backend to come up |

---

## Why this design

- **The client is the intelligence.** Running a *second* local LLM just to
  re-extract facts was the biggest source of friction (had to be running, had to
  be a non-reasoning instruct model, slow). Since the calling agent is already an
  LLM, we drop that entirely and use mem0's verbatim-store path. (mem0 still
  constructs an LLM client internally; it is wired so it is **never contacted**.)
- **One shared HTTP backend.** Plain MCP stdio spawns a *separate* server per
  client — multiple clients would open the same Chroma store with multiple
  writers (lock/corruption risk) and can orphan into zombie processes. A single
  shared backend gives one writer and no duplicates. Inside that backend a single
  global lock serializes **every** memory operation (reads *and* writes), so
  concurrent calls from multiple clients can never interleave or corrupt the
  store — they queue and run one at a time. An OS-level file lock on the store
  directory hard-enforces the single writer: a second backend pointed at the same
  store refuses to start rather than risk corruption. (Data-loss safety is
  prioritized over throughput here; memory ops are fast and infrequent, so the
  serialization is imperceptible.)
- **A per-client stdio proxy for lifecycle.** The proxy is lightweight (no
  embedder/Chroma) and its lifetime tracks the client, so the backend can start
  on launch and stop on close — the on-demand behaviour a bare HTTP URL can't
  provide.
- **Idle auto-exit frees RAM.** The backend holds ~200 MB; it exits shortly after
  the last client disconnects and restarts on the next launch.

---

## FAQ

**What happened to the menu-bar toggle (and the old name)?**
Early versions shipped a menu-bar on/off switch and were named `mem0-mcp-toggle`.
The toggle was replaced by the automatic lifecycle above, and the project was
renamed to `local-mem0-mcp`.

**Does it need an LLM or API key?** No. Only a local embedder, which downloads
once and then runs offline.

**Where is my data?** `~/.mem0-mcp/chroma`. Uninstalling keeps it.

**Can I run several clients at once?** Yes — they all share the one backend
(single Chroma writer).

---

## Troubleshooting

- **Tools missing / client can't connect** → check the `command`/`args` paths in
  your MCP config point at this repo's `.venv/bin/python3` and
  `server/mem0_proxy.py`. The proxy logs to stderr (visible in your client's MCP
  logs).
- **Backend won't start** → confirm the agent is registered:
  `launchctl print gui/$(id -u)/com.mem0mcp.server`. Check
  `~/Library/Logs/mem0-mcp.log`. Start it manually with
  `launchctl kickstart gui/$(id -u)/com.mem0mcp.server`.
- **Log says "refusing to start a second Chroma writer"** → expected, not a bug:
  another backend already holds the store's single-writer lock
  (`~/.mem0-mcp/chroma/.writer.lock`). Only one backend may write at a time. Use
  the one that's already up, or stop it first
  (`launchctl kill TERM gui/$(id -u)/com.mem0mcp.server`) before starting another.
  (During a normal restart the new backend briefly retries while the old one
  exits, so this only persists if a backend is genuinely still running.)
- **First write is slow / needs internet** → the embedder downloads once, then
  runs offline.
- **Search feels off on an older store** → stores created before the cosine
  upgrade use Chroma's default L2 distance; with the backend stopped, run
  `.venv/bin/python server/migrate_cosine.py` to switch to cosine (reuses
  embeddings, backs up first). New installs already use cosine.
- **Free RAM right now** → close your clients (it idle-exits), or
  `launchctl kill TERM gui/$(id -u)/com.mem0mcp.server`.
- **Only runs while logged in** — it's a LaunchAgent (per-user GUI session), not
  a boot daemon.
- **Logs:** `~/Library/Logs/mem0-mcp.log`.

---

## Uninstall

```bash
./uninstall.sh
```

Removes the launchd backend agent (and any legacy menu-bar toggle). Keeps your
stored memories (`~/.mem0-mcp/chroma`) and the venv.

---

## License

MIT — see [LICENSE](LICENSE). Built on
[mem0ai/mem0](https://github.com/mem0ai/mem0),
[FastMCP](https://github.com/jlowin/fastmcp),
[Chroma](https://github.com/chroma-core/chroma), and
[sentence-transformers](https://github.com/UKPLab/sentence-transformers); each
retains its own license.
