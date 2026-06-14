# Changelog

All notable changes to **local-mem0-mcp** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-06-14

### Changed
- **Default embedder is now `intfloat/multilingual-e5-small`** (384-dim) instead of
  `all-MiniLM-L6-v2`. Memories here are bilingual (KO/EN); measured recall on a
  bilingual corpus is markedly better (MRR 0.92 vs 0.79, hit@3 1.00 vs 0.82).
  `MEM0_EMBEDDER_DIMS` stays 384; the first-run download grows from ~90 MB to
  ~470 MB.

### Migration
- **Existing stores must re-embed.** A store created with `all-MiniLM-L6-v2` will
  not match the new default's query vectors, so recall collapses until you either
  re-embed with `server/migrate_reembed.py` or pin the old model via
  `MEM0_EMBEDDER_MODEL=sentence-transformers/all-MiniLM-L6-v2`. See README →
  *Retrieval & tuning*.

## [0.1.0] — 2026-06-14

First tagged release. A fully local, zero-config [Mem0](https://github.com/mem0ai/mem0)
memory server for MCP clients on macOS: no LLM, no API key, on-demand `launchd`
lifecycle, and one shared Chroma writer across all clients.

### Added
- **Tags** — lightweight per-memory labels (e.g. a project name) stored in the
  sidecar so they survive `update_memory`: `add_memory(tags=...)`,
  `tag_memory(id, tags)`, and `search_memories(tags=...)` ANY-match scoping. Shown
  as `#tag` in tool output and filterable in the HTML viewer.
- Always-on **core memory**: `pin_memory` / `unpin_memory`, bounded by
  `MEM0_CORE_BUDGET`, mirrored to `~/.mem0-mcp/CORE_MEMORY.md`; plus the
  `load_context` / `curate_memories` prompts and `memory://{all,core,search}`
  resources.
- **Hybrid retrieval** (dense + local BM25) with non-regressing `rescue` fusion
  (default) or aggressive `rrf`; recall-eval harness at `server/eval_recall.py`.
- Read-only, self-contained **HTML memory viewer** (`server/build_memory_viewer.py`).
- **Test suite** (`tests/`, pytest) and **GitHub Actions CI** (ruff + pytest on
  Python 3.10–3.13).

### Changed
- Split the server into focused, independently testable modules:
  `server/mem0_retrieval.py` (pure tokenizer / BM25 / fusion, stdlib-only) and
  `server/mem0_store.py` (store / meta / migration helpers), reused by the
  migration scripts and the viewer.
- Dependency policy: pin `mem0ai==2.0.4` (the server relies on mem0 2.0.4
  internals); use compatible, next-major-capped ranges for `fastmcp`, `chromadb`,
  and `sentence-transformers`.
- `search_memories` loads the pin/usage sidecar once per call.

### Notes
- Measured embedder recall on a bilingual corpus (see README → *Retrieval &
  tuning*): the default `all-MiniLM-L6-v2` is English-only;
  `intfloat/multilingual-e5-small` (also 384-dim) is the recommended upgrade for
  Korean-heavy / bilingual stores — switch with `server/migrate_reembed.py`.
- Migration scripts support opt-in backup pruning via `MEM0_BACKUP_KEEP`.

[0.2.0]: https://github.com/ost527/local-mem0-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/ost527/local-mem0-mcp/releases/tag/v0.1.0
