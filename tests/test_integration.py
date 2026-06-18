"""Integration tests exercising the real server on a THROWAWAY store.

These import mem0_mcp_server, which constructs the embedder + Chroma. They are
skipped automatically wherever mem0 / chromadb / sentence-transformers are not
installed (e.g. a CI job that installs only the dev tools), so the pure unit
tests still run there. The server is pointed at a temp store BEFORE import, so
the real ~/.only-my-mem0ry store and its single-writer lock are never touched.
"""
import os
import re
import json
import shutil
import datetime
import tempfile

import pytest


@pytest.fixture(scope="module")
def srv():
    pytest.importorskip("mem0")
    pytest.importorskip("chromadb")
    pytest.importorskip("sentence_transformers")
    tmp = tempfile.mkdtemp(prefix="mem0-test-")
    os.environ["MEM0_CHROMA_PATH"] = os.path.join(tmp, "chroma")
    os.environ["MEM0_IDLE_TIMEOUT"] = "0"
    import mem0_mcp_server as s
    try:
        yield s
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _new_id(add_output: str) -> str:
    m = re.search(r"id: ([0-9a-fA-F-]+)\)", add_output)
    assert m, f"could not parse id from: {add_output!r}"
    return m.group(1)


def test_add_then_search_exact_identifier(srv):
    uid = "test_user_a"
    out = srv.add_memory("The deploy key is at ~/.ssh/deploy_ed25519", user_id=uid)
    assert "Stored" in out
    res = srv._semantic_search("deploy_ed25519", uid, 5)
    assert any("deploy_ed25519" in r["memory"] for r in res)


def test_search_is_isolated_by_user(srv):
    srv.add_memory("staging server is at 10.0.0.42", user_id="userX")
    res = srv._semantic_search("10.0.0.42", "userY", 5)
    assert all("10.0.0.42" not in r["memory"] for r in res)


def test_pin_writes_core_file_and_unpin_clears(srv):
    uid = "test_pin"
    mid = _new_id(srv.add_memory("PROJECT root is /Users/x/proj", user_id=uid))
    assert "Pinned to core" in srv.pin_memory(mid)
    assert any(it["id"] == mid for it in srv._core_items(srv._load_meta()))
    with open(srv.CORE_FILE, encoding="utf-8") as f:
        assert "/Users/x/proj" in f.read()
    assert "Unpinned" in srv.unpin_memory(mid)
    assert all(it["id"] != mid for it in srv._core_items(srv._load_meta()))


def test_core_budget_rejects_oversized_pin(srv, monkeypatch):
    mid = _new_id(srv.add_memory("x" * 50, user_id="test_budget"))
    monkeypatch.setattr(srv, "CORE_BUDGET", 10)
    assert "budget exceeded" in srv.pin_memory(mid).lower()


def test_update_then_delete_roundtrip(srv):
    uid = "test_upd"
    mid = _new_id(srv.add_memory("temporary fact about FOO_VAR", user_id=uid))
    assert "Updated" in srv.update_memory(mid, "FOO_VAR is now set to 1")
    res = srv._semantic_search("FOO_VAR", uid, 5)
    assert any("set to 1" in r["memory"] for r in res)
    assert "Deleted" in srv.delete_memory(mid)


def test_tag_scope_filters_search(srv):
    uid = "test_tags"
    srv.add_memory("uses Redis for caching", user_id=uid, tags="proj-alpha, cache")
    srv.add_memory("uses Postgres as the main database", user_id=uid, tags="proj-beta")
    scoped = srv.search_memories("uses", user_id=uid, tags="proj-alpha")
    assert "Redis" in scoped and "Postgres" not in scoped
    both = srv.search_memories("uses", user_id=uid)
    assert "Redis" in both and "Postgres" in both


def test_tag_memory_set_and_clear(srv):
    uid = "test_tagtool"
    mid = _new_id(srv.add_memory("ephemeral note about X_TOKEN", user_id=uid))
    assert "Tagged" in srv.tag_memory(mid, "alpha beta")
    assert "X_TOKEN" in srv.search_memories("X_TOKEN", user_id=uid, tags="alpha")
    assert "Cleared" in srv.tag_memory(mid, "")
    assert "No results" in srv.search_memories("X_TOKEN", user_id=uid, tags="alpha")


def test_delete_removes_tags(srv):
    uid = "test_deltags"
    mid = _new_id(srv.add_memory("temp tagged fact", user_id=uid, tags="zzz"))
    assert mid in srv._load_meta().get("tags", {})
    srv.delete_memory(mid)
    assert mid not in srv._load_meta().get("tags", {})


