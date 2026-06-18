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
        mems, pinned={"a"}, access={"a": {"count": 3, "last": "2026-06-14"}},
        tags={}, types={}, provenance={}, confidence={}, user="u1")
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
    payload = v.build_payload(mems, pinned=set(), access={}, tags={}, types={}, provenance={}, confidence={}, user="u")
    assert [r["id"] for r in payload["memories"]] == ["new", "old"]


def test_build_payload_includes_tags_and_all_tags():
    mems = [
        {"id": "a", "text": "x", "created": "2026-06-14T10:00:00", "updated": None},
        {"id": "b", "text": "y", "created": "2026-06-13T10:00:00", "updated": None},
    ]
    payload = v.build_payload(
        mems, pinned=set(), access={}, tags={"a": ["infra", "32min"]}, types={}, provenance={}, confidence={}, user="u")
    assert payload["allTags"] == ["32min", "infra"]   # sorted, deduped union
    by_id = {r["id"]: r for r in payload["memories"]}
    assert by_id["a"]["tags"] == ["infra", "32min"]
    assert by_id["b"]["tags"] == []


def test_build_payload_includes_type_and_all_types():
    mems = [
        {"id": "a", "text": "x", "created": "2026-06-14T10:00:00", "updated": None},
        {"id": "b", "text": "y", "created": "2026-06-13T10:00:00", "updated": None},
        {"id": "c", "text": "z", "created": "2026-06-12T10:00:00", "updated": None},
    ]
    payload = v.build_payload(
        mems, pinned=set(), access={}, tags={},
        types={"a": "decision", "b": "preference"}, provenance={}, confidence={}, user="u")
    assert payload["allTypes"] == ["decision", "preference"]   # sorted union, untyped excluded
    by_id = {r["id"]: r for r in payload["memories"]}
    assert by_id["a"]["type"] == "decision"
    assert by_id["b"]["type"] == "preference"
    assert by_id["c"]["type"] == ""


def test_build_payload_includes_provenance_and_all_origins():
    mems = [
        {"id": "a", "text": "x", "created": "2026-06-14T10:00:00", "updated": None},
        {"id": "b", "text": "y", "created": "2026-06-13T10:00:00", "updated": None},
        {"id": "c", "text": "z", "created": "2026-06-12T10:00:00", "updated": None},
    ]
    payload = v.build_payload(
        mems, pinned=set(), access={}, tags={}, types={},
        provenance={"a": {"origin": "explicit", "source": "user chat"},
                    "b": {"origin": "imported", "source": "file:r.pdf"}},
        confidence={}, user="u")
    assert payload["allOrigins"] == ["explicit", "imported"]   # sorted union, originless excluded
    by_id = {r["id"]: r for r in payload["memories"]}
    assert by_id["a"]["origin"] == "explicit" and by_id["a"]["source"] == "user chat"
    assert by_id["b"]["origin"] == "imported"
    assert by_id["c"]["origin"] == "" and by_id["c"]["source"] == ""


def test_build_payload_includes_confidence_and_all_confidences():
    mems = [
        {"id": "a", "text": "x", "created": "2026-06-14T10:00:00", "updated": None},
        {"id": "b", "text": "y", "created": "2026-06-13T10:00:00", "updated": None},
        {"id": "c", "text": "z", "created": "2026-06-12T10:00:00", "updated": None},
    ]
    payload = v.build_payload(
        mems, pinned=set(), access={}, tags={}, types={}, provenance={},
        confidence={"a": "high", "b": "low"}, user="u")
    assert payload["allConfidences"] == ["low", "high"]   # sorted by rank (low<high), unset excluded
    by_id = {r["id"]: r for r in payload["memories"]}
    assert by_id["a"]["confidence"] == "high"
    assert by_id["b"]["confidence"] == "low"
    assert by_id["c"]["confidence"] == ""


def test_load_meta_returns_pinned_access_tags(tmp_path):
    p = tmp_path / "meta.json"
    p.write_text(json.dumps({
        "pinned": ["x"],
        "access": {"x": {"count": 1, "last": "2026-06-14"}},
        "tags": {"x": ["proj"]},
        "types": {"x": "fact"},
        "provenance": {"x": {"origin": "inferred", "source": "deduced"}},
        "confidence": {"x": "medium"},
    }), encoding="utf-8")
    pinned, access, tags, types, provenance, confidence = v.load_meta(str(p))
    assert pinned == {"x"}
    assert access["x"]["count"] == 1
    assert tags["x"] == ["proj"]
    assert types["x"] == "fact"
    assert provenance["x"]["origin"] == "inferred"
    assert confidence["x"] == "medium"


def test_load_meta_missing_is_empty(tmp_path):
    pinned, access, tags, types, provenance, confidence = v.load_meta(str(tmp_path / "none.json"))
    assert pinned == set() and access == {} and tags == {} and types == {}
    assert provenance == {} and confidence == {}
