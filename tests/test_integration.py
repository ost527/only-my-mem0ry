"""Integration tests exercising the real server on a THROWAWAY store.

These import mem0_mcp_server, which constructs the embedder + Chroma. They are
skipped automatically wherever mem0 / chromadb / sentence-transformers are not
installed (e.g. a CI job that installs only the dev tools), so the pure unit
tests still run there. The server is pointed at a temp store BEFORE import, so
the real ~/.mem0-mcp store and its single-writer lock are never touched.
"""
import os
import re
import shutil
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
