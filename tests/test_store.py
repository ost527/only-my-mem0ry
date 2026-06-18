"""Unit tests for the shared store/meta helpers (no embedder / Chroma needed)."""
import os
import socket

import pytest

from mem0_store import (
    expand, atomic_write, load_meta, save_meta,
    render_core_file, core_used, is_backend_up,
    normalize_tags, normalize_type, MEMORY_TYPES,
    normalize_origin, PROVENANCE_ORIGINS, prune_old_backups,
    normalize_confidence, CONFIDENCE_LEVELS, parse_date, date_of,
    acquire_single_writer_lock, SingleWriterLockError, WRITER_LOCKFILE,
    recreate_collection_cosine,
)

DEFAULTS = {"pinned": [], "access": {}, "tags": {}, "types": {}, "provenance": {},
            "confidence": {}, "history": {}}


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
            "provenance": {"id1": {"origin": "explicit", "source": "user chat"}},
            "extra": "쿠팡",
        }
        save_meta(p, meta)
        loaded = load_meta(p)
        assert loaded["pinned"] == ["id1"]
        assert loaded["access"]["id1"]["count"] == 2
        assert loaded["tags"]["id1"] == ["proj", "infra"]
        assert loaded["types"]["id1"] == "decision"
        assert loaded["provenance"]["id1"]["origin"] == "explicit"
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


class TestNormalizeOrigin:
    def test_empty_inputs_return_empty_string(self):
        assert normalize_origin("") == ""
        assert normalize_origin(None) == ""
        assert normalize_origin("   ") == ""

    def test_valid_origin_is_canonicalized(self):
        assert normalize_origin("explicit") == "explicit"
        assert normalize_origin("INFERRED") == "inferred"
        assert normalize_origin("  #Imported  ") == "imported"

    def test_every_origin_is_accepted(self):
        for o in PROVENANCE_ORIGINS:
            assert normalize_origin(o) == o
            assert normalize_origin(o.upper()) == o

    def test_unknown_origin_returns_none(self):
        assert normalize_origin("guess") is None
        assert normalize_origin("explicitly") is None      # no fuzzy matching

    def test_vocabulary_is_the_expected_three(self):
        assert PROVENANCE_ORIGINS == ("explicit", "inferred", "imported")


class TestNormalizeConfidence:
    def test_empty_inputs_return_empty_string(self):
        assert normalize_confidence("") == ""
        assert normalize_confidence(None) == ""
        assert normalize_confidence("   ") == ""

    def test_valid_confidence_is_canonicalized(self):
        assert normalize_confidence("high") == "high"
        assert normalize_confidence("MEDIUM") == "medium"
        assert normalize_confidence("  #Low  ") == "low"

    def test_every_level_is_accepted(self):
        for c in CONFIDENCE_LEVELS:
            assert normalize_confidence(c) == c
            assert normalize_confidence(c.upper()) == c

    def test_unknown_confidence_returns_none(self):
        assert normalize_confidence("certain") is None
        assert normalize_confidence("0.9") is None        # no numeric precision
        assert normalize_confidence("hi") is None

    def test_vocabulary_is_low_medium_high(self):
        assert CONFIDENCE_LEVELS == ("low", "medium", "high")


class TestParseDate:
    def test_empty_inputs_return_empty_string(self):
        assert parse_date("") == ""
        assert parse_date(None) == ""
        assert parse_date("   ") == ""

    def test_plain_date_passthrough(self):
        assert parse_date("2026-06-14") == "2026-06-14"

    def test_iso_timestamp_truncated_to_date(self):
        assert parse_date("2026-06-14T10:30:00") == "2026-06-14"
        assert parse_date("2026-06-14T10:30:00+09:00") == "2026-06-14"

    def test_invalid_returns_none(self):
        assert parse_date("not-a-date") is None
        assert parse_date("2026/06/14") is None          # wrong separator
        assert parse_date("2026-13-01") is None          # impossible month

    def test_date_of_takes_day_prefix(self):
        assert date_of("2026-06-14T10:30:00") == "2026-06-14"
        assert date_of("2026-06-14") == "2026-06-14"
        assert date_of("") == ""
        assert date_of(None) == ""


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


