"""Unit tests for the pure retrieval primitives (no embedder / Chroma needed)."""
from mem0_retrieval import (
    tokenize, bm25_rank, rrf_merge, fuse_rescue, cluster_by_pairs,
    rerank_with_bias, is_conflict_pair,
)


def _corpus(*pairs):
    return [{"id": i, "memory": m} for i, m in pairs]


class TestTokenize:
    def test_lowercases_and_splits_identifiers(self):
        assert tokenize("COUPANG_SEARCH_RESULT") == ["coupang", "search", "result"]

    def test_splits_ip_into_octets(self):
        assert tokenize("168.107.21.193") == ["168", "107", "21", "193"]

    def test_splits_path_on_punctuation(self):
        assert tokenize("~/.ssh/oracle/oracle-32min.key") == [
            "ssh", "oracle", "oracle", "32min", "key"]

    def test_keeps_non_ascii_runs_as_tokens(self):
        assert tokenize("쿠팡 가격") == ["쿠팡", "가격"]

    def test_camelcase_stays_one_token(self):
        assert tokenize("crawlSearchResultCard") == ["crawlsearchresultcard"]

    def test_empty_and_none(self):
        assert tokenize("") == []
        assert tokenize(None) == []


class TestBM25:
    def test_exact_identifier_ranks_its_doc_first(self):
        corpus = _corpus(
            ("a", "Oracle A1 worker SSH key is at ~/.ssh/oracle/oracle-32min.key"),
            ("b", "mac-worker-1 uses the SSH key at ~/.ssh/id_access"),
            ("c", "The staging server is reachable at 10.0.0.42"),
        )
        ranked = bm25_rank("oracle-32min.key", corpus, limit=5)
        assert ranked and ranked[0]["id"] == "a"

    def test_empty_query_returns_empty(self):
        assert bm25_rank("", _corpus(("a", "hello")), 5) == []

    def test_no_term_overlap_returns_empty(self):
        assert bm25_rank("zzz", _corpus(("a", "hello world")), 5) == []

    def test_respects_limit(self):
        corpus = _corpus(("a", "alpha token"), ("b", "alpha thing"), ("c", "alpha stuff"))
        assert len(bm25_rank("alpha", corpus, limit=2)) == 2

    def test_max_docs_caps_the_scan(self):
        # the matching doc lives beyond the cap, so it is never scanned.
        corpus = _corpus(("a", "nomatch here"), ("b", "target token"))
        assert bm25_rank("target", corpus, limit=5, max_docs=1) == []

    def test_only_positive_scores_returned(self):
        ranked = bm25_rank("alpha", _corpus(("a", "alpha"), ("b", "beta")), 5)
        assert [r["id"] for r in ranked] == ["a"]


class TestRRFMerge:
    def test_combines_dedups_and_rewards_high_in_both(self):
        l1 = _corpus(("a", "A"), ("b", "B"))
        l2 = _corpus(("b", "B"), ("c", "C"))
        out = rrf_merge([l1, l2], limit=10)
        ids = [r["id"] for r in out]
        assert set(ids) == {"a", "b", "c"}
        assert ids[0] == "b"  # appears in both lists -> highest fused score

    def test_respects_limit(self):
        l1 = _corpus(("a", "A"), ("b", "B"), ("c", "C"))
        assert len(rrf_merge([l1], limit=2)) == 2

    def test_skips_none_ids(self):
        out = rrf_merge([[{"id": None, "memory": "x"}, {"id": "a", "memory": "A"}]], 10)
        assert [r["id"] for r in out] == ["a"]


class TestFuseRescue:
    def test_preserves_dense_order(self):
        dense = _corpus(("d1", "x"), ("d2", "y"))
        lexical = _corpus(("d2", "y"), ("d1", "x"))  # reversed on purpose
        out = fuse_rescue(dense, lexical, limit=10)
        assert [r["id"] for r in out] == ["d1", "d2"]  # dense order is the backbone

    def test_appends_lexical_only_hits(self):
        dense = _corpus(("d1", "x"))
        lexical = _corpus(("d1", "x"), ("L", "lexonly"))
        out = fuse_rescue(dense, lexical, 10)
        assert [r["id"] for r in out] == ["d1", "L"]

    def test_respects_limit(self):
        dense = _corpus(("d1", "x"), ("d2", "y"))
        lexical = _corpus(("L", "z"))
        assert [r["id"] for r in fuse_rescue(dense, lexical, limit=2)] == ["d1", "d2"]

    def test_dedups_overlap(self):
        out = fuse_rescue(_corpus(("d1", "x")), _corpus(("d1", "x")), 10)
        assert len(out) == 1


