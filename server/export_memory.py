#!/usr/bin/env python3
"""
export_memory.py — dump ALL of your only-my-mem0ry memories to a single Markdown
(MEMORY.md-style) or JSON file.

Like the HTML viewer, it reads the Chroma store + the sidecar (memory_meta.json)
DIRECTLY: no embedding model, no LLM, and no running MCP server are needed. Every
memory is exported with its id, text, semantic type, tags, provenance, confidence,
and created/updated dates. This is the local counterpart of memanto's
`memory export` / `MEMORY.md` sync.

Usage:
    .venv/bin/python server/export_memory.py                 # -> ~/.mem0-mcp/MEMORY.md
    .venv/bin/python server/export_memory.py --format json   # -> ~/.mem0-mcp/memory-export.json
    .venv/bin/python server/export_memory.py --out /tmp/dump.md

Options mirror the server env vars (MEM0_CHROMA_PATH, MEM0_COLLECTION,
MEM0_DEFAULT_USER, MEM0_META_FILE).
"""
import os
import json
import argparse

from mem0_store import expand as _expand, load_meta as _load_sidecar
# Reuse the viewer's Chroma reader + payload builder (single source of truth for
# how a memory + its sidecar metadata become one record). chromadb is imported
# lazily inside load_memories, so importing this module stays cheap (and testable).
from build_memory_viewer import load_memories, build_payload


def render_markdown(payload: dict) -> str:
    """Render the export payload (from build_payload) as a readable MEMORY.md.
    Pure (no I/O) so it is unit-testable without Chroma."""
    lines = [
        "# Memory export — only-my-mem0ry",
        "",
        (f"<!-- Generated {payload['generated']} · user {payload['user']} · "
         f"{payload['total']} memories ({payload['pinnedCount']} pinned). "
         "Auto-generated snapshot; safe to read or share. -->"),
        "",
    ]
    for r in payload["memories"]:
        bits = []
        if r.get("pinned"):
            bits.append("📌 core")
        if r.get("type"):
            bits.append(f"[{r['type']}]")
        origin, src = r.get("origin", ""), r.get("source", "")
        if origin or src:
            bits.append("«" + " · ".join(x for x in (origin, src) if x) + "»")
        if r.get("confidence"):
            bits.append(f"(conf: {r['confidence']})")
        bits += [f"#{t}" for t in (r.get("tags") or [])]
        suffix = ("  " + " ".join(bits)) if bits else ""
        lines.append(f"- **[id: {r['id']}]**{suffix}")
        for ln in (r.get("text", "") or "").splitlines() or [""]:
            lines.append(f"  {ln}")
        dates = []
        if r.get("created"):
            dates.append(f"created {str(r['created'])[:10]}")
        if r.get("updated") and r.get("updated") != r.get("created"):
            dates.append(f"updated {str(r['updated'])[:10]}")
        if r.get("count"):
            dates.append(f"used {r['count']}x")
        if dates:
            lines.append(f"  _{' · '.join(dates)}_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(payload: dict) -> str:
    """Render the export payload as pretty JSON (round-trippable). Pure."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main():
    default_chroma = _expand(os.environ.get("MEM0_CHROMA_PATH", "~/.mem0-mcp/chroma"))
    state_dir = os.path.dirname(default_chroma)
    ap = argparse.ArgumentParser(description="Export all mem0 memories to Markdown or JSON.")
    ap.add_argument("--format", choices=["md", "json"], default="md", help="output format (default: md)")
    ap.add_argument("--out", default=None,
                    help="output path (default: <store parent>/MEMORY.md or memory-export.json)")
    ap.add_argument("--chroma", default=default_chroma, help="Chroma persist dir")
    ap.add_argument("--collection", default=os.environ.get("MEM0_COLLECTION", "mem0"))
    ap.add_argument("--user", default=os.environ.get("MEM0_DEFAULT_USER", "developer_workspace"))
    ap.add_argument("--meta", default=os.environ.get("MEM0_META_FILE",
                    os.path.join(state_dir, "memory_meta.json")))
    args = ap.parse_args()

    chroma_path = _expand(args.chroma)
    meta_path = _expand(args.meta)
    out_path = _expand(args.out) if args.out else os.path.join(
        state_dir, "MEMORY.md" if args.format == "md" else "memory-export.json")

    memories = load_memories(chroma_path, args.collection, args.user)
    meta = _load_sidecar(meta_path)
    payload = build_payload(
        memories, set(meta.get("pinned") or []), meta.get("access") or {},
        meta.get("tags") or {}, meta.get("types") or {}, meta.get("provenance") or {},
        meta.get("confidence") or {}, args.user)
    text = render_json(payload) if args.format == "json" else render_markdown(payload)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("✅ Exported %d memories (%d pinned) -> %s"
          % (payload["total"], payload["pinnedCount"], out_path))


if __name__ == "__main__":
    main()
