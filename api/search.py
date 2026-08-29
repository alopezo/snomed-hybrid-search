#!/usr/bin/env python3
"""
Phase 3 — Hybrid search core (lexical + semantic) with RRF fusion.

- Lexical channel:  to_tsquery('unaccent_simple', 'w1:* & w2:* ...')  (multi-prefix, order- & accent-independent)
- Semantic channel: BioLORD-2023-M -> pgvector kNN (cosine)
- Fusion:           Reciprocal Rank Fusion (k=60) + preferred-term boost
- Optional:         ES->EN expansion/translation with gemma (improves the lexical channel)

Can be used as a library (import search) or CLI:
    python api/search.py "azucar alta"
    python api/search.py --no-gemma "infarto"
"""
from __future__ import annotations

import argparse
import os
import time
import unicodedata
from functools import lru_cache

import httpx
import psycopg
from dotenv import load_dotenv

load_dotenv()

PG_DSN = os.environ["PG_DSN"]
EMBED_MODEL = os.environ.get("EMBED_MODEL", "FremyCompany/BioLORD-2023-M")
EMBED_DEVICE = os.environ.get("EMBED_DEVICE", "cpu")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
GEMMA_URL = os.environ.get("GEMMA_URL", "http://127.0.0.1:8080/v1")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")

RRF_K = 60
CANDIDATES = 200  # top-N per channel before fusing
# Preferred-term nudge added to the RRF score. Kept small: an RRF rank step is ~1/60² ≈ 0.0003,
# so 0.001 ≈ a few ranks — a gentle tie-breaker, not a dominator (0.01 used to flip clear winners).
PREF_BOOST = 0.001

RRF_SQL = """
WITH q AS (SELECT to_tsquery('unaccent_simple', %(tsq)s) AS ts),
lex AS (
    SELECT d.id, row_number() OVER (ORDER BY ts_rank(d.term_tsv, q.ts, 1) DESC) AS r
    FROM descriptions d, q
    WHERE %(tsq)s <> '' AND d.term_tsv @@ q.ts
      AND (%(filter)s::bigint IS NULL OR EXISTS (
          SELECT 1 FROM concept_ancestors ca
          WHERE ca.concept_id = d.concept_id AND ca.ancestors @> ARRAY[%(filter)s::bigint]))
    LIMIT %(cand)s
),
vec AS (
    SELECT d.id, row_number() OVER (ORDER BY d.embedding <=> %(qvec)s::vector) AS r
    FROM descriptions d
    WHERE d.embedding IS NOT NULL
      AND (%(filter)s::bigint IS NULL OR EXISTS (
          SELECT 1 FROM concept_ancestors ca
          WHERE ca.concept_id = d.concept_id AND ca.ancestors @> ARRAY[%(filter)s::bigint]))
    ORDER BY d.embedding <=> %(qvec)s::vector
    LIMIT %(cand)s
),
fused AS (
    SELECT COALESCE(l.id, v.id) AS id,
           COALESCE(1.0/(%(k)s + l.r), 0) AS lex_s,
           COALESCE(1.0/(%(k)s + v.r), 0) AS vec_s,
           (l.id IS NOT NULL) AS in_lex,
           (v.id IS NOT NULL) AS in_vec
    FROM lex l FULL OUTER JOIN vec v ON l.id = v.id
),
scored AS (
    SELECT d.concept_id, d.term, d.semantic_tag,
           f.lex_s + f.vec_s + (d.pref_us::int * %(pref_boost)s) AS score,
           f.in_lex, f.in_vec
    FROM fused f JOIN descriptions d ON d.id = f.id
),
best AS (
    SELECT DISTINCT ON (concept_id)
           concept_id, term AS matched_term, semantic_tag, score, in_lex, in_vec
    FROM scored
    ORDER BY concept_id, score DESC
)
SELECT b.concept_id, b.matched_term, b.semantic_tag, b.score, b.in_lex, b.in_vec,
       fsn.term AS fsn,
       EXISTS (SELECT 1 FROM descriptions e
               WHERE e.concept_id = b.concept_id AND e.term_norm = ANY(%(exact_norms)s)) AS is_exact
FROM best b
LEFT JOIN LATERAL (
    SELECT term FROM descriptions
    WHERE concept_id = b.concept_id AND type_id = 900000000000003001 LIMIT 1
) fsn ON true
ORDER BY b.score DESC
LIMIT %(k_out)s
"""


