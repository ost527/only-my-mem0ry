# mem0-mcp-toggle

A local **[Mem0](https://github.com/mem0ai/mem0) MCP server** for macOS. Memories are embedded locally and stored in **Chroma**. There is **no LLM** in the loop — your AI client is already an LLM, so *it* decides what to remember and how to reconcile it, and the server just embeds and stores.

The server is **auto-managed**: it starts when your IDE/CLI launches and shuts itself down (freeing RAM) after the last client closes — no manual switch. Multiple clients (Kiro, Claude Desktop, Cursor, …) safely share **one** backend process.

> Unofficial community tool — not affiliated with mem0ai.
>
> *Note: earlier versions used a menu-bar on/off switch (hence the repo name). That manual toggle has been replaced by the automatic lifecycle below.*

```
  ┌────────────┐  stdio   ┌───────────────┐   HTTP 127.0.0.1:8765   ┌─────────────────────┐
  │ MCP client │─spawns──▶│  mem0_proxy   │────────────────────────▶│  mem0 backend (one) │
  │ (Kiro/IDE) │◀─tools───│ (per client)  │  forwards + keepalive   │  embed + Chroma     │
  └────────────┘          └───────────────┘                         └─────────────────────┘
        │ close ─▶ proxy dies ─▶ backend idle-exits (frees ~200 MB)         ▲ single writer
   more clients ── each spawns its own proxy ───────────────────────────────┘ (shared backend)
```

## Prerequisites

- **macOS 12+**
- **Python 3.10+** (`python3`)

No LLM, API key, Xcode, or external service required — embeddings run locally.

## Install

```bash
git clone <this-repo> mem0-mcp-toggle
cd mem0-mcp-toggle
./install.sh
```

Override defaults with env vars:

```bash
MEM0_MCP_PORT=8800 \
MEM0_IDLE_TIMEOUT=900 \
./install.sh
```

`install.sh` creates a Python venv, installs deps (mem0ai, fastmcp, chromadb, sentence-transformers), and registers **one** `launchd` agent:

| Label | Role | Start policy |
|-------|------|--------------|
| `com.mem0mcp.server` | the shared mem0 HTTP backend | **on-demand** (started by the proxy; idle-exits when unused) |

The embedding model (`all-MiniLM-L6-v2`) downloads automatically on first use (needs internet once, then runs offline).

## Connect your MCP client

Add to your client's MCP config (e.g. `~/.kiro/settings/mcp.json`, Claude Desktop, Cursor) — use the **stdio proxy command** (`install.sh` prints the exact paths):

```json
{
  "mcpServers": {
    "local-mem0-mcp": {
      "command": "/ABS/PATH/mem0-mcp-toggle/.venv/bin/python3",
      "args": ["/ABS/PATH/mem0-mcp-toggle/server/mem0_proxy.py"]
    }
  }
}
```

Tools exposed: `add_memory`, `update_memory`, `search_memories`, `list_memories`, `delete_memory`.

## How the lifecycle works

1. Your IDE/CLI launches → it spawns `mem0_proxy.py` (stdio) as a child process.
2. The proxy **auto-starts the shared backend** (`launchctl kickstart`) if it isn't running, then **forwards** all tool calls to it and sends a periodic keepalive so the backend stays warm while your client is open.
3. You close the client → the proxy dies → with no proxy left to keep it warm, the backend **idle-exits** after `MEM0_IDLE_TIMEOUT` seconds and frees its RAM.
4. Open any client again → the proxy starts the backend again.

Because every proxy forwards to the **same** backend, there is a single Chroma writer even with several clients open at once.

## How memory works (the client is the brain)

mem0's value is "smart memory": extract facts, then add/update/delete to dedup and resolve conflicts. That normally needs an LLM — but your MCP client *is* a capable LLM, so it does that reasoning and calls these primitives:

- **`add_memory(text)`** — stores a fact verbatim, and returns the nearest existing memories so the client can reconcile.
- **`update_memory(id, text)`** — refine/merge an existing memory (avoid duplicates).
- **`delete_memory(id)`** — remove an outdated/contradicted memory.
- **`search_memories(query)`** / **`list_memories()`** — retrieve (with IDs) for reconciliation.

Typical flow the client follows: extract atomic facts → `search_memories` → `add`/`update`/`delete` accordingly. No second LLM, no API key, fully offline after the embedder is cached.

## Configuration (env vars)

**Backend** (`com.mem0mcp.server`; set in `launchd/com.mem0mcp.server.plist.template`, then re-run install, or pass to `install.sh`):

| Var | Default | Notes |
|-----|---------|-------|
| `MEM0_IDLE_TIMEOUT` | `600` | seconds of no activity before the backend exits (frees RAM); `0` disables |
| `MEM0_EMBEDDER_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | local embeddings |
| `MEM0_EMBEDDER_DIMS` | `384` | must match the model |
| `MEM0_CHROMA_PATH` | `~/.mem0-mcp/chroma` | vector store location |
| `MEM0_COLLECTION` | `mem0` | Chroma collection name |
| `MEM0_DEFAULT_USER` | `developer_workspace` | default `user_id` |
| `MEM0_RELATED_TOPK` | `3` | nearest memories `add_memory` surfaces for reconciliation |
| `MEM0_MCP_PORT` | `8765` | backend HTTP port (must match the proxy) |

**Proxy** (`mem0_proxy.py`; set via the `env` block of your MCP config if needed):

| Var | Default | Notes |
|-----|---------|-------|
| `MEM0_MCP_PORT` | `8765` | backend port to reach/kickstart |
| `MEM0_SERVER_LABEL` | `com.mem0mcp.server` | launchd label to start on demand |
| `MEM0_PROXY_KEEPALIVE` | `clamp(IDLE/3, 5, 120)` | seconds between keepalive pings |
| `MEM0_BACKEND_READY_TIMEOUT` | `40` | seconds to wait for the backend to come up |

## Why this design

- **No LLM — the client is the intelligence.** The agent calling these tools is already a capable LLM, so it extracts facts and decides add/update/delete itself. Running a *second* local LLM to re-extract was the biggest source of friction (had to be running, non-reasoning instruct only, slow). We use mem0's `infer=False` path: embed + store verbatim, instant and deterministic. (mem0 still constructs an LLM client internally; it is wired so it is **never contacted**.)
- **One shared HTTP backend (single Chroma writer).** Plain MCP stdio spawns a *separate* server per client, so multiple clients would open the same Chroma store with multiple writers (lock/corruption risk) and can orphan into zombies. A single shared backend avoids that.
- **Per-client stdio proxy for lifecycle.** The proxy is lightweight (no embedder/Chroma) and its lifetime tracks the client, so the backend can be started on launch and stopped on close — the on-demand behavior an HTTP URL can't give you by itself.
- **Idle auto-exit frees RAM.** The backend holds ~200 MB (embedder + runtime); it exits shortly after the last client disconnects and restarts on the next launch. No manual toggle to remember.

## Troubleshooting

- **Tools missing / client can't connect** → check the `command`/`args` paths in your MCP config point at this repo's `.venv/bin/python3` and `server/mem0_proxy.py`. The proxy logs to stderr (visible in your client's MCP logs).
- **Backend won't start** → confirm the agent is registered: `launchctl print gui/$(id -u)/com.mem0mcp.server`. Check `~/Library/Logs/mem0-mcp.log`. You can start it manually with `launchctl kickstart gui/$(id -u)/com.mem0mcp.server`.
- **First write is slow / needs internet** → the embedding model downloads once, then runs offline.
- **Free RAM right now** → close your clients (it idle-exits), or `launchctl kill TERM gui/$(id -u)/com.mem0mcp.server`.
- **Only runs while logged in** — it's a LaunchAgent (per-user GUI session), not a boot daemon.
- **Logs:** `~/Library/Logs/mem0-mcp.log`.

## Uninstall

```bash
./uninstall.sh
```
Removes the launchd backend agent. Keeps your stored memories (`~/.mem0-mcp/chroma`) and venv.

## License

MIT — see [LICENSE](LICENSE). Built on [mem0ai/mem0](https://github.com/mem0ai/mem0), [FastMCP](https://github.com/jlowin/fastmcp), [Chroma](https://github.com/chroma-core/chroma), and [sentence-transformers](https://github.com/UKPLab/sentence-transformers); each retains its own license.
