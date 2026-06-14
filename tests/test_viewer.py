"""Unit tests for the viewer's pure data transform (importable without Chroma)."""
import json

import build_memory_viewer as v


def test_build_payload_counts_and_flags():
    mems = [
        {"id": "a", "text": "hello", "created": "2026-06-14T10:00:00", "updated": None},
        {"id": "b", "text": "world", "created": "2026-06-13T09:00:00",
         "updated": "2026-06-14T11:00:00"},
    ]
    payload = v.build_payload(
        mems, pinned={"a"}, access={"a": {"count": 3, "last": "2026-06-14"}}, user="u1")
    assert payload["total"] == 2
    assert payload["pinnedCount"] == 1
    assert payload["user"] == "u1"
    by_id = {r["id"]: r for r in payload["memories"]}
    assert by_id["a"]["pinned"] is True and by_id["a"]["count"] == 3
    assert by_id["b"]["pinned"] is False and by_id["b"]["count"] == 0


def test_build_payload_sorts_newest_first():
    mems = [
        {"id": "old", "text": "x", "created": "2026-01-01T00:00:00", "updated": None},
        {"id": "new", "text": "y", "created": "2026-06-01T00:00:00", "updated": None},
    ]
    payload = v.build_payload(mems, pinned=set(), access={}, user="u")
    assert [r["id"] for r in payload["memories"]] == ["new", "old"]


def test_load_meta_returns_pinned_and_access(tmp_path):
    p = tmp_path / "meta.json"
    p.write_text(
        json.dumps({"pinned": ["x"], "access": {"x": {"count": 1, "last": "2026-06-14"}}}),
        encoding="utf-8")
    pinned, access = v.load_meta(str(p))
    assert pinned == {"x"}
    assert access["x"]["count"] == 1


def test_load_meta_missing_is_empty(tmp_path):
    pinned, access = v.load_meta(str(tmp_path / "none.json"))
    assert pinned == set() and access == {}
