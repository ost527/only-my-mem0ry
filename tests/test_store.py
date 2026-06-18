"""Unit tests for the shared store/meta helpers (no embedder / Chroma needed)."""
import os
import socket

from mem0_store import (
    expand, atomic_write, load_meta, save_meta,
    render_core_file, core_used, is_backend_up,
    normalize_tags, normalize_type, MEMORY_TYPES, prune_old_backups,
)

DEFAULTS = {"pinned": [], "access": {}, "tags": {}, "types": {}}


class TestExpand:
    def test_makes_relative_absolute(self):
        assert os.path.isabs(expand("foo/bar"))

    def test_expands_tilde(self):
        out = expand("~/foo")
        assert "~" not in out and os.path.isabs(out)


class TestAtomicWrite:
    def test_creates_and_overwrites(self, tmp_path):
        p = tmp_path / "f.txt"
        atomic_write(str(p), "hello")
        assert p.read_text(encoding="utf-8") == "hello"
        atomic_write(str(p), "world")
        assert p.read_text(encoding="utf-8") == "world"

    def test_leaves_no_temp_file(self, tmp_path):
        p = tmp_path / "f.txt"
        atomic_write(str(p), "data")
        assert not (tmp_path / "f.txt.tmp").exists()

    def test_writes_unicode(self, tmp_path):
        p = tmp_path / "f.txt"
        atomic_write(str(p), "쿠팡 가격 — 화면공유")
        assert p.read_text(encoding="utf-8") == "쿠팡 가격 — 화면공유"


class TestMeta:
    def test_missing_file_returns_defaults(self, tmp_path):
        assert load_meta(str(tmp_path / "nope.json")) == DEFAULTS

    def test_corrupt_file_returns_defaults(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert load_meta(str(p)) == DEFAULTS

    def test_non_dict_json_returns_defaults(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_meta(str(p)) == DEFAULTS

    def test_roundtrip_preserves_unicode_and_keys(self, tmp_path):
        p = str(tmp_path / "m.json")
        meta = {
            "pinned": ["id1"],
            "access": {"id1": {"count": 2, "last": "2026-06-14"}},
            "tags": {"id1": ["proj", "infra"]},
            "types": {"id1": "decision"},
            "extra": "쿠팡",
        }
        save_meta(p, meta)
        loaded = load_meta(p)
        assert loaded["pinned"] == ["id1"]
        assert loaded["access"]["id1"]["count"] == 2
        assert loaded["tags"]["id1"] == ["proj", "infra"]
        assert loaded["types"]["id1"] == "decision"
        assert loaded["extra"] == "쿠팡"

    def test_save_empty_dict_then_load_has_defaults(self, tmp_path):
        p = str(tmp_path / "m.json")
        save_meta(p, {})
        assert load_meta(p) == DEFAULTS


class TestRenderCoreFile:
    def test_empty_has_placeholder_and_header(self):
        text = render_core_file([])
        assert text.startswith("# Core memory (always-on)")
        assert "do not edit" in text.lower()
        assert "core memory is empty" in text

    def test_lists_items_with_ids(self):
        items = [{"id": "abc", "memory": "fact one"}, {"id": "def", "memory": "fact two"}]
        text = render_core_file(items)
        assert "- fact one  (id: abc)" in text
        assert "- fact two  (id: def)" in text


class TestCoreUsed:
    def test_sums_lengths(self):
        assert core_used([{"id": "1", "memory": "abc"}, {"id": "2", "memory": "de"}]) == 5

    def test_empty_is_zero(self):
        assert core_used([]) == 0


class TestNormalizeTags:
    def test_string_split_lower_dedup_sorted(self):
        assert normalize_tags("  #32min, Infra infra  proj-x") == ["32min", "infra", "proj-x"]

    def test_list_input_split_and_normalized(self):
        assert normalize_tags(["A", "b,c", "#a"]) == ["a", "b", "c"]

    def test_strips_leading_hash(self):
        assert normalize_tags("#tag") == ["tag"]

    def test_empty_inputs(self):
        assert normalize_tags("") == []
        assert normalize_tags(None) == []
        assert normalize_tags([]) == []


class TestNormalizeType:
    def test_empty_inputs_return_empty_string(self):
        assert normalize_type("") == ""
        assert normalize_type(None) == ""
        assert normalize_type("   ") == ""

    def test_valid_type_is_canonicalized(self):
        assert normalize_type("fact") == "fact"
        assert normalize_type("DECISION") == "decision"
        assert normalize_type("  #Preference  ") == "preference"

    def test_every_vocabulary_member_is_accepted(self):
        for t in MEMORY_TYPES:
            assert normalize_type(t) == t
            assert normalize_type(t.upper()) == t

    def test_unknown_type_returns_none(self):
        assert normalize_type("banana") is None
        assert normalize_type("facts") is None      # no fuzzy/plural matching
        assert normalize_type("pref") is None

    def test_vocabulary_is_the_expected_13(self):
        assert len(MEMORY_TYPES) == 13
        assert len(set(MEMORY_TYPES)) == 13          # no duplicates
        assert MEMORY_TYPES == tuple(t.lower() for t in MEMORY_TYPES)
        for core in ("fact", "preference", "decision", "instruction", "error"):
            assert core in MEMORY_TYPES


class TestPruneOldBackups:
    def _mk(self, tmp_path, *suffixes):
        store = tmp_path / "chroma"
        store.mkdir()
        for s in suffixes:
            (tmp_path / f"chroma.bak.{s}").mkdir()
        return str(store)

    def test_keep_zero_is_noop(self, tmp_path):
        store = self._mk(tmp_path, "1", "2", "3")
        assert prune_old_backups(store, 0) == []
        assert len(list(tmp_path.glob("chroma.bak.*"))) == 3

    def test_keeps_newest_n(self, tmp_path):
        store = self._mk(tmp_path, "1000000001", "1000000002", "1000000003", "1000000004")
        removed = prune_old_backups(store, 2)
        assert sorted(os.path.basename(r) for r in removed) == [
            "chroma.bak.1000000001", "chroma.bak.1000000002"]
        assert sorted(p.name for p in tmp_path.glob("chroma.bak.*")) == [
            "chroma.bak.1000000003", "chroma.bak.1000000004"]

    def test_keep_more_than_exist_removes_nothing(self, tmp_path):
        store = self._mk(tmp_path, "1", "2")
        assert prune_old_backups(store, 5) == []
        assert len(list(tmp_path.glob("chroma.bak.*"))) == 2


class TestIsBackendUp:
    def test_false_on_closed_port(self):
        assert is_backend_up("127.0.0.1", 1) is False

    def test_true_on_open_socket(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            assert is_backend_up("127.0.0.1", port) is True
        finally:
            srv.close()
