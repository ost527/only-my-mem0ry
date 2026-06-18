# local-mem0-mcp

**English** | [한국어](README.ko.md)

[![CI](https://github.com/ost527/local-mem0-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/ost527/local-mem0-mcp/actions/workflows/ci.yml)

**A fully local, zero-config [Mem0](https://github.com/mem0ai/mem0) memory server for MCP clients on macOS.**
No LLM, no API key, no cloud — and no switch to flip. It starts when your IDE/CLI
opens and shuts itself off (freeing RAM) when you're done.

> Unofficial community tool — not affiliated with mem0ai.

---

## Highlights

- 🧠 **No LLM in the loop.** Your MCP client is *already* a capable LLM, so it
  does the "smart memory" reasoning (extract facts, dedup, merge, resolve
  conflicts) and calls simple primitives. No second model, no API key, no cost.
- 💾 **100% local.** Embeddings run on-device (`intfloat/multilingual-e5-small`);
  memories live in a local **Chroma** store at `~/.mem0-mcp/chroma`. Works offline.
- ⚡ **Auto-managed lifecycle.** Launching a client starts the backend on demand;
  closing the last client lets it idle-exit and free ~200 MB. No manual toggle.
- 🤝 **Multi-client safe.** Kiro, Claude Desktop, Cursor, … all share **one**
  backend process — a single Chroma writer, no duplicate servers, no zombies.
- 📌 **Always-on core memory.** Pin the few must-not-forget facts; they're
  mirrored to a file your rules load every session, so they're always in context
  — no search required.

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
downloads once on first use (~470 MB for the default multilingual model), then runs
fully offline.

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
| `add_memory(text, user_id?, tags?, mem_type?)` | Store a fact verbatim. Optional `tags` (e.g. a project name) scope later search; optional `mem_type` sets ONE semantic category (see [Memory types](#memory-types-typed-semantic-memory)). Returns the nearest existing memories so you can reconcile. |
| `update_memory(id, text)` | Replace/merge an existing memory (avoid duplicates). |
| `delete_memory(id)` | Remove an outdated or contradicted memory. |
| `search_memories(query, user_id?, tags?, mem_type?)` | Semantic search; optional `tags` (ANY-match) and/or `mem_type` scope results (the two filters combine, AND). Returns memories **with IDs** (📌 pinned, `[type]`, `#tags` shown). |
| `tag_memory(id, tags)` | Set/replace a memory's tags (empty string clears). Tags live in the sidecar, so they survive `update_memory`. |
| `set_memory_type(id, mem_type)` | Set/replace a memory's semantic **type** — one of 13 categories (empty string clears). Lives in the sidecar, so it survives `update_memory`. |
| `list_memories(user_id?)` | List everything stored (with IDs; 📌 pinned, `[type]`, `#tags` shown). |
| `pin_memory(id)` | Pin a memory into always-on **core** (mirrored to a file your rules load every session). Bounded by `MEM0_CORE_BUDGET`. |
| `unpin_memory(id)` | Remove from core; the memory stays stored and searchable. |

**Prompt & resources** (for clients that surface them) make recall low-friction —
no need for the agent to *remember* to search:

| Kind | Name | What it does |
|------|------|--------------|
| Prompt | `load_context(query?)` | Pull relevant memories into the conversation as context — invoke at the start of a task so the agent recalls instead of re-asking. No query = list all. |
| Prompt | `curate_memories()` | Maintenance pass: full inventory + usage stats, with instructions for the agent to merge duplicates, drop stale facts, rewrite, and re-balance core. |
| Prompt | `answer(query)` | Answer a question grounded **only** in stored memory — the server retrieves the relevant memories and frames them; the agent answers from them with `[id]` citations (the local, no-LLM equivalent of a RAG `answer`). |
| Resource | `memory://all` | All stored memories (with IDs). |
| Resource | `memory://core` | The pinned always-on **core** set. |
| Resource | `memory://search/{query}` | Hybrid-ranked memories for `query`. |

---

## Getting agents to use memory *proactively*

Storage is half the problem; the other half is getting agents to *recall before
asking* and *save without being told* — so you never repeat yourself and tokens
aren't burned re-explaining. Three layers push for that:

1. **Server instructions** (built in). Sent to every client in the MCP
   initialize response; most clients inject them into the agent's system
   prompt: search memory at task start and before asking the user anything,
   save durable facts the moment they appear, reconcile instead of duplicating,
   never store secrets. Both the backend and the proxy declare them (a FastMCP
   proxy answers initialize itself), see `server/mem0_instructions.py`.
2. **When-to-call tool descriptions** (built in). `search_memories` and
   `add_memory` carry explicit triggers, so even an agent that reads only the
   tool schema knows *when* to fire them.
3. **A rules-file snippet** (recommended). Clients differ in whether they
   surface server instructions, so for maximum reliability also paste this into
   the agent's always-on rules (`AGENTS.md`, `CLAUDE.md`, `.cursorrules`, Kiro
   steering, ...):

   ```markdown
   ## Long-term memory (local-mem0-mcp)
   You have persistent memory shared with the user's other LLM clients/agents. Use it without being asked:
   - Task start: call search_memories with the task's key terms.
   - Before asking the user anything: search_memories first — the answer may already be stored.
   - On learning a durable fact (decision, preference, config, path, environment quirk): call add_memory immediately, one atomic fact per call.
   - Reconcile, don't duplicate: update_memory to refine/merge; delete_memory when a memory becomes wrong.
   - Never store secrets (passwords, API keys, tokens).
   ```

---

## Core memory (always-on)

Retrieval has one structural gap: the agent has to *decide* to search. **Core
memory** closes it. Pin the handful of must-not-forget facts — project identity,
key paths, environment, core preferences — and they're mirrored to a plain file,
`~/.mem0-mcp/CORE_MEMORY.md`, that your always-on rules load **every session**.
Those facts reach the agent with no tool call and no retrieval luck.

- **Pin / unpin.** `pin_memory(id)` adds a memory to core; `unpin_memory(id)`
  removes it. Either way the memory stays stored and searchable; pinned entries
  show 📌 in `search_memories` / `list_memories`.
- **Bounded by design.** Core is capped at `MEM0_CORE_BUDGET` characters
  (default 4000). It loads into *every* session, so the cap keeps that always-on
  block small — pinning past it is refused until you unpin or shorten.
- **Activate it once.** Add a line to your always-on rules file so the agent
  reads the mirror at the start of every session:

  ```markdown
  ## Core memory (always-on)
  At the START of every session, read ~/.mem0-mcp/CORE_MEMORY.md — the user's
  pinned, always-on core memory. (Claude Code: import it with `@~/.mem0-mcp/CORE_MEMORY.md`.)
  ```

The mirror file is auto-generated (re-synced on every pin/unpin and at backend
startup) — never edit it by hand. Core is also exposed as the `memory://core`
resource and shown at the top of `load_context`.

---

## Tags (lightweight scoping)

Memories can carry **tags** — short labels, typically a project name (`32min`) or
area (`infra`) — so you can scope recall to one context:

- **Set tags** when storing: `add_memory(text, tags="32min, infra")`, or label an
  existing memory with `tag_memory(id, "32min")` (an empty string clears them).
- **Scope a search**: `search_memories(query, tags="32min")` returns only memories
  carrying **any** of those tags. Without `tags`, search spans everything — so
  shared/common facts stay visible across every project.
- Tags render as `#tag` in `search_memories` / `list_memories`, and the HTML
  memory viewer gains a tag filter.

Tags live in the sidecar (`memory_meta.json`), **not** in the vector store, so they
survive `update_memory` and never affect embeddings or ranking. They are a hard
post-filter layered on top of hybrid search — complementary to `user_id` (a full
partition) and to pinning a fact into always-on **core**.

---

## Memory types (typed semantic memory)

Beyond free-form tags, each memory can carry **one semantic type** — a category
that says *what kind of thing* it is. Where a tag answers "which project?", a type
answers "is this a decision, a preference, a fact, an instruction…?" so you can
scope recall by kind ("show me the user's *preferences*", "what *decisions* were
made?"). The vocabulary is a fixed set of **13 categories** (inspired by
[memanto](https://github.com/moorcheh-ai/memanto)'s typed memory):

> `fact` · `preference` · `decision` · `instruction` · `goal` · `commitment` ·
> `relationship` · `context` · `event` · `learning` · `observation` · `artifact` ·
> `error`

- **Type a memory** when storing: `add_memory(text, mem_type="decision")`, or set
  it later with `set_memory_type(id, "decision")` (an empty string clears it). On
  `add_memory` an unrecognized type is **ignored with a warning** — the memory is
  still stored (never dropped), so you lose no data; you can type it afterwards.
  `set_memory_type` rejects an unknown type outright (nothing is at stake).
- **Scope a search**: `search_memories(query, mem_type="decision")` returns only
  memories of that type. It **combines with `tags`** (AND): e.g.
  `search_memories("auth", tags="32min", mem_type="decision")` finds *32min*
  decisions about auth. Without `mem_type`, search spans every type.
- Types render as a `[type]` label in `search_memories` / `list_memories` (and in
  the `curate_memories` inventory), and the HTML viewer gains a type filter and a
  clickable type chip per card.

A memory has **at most one** type (unlike tags, which are many and free-form); the
controlled vocabulary keeps the categorization consistent and filterable. Like
tags, the type lives in the sidecar (`memory_meta.json`), **not** the vector store,
so it survives `update_memory` and never affects embeddings or ranking — it is a
pure post-filter over hybrid search.

---

## Keeping memory tidy (curation)

Every search quietly records lightweight usage stats — retrieval count and
last-used date — per memory. The `curate_memories` prompt turns those into a
maintenance pass: it lays out the full inventory (📌 pinned, created date, usage)
and asks the agent to merge duplicates, drop stale facts, tighten wording, and
re-balance what deserves an always-on core slot — one tool call at a time. It also
**flags likely-duplicate clusters** (memories whose cosine similarity ≥
`MEM0_DUP_THRESHOLD`, computed locally over the stored embeddings — no LLM) as prime
merge candidates. Run it periodically or whenever memory feels noisy. (Low usage
alone is never a reason to delete: durable facts stay.)

---

## How memory works (the client is the brain)

Mem0's value is "smart memory": pull out the durable facts, then add / update /
delete so memory stays deduplicated and consistent. That normally needs an LLM —
but **your MCP client is one**, so it does the reasoning and drives these tools:

1. **Extract** the atomic facts worth keeping from the conversation.
2. **`search_memories`** for related / duplicate / contradicting entries.
3. **Reconcile**: `add_memory` (new) · `update_memory` (refine/merge) ·
   `delete_memory` (obsolete).

To make step 3 easy, `add_memory` returns the nearest existing memories with their
cosine similarity and **warns when the new entry looks like a near-duplicate**
(similarity ≥ `MEM0_DUP_THRESHOLD`), so you reconcile (update/merge) instead of
piling up redundant copies. Under the hood the server uses mem0's `infer=False`
path — embed and store verbatim — so writes are instant and deterministic, with no
model call.

---

## Retrieval & tuning

Search is **hybrid by default**: dense vector similarity (semantic) fused with a
local BM25 lexical signal, so both paraphrases *and* exact identifiers (file paths,
env-var names, IPs, function names) surface. Fusion defaults to **`rescue`** — it
keeps the dense ranking and only *adds* exact matches the vector model missed, so
it never reorders good dense results (provably non-regressing; its payoff grows as
the store gets larger). An aggressive Reciprocal Rank Fusion is available via
`MEM0_FUSION=rrf` (it can reorder dense results — measure first). Turn hybrid off
with `MEM0_HYBRID_SEARCH=0`. No extra dependency; all local and deterministic.

**Measure before you tune.** `server/eval_recall.py` builds a *throwaway* store
with a labeled corpus and reports hit@k / MRR for dense vs hybrid (it never touches
your real store or the backend):

```bash
.venv/bin/python server/eval_recall.py
EVAL_VERBOSE=1 .venv/bin/python server/eval_recall.py   # per-query first-hit ranks
```

**The default embedder is `intfloat/multilingual-e5-small`** (384-dim, ~470 MB),
because memories here are bilingual (KO/EN) and an English-only model misses Korean
and cross-lingual recall. Swapping `MEM0_EMBEDDER_MODEL` on a *populated* store
breaks ranking (old vectors were produced by the old model), so re-embed instead
(backs up first; stop the backend first):

```bash
# e.g. switch to the lighter English-only model
MEM0_EMBEDDER_MODEL=sentence-transformers/all-MiniLM-L6-v2 MEM0_EMBEDDER_DIMS=384 \
    .venv/bin/python server/migrate_reembed.py
```

> **Upgrading from a version before 0.2.0?** The old default was
> `all-MiniLM-L6-v2`. After updating, either re-embed your store (command above,
> with the new default `intfloat/multilingual-e5-small`) **or** keep the old model
> by setting `MEM0_EMBEDDER_MODEL=sentence-transformers/all-MiniLM-L6-v2` for the
> backend. Otherwise new query vectors won't match your stored vectors and recall
> collapses.

**Measured** on a bilingual corpus (31 memories; 22 KO/EN + cross-lingual queries;
`server/eval_recall.py`):

| embedder (384-dim) | download | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| `intfloat/multilingual-e5-small` (**default**) | ~470 MB | **0.86** | **1.00** | **1.00** | **0.92** |
| `all-MiniLM-L6-v2` (English-only, lighter) | ~90 MB | 0.73 | 0.82 | 0.91 | 0.79 |
| `paraphrase-multilingual-MiniLM-L12-v2` | ~470 MB | 0.77 | 0.86 | 0.86 | 0.81 |

All three are 384-dim, so `MEM0_EMBEDDER_DIMS` stays `384`. If your store is
**English-only** and you want the smallest download, re-embed with
`all-MiniLM-L6-v2` as shown above.

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
| `MEM0_EMBEDDER_MODEL` | `intfloat/multilingual-e5-small` | local embedder |
| `MEM0_EMBEDDER_DIMS` | `384` | must match the model |
| `MEM0_CHROMA_PATH` | `~/.mem0-mcp/chroma` | vector store location |
| `MEM0_COLLECTION` | `mem0` | Chroma collection name |
| `MEM0_DEFAULT_USER` | `developer_workspace` | default `user_id` |
| `MEM0_RELATED_TOPK` | `3` | nearest memories `add_memory` surfaces |
| `MEM0_SEARCH_TOPK` | `10` | results `search_memories` returns |
| `MEM0_CORE_BUDGET` | `4000` | max total chars of pinned (core) memories; pinning past it is refused |
| `MEM0_CORE_FILE` | `~/.mem0-mcp/CORE_MEMORY.md` | always-on core mirror file (rules files read this) |
| `MEM0_META_FILE` | `~/.mem0-mcp/memory_meta.json` | sidecar: pin state + per-memory usage stats |
| `MEM0_HYBRID_SEARCH` | `1` | hybrid dense+lexical retrieval; `0` = dense only |
| `MEM0_FUSION` | `rescue` | `rescue` (non-regressing) or `rrf` (aggressive) |
| `MEM0_RRF_K` | `60` | RRF constant (used only when `MEM0_FUSION=rrf`) |
| `MEM0_BM25_MAX_DOCS` | `5000` | cap on lexical scan size for very large stores |
| `MEM0_DUP_THRESHOLD` | `0.92` | cosine ≥ this flags a near-duplicate (`add_memory` warning + `curate_memories` clusters); tuned for the default embedder, retune if you swap it |
| `MEM0_DUP_MAX_DOCS` | `2000` | skip the O(n²) duplicate scan in `curate_memories` above this many memories |
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

## Development

The server is split into small, focused modules so the core logic is easy to test
in isolation:

- `server/mem0_retrieval.py` — pure retrieval primitives (tokenizer, BM25, rank
  fusion). Stdlib-only; no embedder or Chroma, so it imports instantly.
- `server/mem0_store.py` — shared store/meta/migration helpers (paths, atomic
  writes, the pin/usage sidecar, the core-file mirror, backend liveness, Chroma
  backup/recreate). Imported by the server and reused by the migration scripts and
  the viewer.
- `server/mem0_mcp_server.py` — the MCP tools/prompts/resources, lifecycle, and the
  dense + hybrid search that wires the modules together.

Run the tests and linter (dev tools only — **not** runtime deps, so they stay out
of `requirements.txt`):

```bash
.venv/bin/python -m pip install pytest ruff
.venv/bin/python -m pytest           # pure unit tests + integration tests
.venv/bin/ruff check server tests    # lint (pyflakes + correctness rules)
```

The unit tests (`tests/test_retrieval.py`, `test_store.py`, `test_viewer.py`) need
no model and run in milliseconds; the integration tests (`test_integration.py`)
exercise the real server on a throwaway store and **skip automatically** when the
runtime deps aren't installed. GitHub Actions (`.github/workflows/ci.yml`) runs
ruff + pytest on Python 3.10–3.13.

**Dependencies.** `mem0ai` is pinned exactly (`==2.0.4`) because the server relies
on specific mem0 2.0.4 internals; the rest use compatible ranges capped below the
next major (`fastmcp`, `chromadb`, `sentence-transformers`). When you bump any
dependency, re-run the test suite and `server/eval_recall.py` first.

---

## FAQ

**What happened to the menu-bar toggle (and the old name)?**
Early versions shipped a menu-bar on/off switch and were named `mem0-mcp-toggle`.
The toggle was replaced by the automatic lifecycle above, and the project was
renamed to `local-mem0-mcp`.

**Does it need an LLM or API key?** No. Only a local embedder, which downloads
once and then runs offline.

**What's "core memory"?** Regular memories surface only when searched; pinned
*core* memories load into **every** session via `~/.mem0-mcp/CORE_MEMORY.md` (see
[Core memory](#core-memory-always-on)). Use `pin_memory` for the few facts you
always want in context.

**Where is my data?** `~/.mem0-mcp/chroma` (vectors), plus
`~/.mem0-mcp/CORE_MEMORY.md` (pinned-core mirror) and
`~/.mem0-mcp/memory_meta.json` (pin state + usage stats). Uninstalling keeps them.

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