def normalize(term: str) -> str:
    """lower + strip accents (NFKD) + collapse spaces. Used for the embedding dedup key."""
    t = unicodedata.normalize("NFKD", term.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


def dedup_words(text: str) -> str:
    """Order-preserving, case-insensitive de-duplication of space-separated words
    (the LLM sometimes repeats a term, e.g. 'thrombocytopenia thrombocytopenia')."""
    out, seen = [], set()
    for w in text.split():
        k = w.lower()
        if k not in seen:
            seen.add(k)
            out.append(w)
    return " ".join(out)


def to_prefix_query(text: str) -> str:
    """Build a multi-prefix, order-independent tsquery: 'w1:* & w2:* ...'. Tokenizes, strips
    punctuation, and de-duplicates. Accent/case folding is done by the SQL text-search config
    (unaccent_simple), so raw letters are kept here — index and query normalize the same way."""
    words, seen = [], set()
    for raw in text.split():
        w = "".join(ch for ch in raw if ch.isalnum())
        if not w:
            continue
        k = w.lower()
        if k in seen:
            continue
        seen.add(k)
        words.append(w)
    return " & ".join(f"{w}:*" for w in words)


def exact_first(results: list[dict]) -> list[dict]:
    """Deterministic manual rerank: float concepts with an EXACT description match to the top,
    preserving the existing order within each group (stable). An exact match to what the user
    typed should always win, regardless of the model reranker or the preferred-term nudge."""
    return sorted(results, key=lambda r: 0 if r.get("is_exact") else 1)


def build_tsquery(query: str, expansion: str | None) -> str:
    """Lexical query = OR of two AND-groups: the ORIGINAL query and the gemma expansion.
    This keeps exact/synonym matches for already-precise clinical terms (e.g. 'Hepatomegaly',
    which gemma may otherwise expand into something worse) while still bridging lay terms via
    the expansion. Each group ANDs its own prefixes; the groups are OR-ed."""
    groups = []
    for text in (query, expansion):
        if not text:
            continue
        g = to_prefix_query(text)
        if g and g not in groups:
            groups.append(g)
    if not groups:
        return ""
    if len(groups) == 1:
        return groups[0]
    return " | ".join(f"({g})" for g in groups)


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)