def test_add_with_type_and_type_scoped_search(srv):
    uid = "test_type_filter"
    srv.add_memory("We deploy on Fridays only.", user_id=uid, mem_type="decision")
    srv.add_memory("The user likes terse replies.", user_id=uid, mem_type="preference")
    dec = srv.search_memories("deploy replies", user_id=uid, mem_type="decision")
    assert "Fridays" in dec and "terse" not in dec
    assert "[decision]" in dec               # the type label is rendered
    pref = srv.search_memories("deploy replies", user_id=uid, mem_type="preference")
    assert "terse" in pref and "Fridays" not in pref


def test_set_memory_type_set_and_clear(srv):
    uid = "test_settype"
    mid = _new_id(srv.add_memory("note about ENV_FLAG handling", user_id=uid))
    assert "Set type" in srv.set_memory_type(mid, "instruction")
    assert srv._load_meta()["types"].get(mid) == "instruction"
    assert "ENV_FLAG" in srv.search_memories("ENV_FLAG", user_id=uid, mem_type="instruction")
    assert "Cleared" in srv.set_memory_type(mid, "")
    assert mid not in srv._load_meta()["types"]
    assert "No results" in srv.search_memories("ENV_FLAG", user_id=uid, mem_type="instruction")


def test_add_memory_is_lenient_on_unknown_type(srv):
    uid = "test_badtype_add"
    out = srv.add_memory("a fact with a bogus type", user_id=uid, mem_type="bogus")
    assert "Stored" in out and "Ignored unknown type" in out
    mid = _new_id(out)
    assert mid not in srv._load_meta()["types"]   # stored, but WITHOUT a type


def test_set_memory_type_rejects_unknown_type(srv):
    uid = "test_badtype_set"
    mid = _new_id(srv.add_memory("another plain fact", user_id=uid))
    assert "Unknown memory type" in srv.set_memory_type(mid, "nonsense")
    assert mid not in srv._load_meta()["types"]


def test_search_rejects_unknown_type_filter(srv):
    uid = "test_badtype_search"
    srv.add_memory("some searchable fact", user_id=uid)
    assert "Unknown memory type" in srv.search_memories("fact", user_id=uid, mem_type="nope")


def test_delete_removes_type(srv):
    uid = "test_deltype"
    mid = _new_id(srv.add_memory("temp typed fact", user_id=uid, mem_type="event"))
    assert srv._load_meta()["types"].get(mid) == "event"
    srv.delete_memory(mid)
    assert mid not in srv._load_meta().get("types", {})


def test_search_combines_tag_and_type_filters(srv):
    uid = "test_tagtype"
    srv.add_memory("Alpha uses Redis for caching.", user_id=uid, tags="proj-a", mem_type="fact")
    srv.add_memory("Alpha decided to adopt Postgres.", user_id=uid, tags="proj-a", mem_type="decision")
    srv.add_memory("Beta uses Redis for caching.", user_id=uid, tags="proj-b", mem_type="fact")
    got = srv.search_memories("the project setup", user_id=uid, tags="proj-a", mem_type="fact")
    assert "Alpha uses Redis" in got      # matches BOTH tag=proj-a AND type=fact
    assert "Postgres" not in got          # excluded by type filter (it is a decision)
    assert "Beta uses Redis" not in got   # excluded by tag filter (it is proj-b)


def test_answer_grounds_in_retrieved_memory(srv):
    uid = "test_answer"
    srv.add_memory("The prod database listens on port 6543.", user_id=uid)
    out = srv._answer_context("which port does the prod database use?", uid=uid)
    assert "6543" in out                      # the relevant memory was retrieved
    assert "ONLY the memories" in out         # grounding instruction present
    assert "[id:" in out and "cite" in out.lower()


def test_answer_handles_no_results_without_guessing(srv):
    out = srv._answer_context("a totally unstored topic zzzq", uid="test_answer_empty")
    assert "guess" in out.lower()             # instructs the agent NOT to guess
    assert "6543" not in out


def test_add_with_provenance_and_origin_filter(srv):
    uid = "test_prov"
    srv.add_memory("We chose Postgres over MySQL.", user_id=uid, origin="explicit", source="kickoff call")
    srv.add_memory("Maybe the cache layer is Redis.", user_id=uid, origin="inferred")
    got = srv.search_memories("database cache layer", user_id=uid, origin="explicit")
    assert "Postgres" in got and "Maybe the cache" not in got   # inferred one filtered out
    assert "«explicit · kickoff call»" in got                   # provenance is rendered