class TestClusterByPairs:
    def test_transitive_merge(self):
        cl = cluster_by_pairs([("a", "b"), ("b", "c"), ("d", "e")])
        assert [set(c) for c in cl] == [{"a", "b", "c"}, {"d", "e"}]  # size desc

    def test_empty(self):
        assert cluster_by_pairs([]) == []

    def test_dedup_repeated_pairs(self):
        assert cluster_by_pairs([("a", "b"), ("a", "b")]) == [["a", "b"]]

    def test_sorted_within_cluster_and_by_size(self):
        cl = cluster_by_pairs([("y", "x"), ("z", "y"), ("n", "m")])
        assert cl[0] == ["x", "y", "z"]   # size 3, sorted ids
        assert cl[1] == ["m", "n"]

    def test_singletons_never_appear(self):
        # 'solo' is never in a pair -> excluded
        cl = cluster_by_pairs([("a", "b")])
        assert all("solo" not in c for c in cl) and cl == [["a", "b"]]


class TestRerankWithBias:
    def _r(self, *ids):
        return [{"id": i, "memory": i} for i in ids]

    def test_zero_weight_is_noop(self):
        results = self._r("a", "b", "c")
        out = rerank_with_bias(results, {"a": 0.0, "b": 1.0, "c": 0.5}, 0)
        assert [r["id"] for r in out] == ["a", "b", "c"]

    def test_small_weight_only_breaks_near_ties_not_clear_ranking(self):
        # rank gaps are 1; weight < 1 can never overtake a clearly-higher item.
        results = self._r("a", "b", "c")          # a is rank-1 (best)
        out = rerank_with_bias(results, {"a": 0.0, "b": 1.0, "c": 1.0}, 0.5)
        assert [r["id"] for r in out] == ["a", "b", "c"]   # order preserved

    def test_large_weight_can_reorder_by_bias(self):
        results = self._r("a", "b", "c")
        out = rerank_with_bias(results, {"a": 0.0, "b": 0.0, "c": 1.0}, 5)
        assert out[0]["id"] == "c"                 # strong bias promotes c

    def test_stable_for_equal_scores(self):
        results = self._r("a", "b", "c")
        out = rerank_with_bias(results, {"a": 0.0, "b": 0.0, "c": 0.0}, 1)
        assert [r["id"] for r in out] == ["a", "b", "c"]   # original order kept

    def test_empty_results(self):
        assert rerank_with_bias([], {}, 5) == []


class TestIsConflictPair:
    def test_differing_numbers_conflict(self):
        assert is_conflict_pair("the db listens on port 5432",
                                "the db listens on port 5433")

    def test_differing_weekdays_conflict(self):
        assert is_conflict_pair("we deploy on Friday", "we deploy on Monday")

    def test_antonym_flip_conflict(self):
        assert is_conflict_pair("feature X is enabled", "feature X is disabled")

    def test_negation_asymmetry_conflict(self):
        assert is_conflict_pair("the cache is cleared on boot",
                                "the cache is not cleared on boot")

    def test_identical_text_is_not_a_conflict(self):
        assert not is_conflict_pair("port is 5432", "port is 5432")

    def test_unrelated_text_is_not_a_conflict(self):
        assert not is_conflict_pair("the db listens on port 5432",
                                    "the office cat naps by the window")

    def test_same_subject_same_number_not_conflict(self):
        # high overlap but they AGREE -> not a conflict candidate
        assert not is_conflict_pair("the db listens on port 5432",
                                    "the db also listens on port 5432")

    def test_empty_inputs(self):
        assert not is_conflict_pair("", "anything")
        assert not is_conflict_pair("anything", "")