def embed_query(text: str) -> str:
    vec = get_model().encode([text], normalize_embeddings=True)[0]
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def gemma_expand(query: str, timeout: float = 25.0) -> str | None:
    """Pre-process a clinician's note: auto-detect the language, translate to English, and expand
    it to standard clinical term(s) + synonyms (also cleans up shorthand/typos). Best-effort:
    if it fails, returns None."""
    prompt = (
        "You are a clinical terminology assistant. Given a fast clinical note from a clinician (any "
        "language; sometimes using acronyms, shorthand, or localisms), translate it to English and "
        "output the corresponding standard English clinical term(s) and close synonyms (expand "
        "abbreviations, use formal medical vocabulary). Do not add details not implied by the note. "
        "Return ONLY English medical terms separated by spaces, no explanations.\nNote: " + query
    )
    try:
        r = httpx.post(
            f"{GEMMA_URL}/chat/completions",
            json={
                "model": GEMMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 60,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


@lru_cache(maxsize=1)
def get_reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANK_MODEL, device=EMBED_DEVICE)


def crossencoder_rerank(text: str, results: list[dict]) -> list[dict] | None:
    """Reorder candidates by a cross-encoder's relevance score for (text, concept FSN).
    A purpose-built neural reranker (deterministic, multilingual) — not an LLM opinion, not lexical.
    `text` should be the clinical query (the gemma expansion when available; it scores far better
    against a normalized clinical phrase than against short lay input). Returns the reordered list,
    or None on failure (caller keeps the original order)."""
    if len(results) < 2:
        return results
    try:
        ce = get_reranker()
        scores = ce.predict([(text, r["fsn"] or r["matched_term"]) for r in results])
    except Exception:
        return None
    order = sorted(range(len(results)), key=lambda i: float(scores[i]), reverse=True)
    return [dict(results[i], rerank_score=round(float(scores[i]), 4)) for i in order]


def search_stream(query: str, k: int = 15, use_gemma: bool = True, rerank: bool = False,
                  filter_concept: int | None = None):
    """Generator version: yields a {"stage": ...} marker before each pipeline step,
    then a final {"stage": "done", ...full result...}. Lets the UI show the live stage.
    `filter_concept`: if set, restrict results to descendants-or-self of that concept id."""
    t: dict[str, float] = {}
    t0 = time.perf_counter()

    yield {"stage": "expand" if use_gemma else "retrieve"}
    tg = time.perf_counter()
    expansion = gemma_expand(query) if use_gemma else None
    if expansion:
        expansion = dedup_words(expansion)   # the LLM sometimes repeats terms
    t["expand_ms"] = round((time.perf_counter() - tg) * 1000, 1)

    # The descriptions index is in ENGLISH. If gemma translated/expanded, we use that EN
    # version in BOTH channels: BioLORD embeds an English clinical phrase much better than short
    # Spanish lay jargon (empirical finding). Without gemma, we fall back to the raw query (works for EN).
    search_text = expansion or query          # semantic channel: expansion when available
    tsq = build_tsquery(query, expansion)     # lexical channel: OR(original, expansion)

    # Exact-match set: what the user typed and the gemma expansion, normalized like term_norm.
    exact_norms = list(dict.fromkeys(n for n in (normalize(query), normalize(expansion or "")) if n))

    yield {"stage": "retrieve"}
    tr = time.perf_counter()
    qvec = embed_query(search_text)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        if filter_concept is not None:
            # Filtered ANN: let HNSW keep scanning until enough in-subtree neighbors pass (pgvector 0.8+).
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur.execute(RRF_SQL, {
            "tsq": tsq, "qvec": qvec, "cand": CANDIDATES,
            "k": RRF_K, "k_out": k, "pref_boost": PREF_BOOST, "exact_norms": exact_norms,
            "filter": filter_concept,
        })
        rows = cur.fetchall()
    t["retrieval_ms"] = round((time.perf_counter() - tr) * 1000, 1)

    results = [
        {
            "concept_id": str(cid),
            "matched_term": term,
            "fsn": fsn,
            "semantic_tag": tag,
            "score": round(float(score), 5),
            "channel": ("both" if in_lex and in_vec else "lexical" if in_lex else "semantic"),
            "is_exact": bool(is_exact),
        }
        for cid, term, tag, score, in_lex, in_vec, fsn, is_exact in rows
    ]
    results = exact_first(results)   # deterministic: exact matches to the top

    # Preview: emit the base (pre-rerank) results immediately so the UI can show them
    # while the (slower) rerank runs. The UI animates the reordering when "done" arrives.
    yield {"stage": "results", "query": query, "expansion": expansion, "tsquery": tsq,
           "reranked": False, "timings": dict(t), "results": results}

    reranked = False
    if rerank and len(results) > 1:
        yield {"stage": "rerank"}
        trr = time.perf_counter()
        new_order = crossencoder_rerank(search_text, results)
        if new_order is not None:
            results = exact_first(new_order)   # rerank the rest, but keep exact matches on top
            reranked = True
        t["rerank_ms"] = round((time.perf_counter() - trr) * 1000, 1)

    t["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    yield {"stage": "done", "query": query, "expansion": expansion, "tsquery": tsq,
           "reranked": reranked, "timings": t, "results": results}


def search(query: str, k: int = 15, use_gemma: bool = True, rerank: bool = False,
           filter_concept: int | None = None) -> dict:
    """Non-streaming convenience wrapper: drains search_stream and returns the final result."""
    final: dict = {}
    for event in search_stream(query, k=k, use_gemma=use_gemma, rerank=rerank,
                               filter_concept=filter_concept):
        final = event
    final = dict(final)
    final.pop("stage", None)
    return final


def health() -> dict:
    """Report the status of each dependency: Postgres, the LLM endpoint, the embedding model."""
    status: dict = {}
    try:
        with psycopg.connect(PG_DSN, connect_timeout=3) as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FILTER (WHERE embedding IS NOT NULL) FROM descriptions")
            n = cur.fetchone()[0]
        status["db"] = {"ok": True, "embeddings": n}
    except Exception as e:
        status["db"] = {"ok": False, "error": str(e)[:140]}
    try:
        r = httpx.get(f"{GEMMA_URL}/models", timeout=3)
        r.raise_for_status()
        installed = [m.get("id") for m in r.json().get("data", [])]
        status["llm"] = {"ok": True, "model": GEMMA_MODEL, "installed": GEMMA_MODEL in installed}
    except Exception as e:
        status["llm"] = {"ok": False, "model": GEMMA_MODEL, "error": str(e)[:140]}
    status["embeddings"] = {"ok": True, "loaded": get_model.cache_info().currsize > 0}
    return status


def warmup() -> dict:
    """Load the embedding model and warm the LLM (the 'wake' action)."""
    get_model()
    exp = gemma_expand("warmup")
    return {"embeddings_loaded": True, "llm_ok": exp is not None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--no-gemma", action="store_true")
    ap.add_argument("--rerank", action="store_true", help="reorder results with the cross-encoder")
    args = ap.parse_args()

    out = search(args.query, k=args.k, use_gemma=not args.no_gemma, rerank=args.rerank)
    print(f"query:     {out['query']!r}")
    if out["expansion"]:
        print(f"expansion: {out['expansion']!r}")
    print(f"tsquery:   {out['tsquery']!r}")
    t = out["timings"]
    timing = f"total {t['total_ms']} ms (expand {t.get('expand_ms', 0)} + retrieval {t.get('retrieval_ms', 0)}"
    timing += f" + rerank {t['rerank_ms']}" if "rerank_ms" in t else ""
    print(timing + ")" + ("  [reranked]" if out["reranked"] else "") + "\n")
    for i, r in enumerate(out["results"], 1):
        tag = f" ({r['semantic_tag']})" if r["semantic_tag"] else ""
        print(f"{i:2}. [{r['channel']:8}] {r['concept_id']}  {r['fsn'] or r['matched_term']}{tag}")


if __name__ == "__main__":
    main()