def test_set_provenance_set_and_clear(srv):
    uid = "test_setprov"
    mid = _new_id(srv.add_memory("a fact about WIDGET_X behaviour", user_id=uid))
    assert "Set provenance" in srv.set_provenance(mid, "imported", "file:spec.md")
    assert srv._load_meta()["provenance"][mid] == {"origin": "imported", "source": "file:spec.md"}
    assert "WIDGET_X" in srv.search_memories("WIDGET_X", user_id=uid, origin="imported")
    assert "Cleared provenance" in srv.set_provenance(mid, "", "")
    assert mid not in srv._load_meta()["provenance"]


def test_add_memory_is_lenient_on_unknown_origin(srv):
    uid = "test_badorigin_add"
    out = srv.add_memory("a fact with a bogus origin", user_id=uid, origin="guessed")
    assert "Stored" in out and "Ignored unknown origin" in out
    assert _new_id(out) not in srv._load_meta()["provenance"]   # stored, but WITHOUT provenance


def test_set_provenance_rejects_unknown_origin(srv):
    uid = "test_badorigin_set"
    mid = _new_id(srv.add_memory("a plain fact for origin reject", user_id=uid))
    assert "Unknown origin" in srv.set_provenance(mid, "nonsense")
    assert mid not in srv._load_meta()["provenance"]


def test_delete_removes_provenance(srv):
    uid = "test_delprov"
    mid = _new_id(srv.add_memory("temp fact with provenance", user_id=uid, origin="explicit", source="x"))
    assert mid in srv._load_meta()["provenance"]
    srv.delete_memory(mid)
    assert mid not in srv._load_meta().get("provenance", {})


def test_update_resyncs_core_file_for_pinned(srv):
    uid = "test_updcore"
    mid = _new_id(srv.add_memory("ORIGINAL fact about PORT 1234", user_id=uid))
    srv.pin_memory(mid)
    srv.update_memory(mid, "UPDATED fact about PORT 5678")
    with open(srv.CORE_FILE, encoding="utf-8") as f:
        body = f.read()
    assert "UPDATED fact about PORT 5678" in body
    assert "ORIGINAL fact about PORT 1234" not in body
    srv.unpin_memory(mid)


def test_add_memory_warns_on_near_duplicate(srv):
    uid = "test_dupwarn"
    srv.add_memory("The staging server is reachable at 10.9.9.9 on port 5432.", user_id=uid)
    out = srv.add_memory("The staging server is reachable at 10.9.9.9 on port 5432.", user_id=uid)
    assert "LIKELY DUPLICATE" in out


def test_add_memory_quiet_for_distinct(srv):
    uid = "test_nodup"
    srv.add_memory("Project Alpha is written in Rust and ships on Fridays.", user_id=uid)
    out = srv.add_memory("Unrelated note: the coffee machine sits on the third floor.", user_id=uid)
    assert "LIKELY DUPLICATE" not in out


def test_duplicate_clusters_groups_near_identical(srv):
    uid = "test_dupclust"
    a = _new_id(srv.add_memory("The nightly backup runs at 2am and uploads to the NAS.", user_id=uid))
    b = _new_id(srv.add_memory("The nightly backup runs at 2 a.m. and uploads to the NAS.", user_id=uid))
    c = _new_id(srv.add_memory("The office cat Mochi naps every afternoon by the window.", user_id=uid))
    clusters = srv._duplicate_clusters(uid, srv._DUP_THRESHOLD, srv._DUP_MAX_DOCS)
    cluster_ids = [{it["id"] for it in g} for g in clusters]
    assert any({a, b} <= cids for cids in cluster_ids), "near-identical memories should cluster"
    assert all(c not in cids for cids in cluster_ids), "unrelated memory should not cluster"


# ---- confidence (Phase 1) ----------------------------------------------------

def test_add_with_confidence_and_min_confidence_filter(srv):
    uid = "test_conf"
    srv.add_memory("The prod DB is definitely Postgres.", user_id=uid, confidence="high")
    srv.add_memory("The cache might be Redis.", user_id=uid, confidence="low")
    got = srv.search_memories("database cache layer", user_id=uid, min_confidence="medium")
    assert "Postgres" in got and "might be Redis" not in got   # low filtered out
    assert "(conf: high)" in got                               # confidence rendered


