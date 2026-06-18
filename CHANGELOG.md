# Changelog

All notable changes to **only-my-mem0ry** are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); the project aims to follow
[Semantic Versioning](https://semver.org/).

## [0.8.1] — 2026-06-19

Hardening + a config fix from a full code review. No runtime correctness bugs were
found; these are robustness, durability, and install/doc-consistency fixes
(behavior-preserving for the running backend — ranking and tools are unchanged).

### Fixed
- **`install.sh` now actually configures the embedder.** It forwards
  `MEM0_EMBEDDER_MODEL` and `MEM0_EMBEDDER_DIMS` into the launchd plist (new
  template placeholders + `sed` rules), so the documented re-embed workflow
  (`MEM0_EMBEDDER_MODEL=… ./install.sh`, printed by `migrate_reembed.py`) really
  reconfigures the backend. Previously those vars were silently ignored, so after
  re-embedding a store to a different model the backend kept querying with the
  **default** embedder — query and stored vectors then lived in different spaces
  and recall collapsed with no error. The README / README.ko config intro now
  states exactly which vars `install.sh` forwards (`MEM0_MCP_PORT`,
  `MEM0_IDLE_TIMEOUT`, `MEM0_EMBEDDER_MODEL`, `MEM0_EMBEDDER_DIMS`); set any other
  via the plist template.
- **Single-writer lock now covers the offline writers too.** The acquire/retry
  logic moved to `mem0_store.acquire_single_writer_lock()` and is now taken by
  `migrate_cosine.py`, `migrate_reembed.py`, and `ingest_file.py` as well — closing
  the TOCTOU window where a client could kickstart the backend between their
  `is_backend_up()` check and their writes, producing a second Chroma writer. The
  lockfile is opened in append mode and truncated only **after** the lock is held,
  so a loser can no longer wipe the winner's pid out of the file.
- **Durable sidecar/core-file writes.** `atomic_write` now `flush()` + `os.fsync()`
  before the rename, so content survives a crash/power-loss after the call returns
  (it was atomic against torn reads, but not durable).
- **Large-store migrations.** `recreate_collection_cosine` re-adds rows in batches
  (≤ the client's max add-batch size) so `migrate_cosine` / `migrate_reembed` can't
  exceed Chroma's per-`add` limit on a big store.
- **HTML viewer hardening.** `esc()` now also escapes quotes (defense-in-depth for
  the copy-id attribute), and the `--user` filter is an exact match like the
  server's `get_all` (it no longer also includes rows with `user_id == None`).

### Notes
- Gates: `ruff` clean; **160** tests pass (+8 new — single-writer lock, batched
  recreate, durable write, viewer `esc` guard); `server/eval_recall.py` is
  non-regressing (dense == hybrid: hit@1 0.864 / hit@3,5 1.000 / MRR 0.917).

## [0.8.0] — 2026-06-18

memanto gap-analysis **Phase 4** (file ingest + batch add). See
`docs/memanto-gap-analysis.md`.

### Added
- **File ingest CLI** (`server/ingest_file.py`) — turn a file into memories
  (memanto's `upload`, the local way). Extracts text, splits it into
  **deterministic** chunks (paragraph boundaries, size target + slight overlap;
  no LLM, no summarization), and stores each chunk tagged with the filename and
  marked `origin=imported`, `source=file:<name>#chunk<i>`. `.txt/.md/.csv/.tsv/
  .json/.log` need only the stdlib; **PDF/DOCX/XLSX use optional parsers isolated
  in `requirements-ingest.txt`** (pypdf / python-docx / openpyxl) so they are
  installed only by people who ingest those formats. `--dry-run` previews chunks
  without writing; writing refuses while the backend is up (same exclusive-access
  rule as the migration scripts). Pure helpers `extract_text` / `chunk_text` are
  unit-tested.
- **Batch add** — new tool `add_memories(items_json)` stores MANY memories in ONE
  locked pass (the bulk counterpart of `add_memory`). `items_json` is a JSON array
  of `{text, tags?, mem_type?, origin?, source?, confidence?}`; lenient (an item
  with an unknown type/origin/confidence is still stored with the bad field
  dropped + flagged; an item with no text is skipped). Returns the new ids plus a
  summary of warnings and near-duplicate flags. Shared core `_add_many` is reused
  by the ingest CLI.

## [0.7.0] — 2026-06-18

memanto gap-analysis **Phase 3** (conflict candidates + recency tie-break).

### Added
- **Conflict candidates** (pure, deterministic, **no LLM**) surfaced in
  `curate_memories`: memory pairs whose cosine similarity is in the "same topic"
  band `[MEM0_CONFLICT_LOW, MEM0_DUP_THRESHOLD)` **and** that disagree on a
  discriminator (a number, a weekday, a boolean/antonym, or a negation) are flagged
  as **suspected contradictions** for the agent to confirm and reconcile — the same
  "client is the brain" philosophy as the duplicate clusters. New pure helper
  `is_conflict_pair` in `server/mem0_retrieval.py` (unit-tested); the cosine band is
  computed in the server from the stored embeddings (`_conflict_candidates`). New
  config `MEM0_CONFLICT_LOW` (default `0.80`).
- **Opt-in recency / confidence tie-break** — `MEM0_RECENCY_BIAS` and
  `MEM0_CONFIDENCE_BIAS` (**both default `0` = OFF**). When `> 0` they add a small
  recency / confidence nudge over the fused ranking via the pure, unit-tested
  `rerank_with_bias`; a weight `< 1` can only break **near-ties** (it never reorders
  a clear ranking, so it is provably non-regressing), while `>= 1` can reorder
  (measure with `server/eval_recall.py` first). Default-off means the ranking — and
  the recall eval — are **byte-identical** to before.

## [0.6.0] — 2026-06-18

memanto gap-analysis **Phase 2** (versioning / no silent overwrite).

### Added
- **Version history** — `update_memory` and `delete_memory` now **archive the prior
  text** to a `history` map in the sidecar before mutating (principle: never destroy
  without a backup), capped at `MEM0_HISTORY_DEPTH` entries per memory (default `5`;
  `0` disables). A deleted memory's history is **kept** so it can still be inspected
  and restored.
  - New tool `memory_history(id)` lists the archived versions (newest first) plus the
    current text.
  - New tool `restore_memory(id, n)` restores version `n` (n=1 = most recent prior).
    If the memory still exists it is updated in place (the current text is archived
    first, so a restore is itself reversible); if it was deleted, the old text is
    re-added as a **new** memory id (its tags/type/provenance/confidence are not
    carried over).
  - `load_meta` now defaults a `"history"` map (backward-compatible; no migration).

## [0.5.0] — 2026-06-18

memanto gap-analysis **Phase 1** (provenance · confidence · temporal · `answer` ·
export). All of it is sidecar metadata, post-filtering, or a prompt/CLI, so the
dense+BM25 ranking path is untouched (`server/eval_recall.py` non-regressing).

### Added
- **`answer(query)` prompt** — a grounded-QA prompt that retrieves the most
  relevant memories and frames them so the calling agent answers **only** from
  them, citing each `[id]` (and says so when memory is insufficient rather than
  guessing). The local, **no-LLM** equivalent of memanto's RAG `answer` primitive:
  the server retrieves, the client LLM generates. Retrieval logic lives in the
  unit-tested helper `_answer_context`.
- **Provenance** — each memory can record WHERE it came from: an `origin` from a
  fixed vocabulary (`explicit`, `inferred`, `imported`) plus a free-text `source`
  (e.g. `"user chat"`, `"file:report.pdf"`). `add_memory(…, origin=, source=)`
  (lenient: an unknown origin is ignored with a warning, the memory is still
  stored), new tool `set_provenance(id, origin, source)` (strict; both empty
  clears), and `search_memories(…, origin=)` post-filter. Rendered as
  `«origin · source»`; new helpers `PROVENANCE_ORIGINS` / `normalize_origin`.
- **Confidence** — each memory can carry a coarse, deterministic confidence
  (`low`, `medium`, `high` — no fake numeric precision; the agent judges it).
  `add_memory(…, confidence=)` (lenient), new tool `set_confidence(id, value)`
  (strict; empty clears), and `search_memories(…, min_confidence=)` keeps only
  memories rated at least that confident (memories with **no** confidence are
  excluded when the gate is set). Rendered as `(conf: …)`; `curate_memories` uses it
  as a re-review hint. New helpers `CONFIDENCE_LEVELS` / `CONFIDENCE_RANK` /
  `normalize_confidence`.
- **Temporal filters** — `search_memories(…, since=, until=, changed_since=)` and
  `list_memories(…, since=, until=)` scope by date (`YYYY-MM-DD`, inclusive, day
  granularity): `since`/`until` filter by CREATED date, `changed_since` by
  last-CHANGED date (updated, else created). Pure post-filter over the existing
  `created_at`/`updated_at` payload (no extra storage). New helpers
  `parse_date` / `date_of`.
- **Full export CLI** (`server/export_memory.py`) — dump ALL memories to a single
  Markdown (`MEMORY.md`-style) or JSON file with id, text, type, tags, provenance,
  confidence, and created/updated dates. Reads the Chroma store + sidecar directly
  (no model, no LLM, no running backend), like the HTML viewer. The local
  counterpart of memanto's `memory export` / `MEMORY.md` sync. Pure render
  functions (`render_markdown` / `render_json`) are unit-tested.

### Notes
- The HTML viewer (`server/build_memory_viewer.py`) gains **provenance** and
  **confidence** filter dropdowns + clickable chips (alongside the existing type/tag
  filters).
- Sidecar (`memory_meta.json`) schema grew `provenance`, `confidence`, and `history`
  maps; `load_meta` defaults them, so existing stores upgrade with **no migration**.
  `delete_memory` cleans every per-memory map (history excepted — see 0.6.0).

## [0.4.0] — 2026-06-18

### Added
- **Memory types** — typed semantic memory (inspired by
  [memanto](https://github.com/moorcheh-ai/memanto)): each memory can carry **one**
  semantic category from a fixed vocabulary of 13 — `fact`, `preference`,
  `decision`, `instruction`, `goal`, `commitment`, `relationship`, `context`,
  `event`, `learning`, `observation`, `artifact`, `error` — so recall can be scoped
  by *kind* (e.g. "show me the decisions"). Like tags, the type lives in the sidecar
  (`memory_meta.json`), **not** the vector store, so it survives `update_memory` and
  never affects embeddings or ranking (a pure post-filter over hybrid search).
  - `add_memory(text, …, mem_type=…)` sets the type at write time; an unrecognized
    type is **ignored with a warning** (the memory is still stored — no data loss).
  - New tool `set_memory_type(id, mem_type)` sets/replaces a memory's type (empty
    string clears it); it **rejects** an unknown type outright.
  - `search_memories(query, …, mem_type=…)` post-filters results to one type and
    **combines with `tags`** (AND across the two dimensions).
  - The `[type]` label is shown in `search_memories` / `list_memories` /
    `curate_memories`, and `curate_memories` gained a step to type untyped memories.
  - The HTML viewer (`build_memory_viewer.py`) gains a type-filter dropdown and a
    clickable type chip per card.
  - New helpers in `server/mem0_store.py`: `MEMORY_TYPES` (the vocabulary) and
    `normalize_type` (unit-tested); `load_meta` now defaults a `"types"` map.

## [0.3.0] — 2026-06-14

### Added
- **Near-duplicate nudges** (pure local cosine over the stored embeddings; no LLM)
  to drive reconciliation instead of letting duplicates pile up:
  - `add_memory` now shows each nearby memory's cosine similarity and prints a
    prominent **LIKELY DUPLICATE** warning (urging update/merge over keeping both)
    when the nearest existing memory is ≥ `MEM0_DUP_THRESHOLD`.
  - `curate_memories` lists **likely-duplicate clusters** (connected components of
    memories with pairwise cosine ≥ the threshold) as prime merge candidates.
  - New config: `MEM0_DUP_THRESHOLD` (default `0.92`, empirically tuned for the
    default e5 embedder, whose similarities run high) and `MEM0_DUP_MAX_DOCS`
    (default `2000`, caps the O(n²) duplicate scan in `curate_memories`).
  - New pure helper `cluster_by_pairs` in `server/mem0_retrieval.py` (unit-tested).

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
  `MEM0_CORE_BUDGET`, mirrored to `~/.only-my-mem0ry/CORE_MEMORY.md`; plus the
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

[0.8.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.8.0
[0.7.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.7.0
[0.6.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.6.0
[0.5.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.5.0
[0.4.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.4.0
[0.3.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.3.0
[0.2.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.2.0
[0.1.0]: https://github.com/ost527/only-my-mem0ry/releases/tag/v0.1.0
