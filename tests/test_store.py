"""Unit tests for the shared store/meta helpers (no embedder / Chroma needed)."""
import os
import socket

from mem0_store import (
    expand, atomic_write, load_meta, save_meta,
    render_core_file, core_used, is_backend_up,
)


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
        assert load_meta(str(tmp_path / "nope.json")) == {"pinned": [], "access": {}}

    def test_corrupt_file_returns_defaults(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid json", encoding="utf-8")
        assert load_meta(str(p)) == {"pinned": [], "access": {}}

    def test_non_dict_json_returns_defaults(self, tmp_path):
        p = tmp_path / "list.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_meta(str(p)) == {"pinned": [], "access": {}}

    def test_roundtrip_preserves_unicode_and_keys(self, tmp_path):
        p = str(tmp_path / "m.json")
        meta = {
            "pinned": ["id1"],
            "access": {"id1": {"count": 2, "last": "2026-06-14"}},
            "extra": "쿠팡",
        }
        save_meta(p, meta)
        loaded = load_meta(p)
        assert loaded["pinned"] == ["id1"]
        assert loaded["access"]["id1"]["count"] == 2
        assert loaded["extra"] == "쿠팡"

    def test_save_empty_dict_then_load_has_defaults(self, tmp_path):
        p = str(tmp_path / "m.json")
        save_meta(p, {})
        assert load_meta(p) == {"pinned": [], "access": {}}


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