def test_min_confidence_excludes_unrated(srv):
    uid = "test_conf_unrated"
    srv.add_memory("Unrated fact about THINGY_TOKEN.", user_id=uid)   # no confidence
    got = srv.search_memories("THINGY_TOKEN", user_id=uid, min_confidence="low")
    assert "No results" in got                                  # unrated excluded by min filter


def test_set_confidence_set_and_clear(srv):
    uid = "test_setconf"
    mid = _new_id(srv.add_memory("a fact about CONF_VAR handling", user_id=uid))
    assert "Set confidence" in srv.set_confidence(mid, "high")
    assert srv._load_meta()["confidence"][mid] == "high"
    assert "CONF_VAR" in srv.search_memories("CONF_VAR", user_id=uid, min_confidence="high")
    assert "Cleared" in srv.set_confidence(mid, "")
    assert mid not in srv._load_meta()["confidence"]


def test_add_memory_lenient_on_unknown_confidence(srv):
    uid = "test_badconf"
    out = srv.add_memory("a fact with a bogus confidence", user_id=uid, confidence="certain")
    assert "Stored" in out and "Ignored unknown confidence" in out
    assert _new_id(out) not in srv._load_meta()["confidence"]


def test_search_rejects_unknown_min_confidence(srv):
    uid = "test_badconf_search"
    srv.add_memory("searchable conf fact", user_id=uid)
    assert "Unknown confidence" in srv.search_memories("fact", user_id=uid, min_confidence="certain")


def test_set_confidence_rejects_unknown(srv):
    uid = "test_badconf_set"
    mid = _new_id(srv.add_memory("plain fact for conf reject", user_id=uid))
    assert "Unknown confidence" in srv.set_confidence(mid, "0.9")
    assert mid not in srv._load_meta()["confidence"]


def test_delete_removes_confidence(srv):
    uid = "test_delconf"
    mid = _new_id(srv.add_memory("temp conf fact", user_id=uid, confidence="medium"))
    assert mid in srv._load_meta()["confidence"]
    srv.delete_memory(mid)
    assert mid not in srv._load_meta().get("confidence", {})


# ---- temporal filters (Phase 1) ----------------------------------------------

def test_temporal_since_until_changed_since(srv):
    uid = "test_temporal"
    srv.add_memory("TEMPORAL_TOKEN fact created today", user_id=uid)
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert "TEMPORAL_TOKEN" in srv.search_memories("TEMPORAL_TOKEN", user_id=uid, since=today)
    assert "TEMPORAL_TOKEN" in srv.search_memories("TEMPORAL_TOKEN", user_id=uid, until=today)
    assert "No results" in srv.search_memories("TEMPORAL_TOKEN", user_id=uid, since=tomorrow)
    assert "No results" in srv.search_memories("TEMPORAL_TOKEN", user_id=uid, until=yesterday)
    assert "TEMPORAL_TOKEN" in srv.search_memories("TEMPORAL_TOKEN", user_id=uid, changed_since=today)
    assert "No results" in srv.search_memories("TEMPORAL_TOKEN", user_id=uid, changed_since=tomorrow)


def test_search_rejects_bad_date(srv):
    uid = "test_baddate"
    srv.add_memory("date fact", user_id=uid)
    assert "Invalid since date" in srv.search_memories("fact", user_id=uid, since="2026/01/01")


def test_list_temporal_filter(srv):
    uid = "test_listtemporal"
    srv.add_memory("LISTTOKEN created today", user_id=uid)
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert "LISTTOKEN" in srv.list_memories(user_id=uid, since=today)
    assert "LISTTOKEN" not in srv.list_memories(user_id=uid, since=tomorrow)


# ---- versioning / history (Phase 2) ------------------------------------------

def test_update_archives_history(srv):
    uid = "test_hist"
    mid = _new_id(srv.add_memory("VERSION one payload", user_id=uid))
    srv.update_memory(mid, "VERSION two payload")
    srv.update_memory(mid, "VERSION three payload")
    texts = [h["text"] for h in srv._load_meta()["history"].get(mid) or []]
    assert "VERSION one payload" in texts and "VERSION two payload" in texts
    out = srv.memory_history(mid)
    assert "CURRENT: VERSION three payload" in out and "VERSION one payload" in out


