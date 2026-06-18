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


def rerank_with_bias(results: list, bias_by_id: dict, weight: float):
    """Optional, OFF-by-default tie-break re-rank. `results` is the current ranked
    list ([{id, ...}]); `bias_by_id` maps id -> a score in [0, 1] (e.g. recency or
    confidence). Each item gets `(n - position) + weight*bias`, then a STABLE sort
    by that score re-orders. Because adjacent rank scores differ by exactly 1, a
    weight < 1 can only break (near-)ties -- it never reorders a clear ranking, so
    it is provably non-regressing; weight >= 1 can reorder (measure first). weight
    == 0 (the default everywhere) is a no-op. Pure + deterministic."""
    if not weight or not results:
        return results
    n = len(results)
    decorated = []
    for pos, r in enumerate(results):
        bias = bias_by_id.get(r.get("id"), 0.0) or 0.0
        decorated.append(((n - pos) + weight * bias, pos, r))
    decorated.sort(key=lambda t: (-t[0], t[1]))
    return [r for _, _, r in decorated]


# ---- conflict-candidate heuristic (pure, deterministic, NO LLM) --------------
# A real semantic contradiction can't be decided without an LLM. Instead we flag
# *candidates*: two memories that are MOSTLY about the same thing (high overlap of
# non-discriminator tokens) yet DISAGREE on a discriminator -- a number, a weekday,
# a boolean/antonym, or a negation. The CLIENT (the brain) confirms or dismisses,
# exactly like the duplicate clusters. The cosine "same topic" band is computed by
# the server (it has the vectors); this function applies the lexical disagreement
# test to a candidate pair's texts.
_NEGATIONS = frozenset({
    "not", "no", "never", "none", "without", "cannot", "cant", "dont",
    "doesnt", "isnt", "arent", "wont", "wasnt", "disabled",
})
_ANTONYMS = (
    frozenset({"enabled", "disabled"}), frozenset({"enable", "disable"}),
    frozenset({"true", "false"}), frozenset({"on", "off"}),
    frozenset({"yes", "no"}), frozenset({"allow", "deny"}),
    frozenset({"allowed", "denied"}), frozenset({"up", "down"}),
    frozenset({"active", "inactive"}), frozenset({"present", "absent"}),
    frozenset({"success", "failure"}), frozenset({"pass", "fail"}),
    frozenset({"open", "closed"}), frozenset({"before", "after"}),
)
_ANTONYM_MEMBERS = frozenset().union(*_ANTONYMS)
_DAYS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
})


def is_conflict_pair(text_a: str, text_b: str, min_overlap: float = 0.5) -> bool:
    """True if two memories look like a CONFLICT candidate: they share most of
    their content tokens (subject overlap >= min_overlap) but disagree on a
    discriminator -- differing numbers, differing weekdays, an antonym flip
    (enabled/disabled), or a negation asymmetry ("is set" vs "is not set").
    Identical or merely-similar texts return False (no disagreement). Pure +
    deterministic; intended to be applied only to pairs already in the cosine
    "same topic" band so it stays cheap and precise."""
    ta, tb = tokenize(text_a), tokenize(text_b)
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    nums_a = {t for t in sa if t.isdigit()}
    nums_b = {t for t in sb if t.isdigit()}
    days_a, days_b = sa & _DAYS, sb & _DAYS
    negs_a, negs_b = sa & _NEGATIONS, sb & _NEGATIONS
    disc_a = nums_a | days_a | negs_a | (sa & _ANTONYM_MEMBERS)
    disc_b = nums_b | days_b | negs_b | (sb & _ANTONYM_MEMBERS)
    content_a, content_b = sa - disc_a, sb - disc_b
    shared, union = content_a & content_b, content_a | content_b
    if not shared or not union or len(shared) / len(union) < min_overlap:
        return False
    if nums_a and nums_b and nums_a != nums_b:
        return True
    if days_a and days_b and days_a != days_b:
        return True
    if bool(negs_a) != bool(negs_b):
        return True
    for group in _ANTONYMS:
        ga, gb = group & sa, group & sb
        if ga and gb and ga != gb:
            return True
    return False
