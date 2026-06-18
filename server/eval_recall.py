#!/usr/bin/env python3
"""recall@k evaluation harness for only-my-mem0ry retrieval.

Builds a fixed, labeled corpus in a THROWAWAY Chroma store and compares retrieval
strategies (dense-only vs hybrid dense+BM25) on labeled queries, printing hit@k
and MRR. Use it to (a) prove hybrid >= dense, and (b) compare embedders: run with
a different MEM0_EMBEDDER_MODEL to see which serves your data best.

Safe + self-contained: it uses its OWN temp store (never your real
~/.only-my-mem0ry/chroma) and does not touch the running backend or take any lock.

USAGE:
    .venv/bin/python server/eval_recall.py
    MEM0_EMBEDDER_MODEL=intfloat/multilingual-e5-small \\
        MEM0_EMBEDDER_DIMS=384 .venv/bin/python server/eval_recall.py
"""
import os
import sys
import shutil
import tempfile

# Isolate BEFORE importing the server: fresh temp store, no idle watchdog. Setting
# MEM0_CHROMA_PATH here means the module-level Memory() opens the temp store, so the
# real store is never touched and no single-writer lock is taken (import != __main__).
_TMP = tempfile.mkdtemp(prefix="mem0-eval-")
os.environ["MEM0_CHROMA_PATH"] = os.path.join(_TMP, "chroma")
os.environ.setdefault("MEM0_IDLE_TIMEOUT", "0")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mem0_mcp_server as srv  # noqa: E402

EVAL_UID = "eval_user"

# label -> memory text. Deliberately identifier-heavy (paths, env vars, IPs,
# function names) with several *near-duplicate distractors* per topic (4 SSH keys,
# 4 env vars, multiple IPs/crawler notes), plus paraphrase-friendly and bilingual
# (KO/EN) entries. The distractors are the point: when many docs share a topic,
# only the exact token disambiguates -- which is where dense-only retrieval slips.
CORPUS = {
    # SSH keys (exact key/host token must disambiguate)
    "oracle_key": "Oracle A1 worker SSH key is at ~/.ssh/oracle/oracle-32min.key, server IP 168.107.21.193",
    "mac_key": "mac-worker-1 uses the SSH key at ~/.ssh/id_access",
    "backup_key": "backup-worker connects with the SSH key at ~/.ssh/id_backup",
    "deploy_key": "CI deploy uses the SSH key at ~/.ssh/deploy_ed25519",
    # IPs / hosts (exact number must disambiguate)
    "staging_ip": "The staging server is reachable at 10.0.0.42",
    "db_ip": "The Postgres database listens on 192.168.1.50 port 5432",
    # env vars (exact name must disambiguate)
    "coupang_env": "Added COUPANG_SEARCH_RESULT_PRICE_ENABLED=1 and COUPANG_SEARCH_RESULT_FALLBACK_TO_DETAIL=0 to the .env file",
    "env_headless": "Set COUPANG_HEADLESS=1 in the .env to run without a visible window",
    "env_pages": "COUPANG_MAX_PAGES=5 limits how many result pages the crawler scans",
    "env_proxy": "PROXY_ROTATION_ENABLED=1 turns on proxy rotation in the .env",
    # crawler behavior / function names
    "crawler_cdp": "Oracle A1 Coupang crawler scans search-result cards via crawlSearchResultCard CDP code without opening detail pages",
    "crawler_retry": "The Coupang crawler retries a failed request up to 3 times before skipping it",
    "crawler_log": "The Coupang crawler writes its progress to crawler.log",
    "select_worker": "selectCoupangWorker runs the status action with silentBusy=true on first lookup",
    # VNC / screen sharing
    "vnc_tunnel": "Connect to Oracle A1 via VNC over an SSH tunnel; the VNC server binds localhost:5900 with password in ~/.vnc/passwd",
    "x11vnc": "x11vnc is not a systemd service, so restart it manually if it crashes",
    "screen_share_app": "The macOS Screen Sharing app has a saved connection to localhost:5900 for Oracle A1",
    # build / config / runtime
    "packaging": "Build the Threads Auto Ad.app with npm run dist (electron-packager, asar disabled); rebuild after source changes",
    "config_order": "coupang-worker-controller loads config from cwd/coupang-worker-controller.config.json first, then the spare-mac-crawler-controller path, then the app Resources config.json",
    "renderer": "The renderer is independent of workers and fills dashboard fields with status and log commands",
    "xvfb": "Oracle A1 runs headless Chrome under Xvfb :99 on Ubuntu",
    "server_design": "only-my-mem0ry is a single shared HTTP backend on 127.0.0.1:8765 started on demand by a per-client stdio proxy",
    "worker_list": "The three workers are local-mac-worker, mac-worker-1, and oracle-worker-1",
    "oracle_control": "oracle-worker-control.sh has macOS-only commands that fail on Ubuntu (pmset not found)",
    # bilingual (KO)
    "ko_launcher": "오라클 A1 화면공유.command 런처가 SSH 터널을 열고 VNC localhost:5900 에 접속한다",
    "ko_crawler": "쿠팡 크롤러는 상세 페이지에 들어가지 않고 검색 결과 카드에서 가격을 수집한다",
    # semantic Korean: queries will paraphrase these with little/no shared tokens,
    # so BM25 can't rescue them -- only a Korean-capable embedder retrieves them.
    "ko_meeting": "팀 주간 회의는 매주 월요일 오전 10시에 진행한다",
    "ko_db_pw": "프로덕션 데이터베이스 접속 정보는 1Password 'prod-db' 항목에 보관한다",
    "ko_deadline": "이번 분기 마감일은 9월 말까지이며 그 전에 배포를 끝내야 한다",
    # cross-lingual: stored in English, asked in Korean (and vice versa). No shared
    # tokens across scripts, so only a multilingual embedder bridges the gap.
    "en_backup": "The nightly backup job runs at 2am and uploads snapshots to the NAS",
    "en_oncall": "The on-call engineer carries the pager for one week at a time",
}