def test_history_depth_cap(srv, monkeypatch):
    uid = "test_histcap"
    monkeypatch.setattr(srv, "_HISTORY_DEPTH", 2)
    mid = _new_id(srv.add_memory("rev0", user_id=uid))
    for i in range(1, 6):
        srv.update_memory(mid, f"rev{i}")
    hist = srv._load_meta()["history"].get(mid) or []
    assert len(hist) == 2                       # capped at MEM0_HISTORY_DEPTH
    assert hist[-1]["text"] == "rev4"           # most recent prior version kept


def test_restore_existing_memory(srv):
    uid = "test_restore"
    mid = _new_id(srv.add_memory("ORIG payload value", user_id=uid))
    srv.update_memory(mid, "CHANGED payload value")
    assert "Restored" in srv.restore_memory(mid, 1)
    assert srv._memory_text(mid) == "ORIG payload value"


def test_restore_deleted_memory_readds_as_new_id(srv):
    uid = "test_restoredel"
    mid = _new_id(srv.add_memory("DELETED_TOKEN content here", user_id=uid))
    srv.delete_memory(mid)
    assert srv._memory_text(mid) is None
    out = srv.restore_memory(mid, 1)
    assert "re-added" in out
    new_id = _new_id(out)
    assert new_id != mid
    assert srv._memory_text(new_id) == "DELETED_TOKEN content here"


def test_memory_history_unknown_id(srv):
    assert "No memory or history" in srv.memory_history("nonexistent-id-xyz")


# ---- conflict candidates (Phase 3) -------------------------------------------

def test_conflict_candidates_flags_disagreement(srv):
    uid = "test_conflict"
    a = _new_id(srv.add_memory("The prod database listens on port 7001.", user_id=uid))
    b = _new_id(srv.add_memory("The prod database listens on port 7002.", user_id=uid))
    srv.add_memory("The office cat naps by the window all afternoon.", user_id=uid)
    # Force the cosine band wide so EVERY pair is a candidate; the lexical heuristic
    # then decides -- deterministic regardless of the actual embedding sims.
    cands = srv._conflict_candidates(uid, -1.0, 2.0, srv._DUP_MAX_DOCS)
    pairs = {frozenset((c["a"]["id"], c["b"]["id"])) for c in cands}
    assert frozenset((a, b)) in pairs                       # differing port -> conflict
    assert all("cat" not in c["a"]["memory"] and "cat" not in c["b"]["memory"] for c in cands)


# ---- batch add (Phase 4) -----------------------------------------------------

def test_add_memories_batch(srv):
    uid = "test_batch"
    items = json.dumps([
        {"text": "Batch fact ALPHA uses Redis", "tags": "proj-x", "mem_type": "fact"},
        {"text": "Batch decision BETA deploy Fridays", "mem_type": "decision", "confidence": "high"},
        {"text": ""},   # skipped (no text)
    ])
    out = srv.add_memories(items, user_id=uid)
    assert "Stored 2/3" in out and "skipped" in out
    assert "ALPHA" in srv.search_memories("ALPHA", user_id=uid, tags="proj-x")
    assert "BETA" in srv.search_memories("BETA", user_id=uid, mem_type="decision", min_confidence="high")


def test_add_memories_rejects_bad_input(srv):
    assert "not valid JSON" in srv.add_memories("{not json", user_id="test_batchbad")
    assert "non-empty JSON array" in srv.add_memories("{}", user_id="test_batchbad")


# ---- recency tie-break (Phase 3) is OFF by default ---------------------------

def test_recency_bias_off_by_default_is_noop(srv):
    uid = "test_recency"
    for i in range(5):
        srv.add_memory(f"recency item {i} about WIDGETS_TOKEN", user_id=uid)
    res = srv._semantic_search("WIDGETS_TOKEN", uid, 5)
    # at the default weights (0), the optional bias leaves the ranking untouched
    assert srv._apply_optional_bias(list(res), uid) == res


# ---- file ingest (Phase 4) ---------------------------------------------------

def test_ingest_file_writes_tagged_imported_memories(srv, tmp_path):
    import ingest_file as ing
    p = tmp_path / "doc.txt"
    p.write_text("First INGESTTOKEN paragraph.\n\nSecond paragraph goes here.", encoding="utf-8")
    res = ing.ingest(str(p), user="test_ingest", target_chars=40, overlap=5)
    assert res["chunks"] >= 1 and len(res["ids"]) == res["chunks"]
    got = srv.search_memories("INGESTTOKEN", user_id="test_ingest", origin="imported")
    assert "INGESTTOKEN" in got
    assert "#doc" in got and "«imported" in got      # filename tag + imported provenance
