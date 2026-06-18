"""Unit tests for the file-ingest pure helpers (chunking + text extraction).

importing ingest_file pulls in only mem0_store (stdlib) at module load; the heavy
server import happens lazily inside ingest(), so these tests need no model/Chroma.
"""
import json

import ingest_file as ing


class TestChunkText:
    def test_empty_returns_empty(self):
        assert ing.chunk_text("") == []
        assert ing.chunk_text("   \n\n  ") == []

    def test_single_short_text_is_one_chunk(self):
        assert ing.chunk_text("just one short paragraph") == ["just one short paragraph"]

    def test_paragraphs_pack_under_target(self):
        out = ing.chunk_text("para one.\n\npara two.", target_chars=200)
        assert out == ["para one.\n\npara two."]

    def test_splits_when_exceeding_target(self):
        text = "\n\n".join(f"paragraph number {i} with some words" for i in range(20))
        out = ing.chunk_text(text, target_chars=100, overlap=0)
        assert len(out) > 1
        assert all(len(c) <= 100 for c in out)

    def test_long_paragraph_is_hard_split_with_overlap(self):
        body = "".join(chr(33 + (i % 90)) for i in range(300))   # 300 varied chars
        out = ing.chunk_text(body, target_chars=100, overlap=20)
        assert len(out) > 1
        assert all(len(c) <= 100 for c in out)
        # consecutive chunks share `overlap` chars (step = target - overlap = 80)
        assert out[0][-20:] == out[1][:20]

    def test_deterministic(self):
        text = "alpha.\n\nbeta.\n\ngamma."
        assert ing.chunk_text(text, 50, 10) == ing.chunk_text(text, 50, 10)


class TestExtractText:
    def test_txt(self, tmp_path):
        p = tmp_path / "n.txt"
        p.write_text("hello\nworld", encoding="utf-8")
        assert ing.extract_text(str(p)) == "hello\nworld"

    def test_md(self, tmp_path):
        p = tmp_path / "n.md"
        p.write_text("# Title\n\nbody", encoding="utf-8")
        assert "# Title" in ing.extract_text(str(p))

    def test_csv(self, tmp_path):
        p = tmp_path / "n.csv"
        p.write_text("a,b\n1,2\n", encoding="utf-8")
        assert ing.extract_text(str(p)) == "a,b\n1,2\n"

    def test_json_is_normalized(self, tmp_path):
        p = tmp_path / "n.json"
        p.write_text('{"b":1,"a":2}', encoding="utf-8")
        out = ing.extract_text(str(p))
        assert json.loads(out) == {"b": 1, "a": 2}    # valid + parseable
        assert "\n" in out                            # pretty-printed (indent=2)

    def test_json_invalid_falls_back_to_raw(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not valid", encoding="utf-8")
        assert ing.extract_text(str(p)) == "{not valid"

    def test_unicode_preserved(self, tmp_path):
        p = tmp_path / "ko.txt"
        p.write_text("쿠팡 가격 — 화면공유", encoding="utf-8")
        assert ing.extract_text(str(p)) == "쿠팡 가격 — 화면공유"
