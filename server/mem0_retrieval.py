"""Pure, local, deterministic retrieval primitives: tokenizer + BM25 + fusion.

Extracted from the server so they can be unit-tested in isolation -- this module
imports ONLY the stdlib (no mem0, no Chroma, no embedder), so importing it is
free and side-effect-free. The dense (vector) path stays in the server because it
needs the live embedding model + vector store; everything here operates on plain
[{"id", "memory", "score"?}] dicts.

Why hybrid retrieval: developer memories are full of exact identifiers -- file
paths, env-var names, IPs, function names (e.g. ~/.ssh/oracle/oracle-32min.key,
COUPANG_SEARCH_RESULT_PRICE_ENABLED, 168.107.21.193, crawlSearchResultCard). Pure
dense (semantic) retrieval often misses exact tokens; a lexical BM25 signal nails
them. We then fuse the two rankings.
"""
import re
import math

# Okapi BM25 defaults (server can override per call).
BM25_K1 = 1.5
BM25_B = 0.75
BM25_MAX_DOCS = 5000
# Reciprocal Rank Fusion constant.
RRF_K = 60


def tokenize(text: str):
    """ASCII identifier parts split on _-./ etc.: COUPANG_SEARCH_RESULT ->
    [coupang, search, result]; 168.107.21.193 -> [168, 107, 21, 193]. Non-ASCII
    runs (e.g. Korean 쿠팡, 화면공유) are kept as their own tokens so lexical
    search helps a bilingual store too. camelCase stays one token, matching a
    same-cased query token."""
    return re.findall(r"[a-z0-9]+|[^\x00-\x7f]+", (text or "").lower())


def bm25_rank(query: str, corpus: list, limit: int,
              k1: float = BM25_K1, b: float = BM25_B, max_docs: int = BM25_MAX_DOCS):
    """Rank corpus docs ([{id, memory}]) against the query with Okapi BM25.
    Returns ranked [{id, memory, score}], score > 0 only (docs sharing >=1 query
    term). Pure-Python, deterministic."""
    q_terms = tokenize(query)
    if not q_terms or not corpus:
        return []
    docs = corpus[:max_docs]
    tokenized = [tokenize(d.get("memory", "")) for d in docs]
    n_docs = len(docs)
    avgdl = (sum(len(t) for t in tokenized) / n_docs) if n_docs else 0.0
    df = {term: sum(1 for toks in tokenized if term in toks) for term in set(q_terms)}
    scored = []
    for d, toks in zip(docs, tokenized):
        if not toks:
            continue
        dl = len(toks)
        score = 0.0
        for term in q_terms:
            n_qi = df.get(term, 0)
            if n_qi == 0:
                continue
            f = toks.count(term)
            if f == 0:
                continue
            idf = math.log(1 + (n_docs - n_qi + 0.5) / (n_qi + 0.5))
            denom = f + k1 * (1 - b + b * dl / avgdl) if avgdl else f + k1
            score += idf * (f * (k1 + 1)) / denom
        if score > 0:
            scored.append({"id": d.get("id"), "memory": d.get("memory", ""), "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def rrf_merge(ranked_lists: list, limit: int, k: int = RRF_K):
    """Reciprocal Rank Fusion: combine ranked [{id, memory}] lists into one.
    fused(id) = sum over lists of 1/(k + rank), rank starting at 1. Robust to the
    different score scales of dense vs BM25; rewards items ranked high in either."""
    fused = {}
    text_by_id = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst, start=1):
            mid = item.get("id")
            if mid is None:
                continue
            fused[mid] = fused.get(mid, 0.0) + 1.0 / (k + rank)
            text_by_id.setdefault(mid, item.get("memory", ""))
    out = [{"id": mid, "memory": text_by_id.get(mid, ""), "score": s} for mid, s in fused.items()]
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:limit]


def fuse_rescue(dense: list, lexical: list, limit: int):
    """Dense-anchored fusion (default): keep dense's ranking as the backbone -- it
    never demotes a doc dense found -- then append lexical-only hits (exact matches
    the vector model missed entirely) in lexical order. Provably non-regressing vs
    dense: it can only ADD recall (rescue overlooked identifiers), never reorder
    dense's results. The visible payoff grows with store size, where dense starts
    dropping exact tokens out of top-k."""
    out, seen = [], set()
    for it in list(dense) + list(lexical):
        mid = it.get("id")
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        out.append(it)
    return out[:limit]


def cluster_by_pairs(pairs):
    """Group ids into connected components from (id_a, id_b) similarity pairs, via
    union-find. Returns clusters (each a sorted list of >=2 ids), ordered by
    descending size then first id. Pure + deterministic; used to surface
    near-duplicate memory clusters for curation."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups = {}
    for node in list(parent):
        groups.setdefault(find(node), []).append(node)
    clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
    clusters.sort(key=lambda g: (-len(g), g[0]))
    return clusters