# query -> set of acceptable labels (a "hit" if any acceptable label is retrieved).
QUERIES = [
    ("oracle-32min.key path", {"oracle_key"}),
    ("168.107.21.193", {"oracle_key"}),
    ("ssh key for backup-worker", {"backup_key"}),
    ("which SSH key does mac-worker-1 use", {"mac_key"}),
    ("COUPANG_SEARCH_RESULT_PRICE_ENABLED", {"coupang_env"}),
    ("COUPANG_MAX_PAGES", {"env_pages"}),
    ("crawlSearchResultCard", {"crawler_cdp"}),
    ("how many times does the crawler retry", {"crawler_retry"}),
    ("where does the crawler write its log", {"crawler_log"}),
    ("how do I open the remote desktop screen", {"vnc_tunnel", "ko_launcher", "screen_share_app"}),
    ("run headless chrome display", {"xvfb"}),
    ("how to package the electron app", {"packaging"}),
    ("staging server address", {"staging_ip"}),
    ("10.0.0.42", {"staging_ip"}),
    ("쿠팡 가격 수집 방식", {"ko_crawler", "crawler_cdp"}),
    ("화면공유 런처", {"ko_launcher"}),
    # semantic Korean (paraphrase, minimal shared tokens -> BM25 can't help)
    ("팀 미팅 스케줄", {"ko_meeting"}),
    ("디비 암호 보관 위치", {"ko_db_pw"}),
    ("분기 기한이 언제까지", {"ko_deadline"}),
    # cross-lingual (KO query -> EN fact, EN query -> KO fact)
    ("야간 백업 몇 시에 도나", {"en_backup"}),
    ("who is holding the pager this week", {"en_oncall"}),
    ("when is the weekly team meeting", {"ko_meeting"}),
]

K_VALUES = (1, 3, 5)
COLS = [f"hit@{k}" for k in K_VALUES] + ["MRR"]


def seed():
    label_by_id = {}
    for label, text in CORPUS.items():
        added = srv._results(srv.m.add(text, user_id=EVAL_UID, infer=False))
        mid = added[0]["id"] if added else None
        label_by_id[mid] = label
    return label_by_id


def evaluate(search_fn, label_by_id):
    maxk = max(K_VALUES)
    hits = {k: 0 for k in K_VALUES}
    rr_sum = 0.0
    per_query = {}
    for q, acceptable in QUERIES:
        results = search_fn(q, EVAL_UID, maxk)
        ranked = [label_by_id.get(r.get("id")) for r in results]
        first = next((i for i, lab in enumerate(ranked, start=1) if lab in acceptable), None)
        per_query[q] = first
        if first:
            rr_sum += 1.0 / first
            for k in K_VALUES:
                if first <= k:
                    hits[k] += 1
    n = len(QUERIES)
    out = {f"hit@{k}": hits[k] / n for k in K_VALUES}
    out["MRR"] = rr_sum / n
    return out, per_query


def main():
    model = os.environ.get("MEM0_EMBEDDER_MODEL", "intfloat/multilingual-e5-small")
    label_by_id = seed()
    print(f"embedder : {model}")
    print(f"corpus   : {len(CORPUS)} memories | queries: {len(QUERIES)} | MRR over top-{max(K_VALUES)}\n")
    header = f"{'strategy':10}" + "".join(f"{c:>9}" for c in COLS)
    print(header)
    print("-" * len(header))
    metrics, per_q = {}, {}
    for name, fn in (("dense", srv._dense_search), ("hybrid", srv._semantic_search)):
        res, pq = evaluate(fn, label_by_id)
        metrics[name], per_q[name] = res, pq
        print(f"{name:10}" + "".join(f"{res[c]:>9.3f}" for c in COLS))
    print("\ndelta (hybrid - dense):")
    print("  " + "  ".join(f"{c} {metrics['hybrid'][c] - metrics['dense'][c]:+.3f}" for c in COLS))

    if os.environ.get("EVAL_VERBOSE"):
        print("\nper-query first-hit rank (lower=better, '-'=miss):")
        print(f"  {'query':38}{'dense':>6}{'hybrid':>8}")
        for q, _ in QUERIES:
            d, h = per_q["dense"][q], per_q["hybrid"][q]
            mark = ""
            if d and (not h or h > d):
                mark = "   <-- REGRESS"
            elif h and (not d or h < d):
                mark = "   <-- improve"
            print(f"  {q[:38]:38}{str(d or '-'):>6}{str(h or '-'):>8}{mark}")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