class TestAtomicWriteDurability:
    def test_content_still_correct_after_fsync(self, tmp_path):
        # fsync is internal; just assert the durable write still produces the file
        # (and that a follow-up overwrite works) so the added flush/fsync can't
        # silently break the happy path.
        p = tmp_path / "d.txt"
        atomic_write(str(p), "first")
        atomic_write(str(p), "second")
        assert p.read_text(encoding="utf-8") == "second"
        assert not (tmp_path / "d.txt.tmp").exists()


class TestSingleWriterLock:
    def test_acquire_writes_pid_and_blocks_second(self, tmp_path):
        store = str(tmp_path / "chroma")
        fh = acquire_single_writer_lock(store, retry_seconds=0)
        try:
            lock = tmp_path / "chroma" / WRITER_LOCKFILE
            assert lock.read_text(encoding="utf-8") == str(os.getpid())
            # a second acquire is refused while the first handle holds the lock
            with pytest.raises(SingleWriterLockError):
                acquire_single_writer_lock(store, retry_seconds=0)
            # the loser must NOT have truncated the winner's pid out of the file
            assert lock.read_text(encoding="utf-8") == str(os.getpid())
        finally:
            fh.close()

    def test_releases_on_close_so_it_is_reacquirable(self, tmp_path):
        store = str(tmp_path / "chroma")
        fh = acquire_single_writer_lock(store, retry_seconds=0)
        fh.close()                       # closing the handle frees the OS lock
        fh2 = acquire_single_writer_lock(store, retry_seconds=0)
        fh2.close()

    def test_creates_missing_store_dir(self, tmp_path):
        store = str(tmp_path / "deeper" / "chroma")
        fh = acquire_single_writer_lock(store, retry_seconds=0)
        try:
            assert os.path.isdir(store)
        finally:
            fh.close()


class _FakeCollection:
    def __init__(self, metadata):
        self.metadata = metadata
        self.added = []          # one dict of kwargs per add() call

    def add(self, **kw):
        self.added.append(kw)


class _FakeClient:
    """Minimal stand-in for a chromadb client (no chromadb dependency in tests)."""

    def __init__(self, max_bs=2):
        self._max_bs = max_bs
        self.deleted = []
        self.created = None

    def delete_collection(self, name):
        self.deleted.append(name)

    def create_collection(self, name, metadata=None):
        self.created = _FakeCollection(metadata)
        return self.created

    def get_max_batch_size(self):
        return self._max_bs


class TestRecreateCollectionCosine:
    def test_batches_when_over_max_and_preserves_order(self):
        client = _FakeClient(max_bs=2)
        ids = ["a", "b", "c", "d", "e"]
        embs = [[0.1], [0.2], [0.3], [0.4], [0.5]]
        metas = [{"i": i} for i in range(5)]
        col = recreate_collection_cosine(client, "mem0", ids, embs, metas)
        assert client.deleted == ["mem0"]
        assert col.metadata == {"hnsw:space": "cosine"}
        assert len(col.added) == 3                       # ceil(5 / 2)
        assert [i for kw in col.added for i in kw["ids"]] == ids
        assert [e for kw in col.added for e in kw["embeddings"]] == embs
        assert [m for kw in col.added for m in kw["metadatas"]] == metas
        assert all("documents" not in kw for kw in col.added)

    def test_single_batch_when_under_max(self):
        client = _FakeClient(max_bs=100)
        col = recreate_collection_cosine(
            client, "c", ["a", "b"], [[1.0], [2.0]], [{}, {}], documents=["d1", "d2"])
        assert len(col.added) == 1
        assert col.added[0]["documents"] == ["d1", "d2"]

    def test_falls_back_when_max_batch_unavailable(self):
        class _NoMaxClient:                      # older client w/o get_max_batch_size
            def __init__(self):
                self.created = None

            def delete_collection(self, name):
                pass

            def create_collection(self, name, metadata=None):
                self.created = _FakeCollection(metadata)
                return self.created

        client = _NoMaxClient()
        col = recreate_collection_cosine(client, "c", ["a"], [[1.0]], [{}])
        assert len(col.added) == 1
        assert col.added[0]["ids"] == ["a"]
