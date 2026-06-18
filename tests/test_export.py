"""Unit tests for the export renderers (pure; importable without Chroma)."""
import json

import export_memory as ex


def _payload(memories):
    return {
        "generated": "2026-06-18T12:00:00+09:00",
        "user": "u1",
        "total": len(memories),
        "pinnedCount": sum(1 for m in memories if m.get("pinned")),
        "allTags": [], "allTypes": [], "allOrigins": [], "allConfidences": [],
        "memories": memories,
    }


def _mem(**kw):
    base = {"id": "abc", "text": "hello world", "created": "2026-06-14T10:00:00",
            "updated": None, "pinned": False, "count": 0, "last": None,
            "tags": [], "type": "", "origin": "", "source": "", "confidence": ""}
    base.update(kw)
    return base


class TestRenderMarkdown:
    def test_includes_header_and_generated_comment(self):
        md = ex.render_markdown(_payload([_mem()]))
        assert md.startswith("# Memory export — local-mem0-mcp")
        assert "user u1" in md and "1 memories" in md

    def test_renders_all_metadata_for_a_memory(self):
        md = ex.render_markdown(_payload([_mem(
            id="m1", text="We deploy on Fridays", pinned=True, type="decision",
            origin="explicit", source="kickoff", confidence="high",
            tags=["infra", "32min"], created="2026-06-14T10:00:00",
            updated="2026-06-15T11:00:00", count=4)]))
        assert "[id: m1]" in md
        assert "📌 core" in md
        assert "[decision]" in md
        assert "«explicit · kickoff»" in md
        assert "(conf: high)" in md
        assert "#infra" in md and "#32min" in md
        assert "We deploy on Fridays" in md
        assert "created 2026-06-14" in md and "updated 2026-06-15" in md and "used 4x" in md

    def test_multiline_text_is_indented(self):
        md = ex.render_markdown(_payload([_mem(text="line one\nline two")]))
        assert "  line one" in md and "  line two" in md

    def test_empty_store(self):
        md = ex.render_markdown(_payload([]))
        assert md.startswith("# Memory export") and "0 memories" in md


class TestRenderJson:
    def test_roundtrips(self):
        payload = _payload([_mem(id="x", confidence="low", tags=["t"])])
        loaded = json.loads(ex.render_json(payload))
        assert loaded["total"] == 1
        assert loaded["memories"][0]["id"] == "x"
        assert loaded["memories"][0]["confidence"] == "low"
        assert loaded["memories"][0]["tags"] == ["t"]

    def test_preserves_unicode(self):
        loaded = json.loads(ex.render_json(_payload([_mem(text="쿠팡 가격")])))
        assert loaded["memories"][0]["text"] == "쿠팡 가격"
