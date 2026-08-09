#!/usr/bin/env python3
"""
Phase 3 — Hybrid search core (lexical + semantic) with RRF fusion.

- Lexical channel:  to_tsquery('simple', 'w1:* & w2:* ...')  (multi-prefix ignore-order)
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
import re
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
GEMMA_URL = os.environ.get("GEMMA_URL", "http://127.0.0.1:8080/v1")
GEMMA_MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")

RRF_K = 60
CANDIDATES = 200  # top-N per channel before fusing

# Source languages the clinician can write in (code -> name used in the prompt).
LANGUAGES = {
    "es": "Spanish", "en": "English", "fr": "French", "de": "German",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "da": "Danish",
}
DEFAULT_LANG = "es"

RRF_SQL = """
WITH q AS (SELECT to_tsquery('simple', %(tsq)s) AS ts),
lex AS (
    SELECT d.id, row_number() OVER (ORDER BY ts_rank(d.term_tsv, q.ts) DESC) AS r
    FROM descriptions d, q
    WHERE %(tsq)s <> '' AND d.term_tsv @@ q.ts
    LIMIT %(cand)s
),
vec AS (
    SELECT d.id, row_number() OVER (ORDER BY d.embedding <=> %(qvec)s::vector) AS r
    FROM descriptions d
    WHERE d.embedding IS NOT NULL
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
           f.lex_s + f.vec_s + (d.pref_us::int * 0.01) AS score,
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
       fsn.term AS fsn
FROM best b
LEFT JOIN LATERAL (
    SELECT term FROM descriptions
    WHERE concept_id = b.concept_id AND type_id = 900000000000003001 LIMIT 1
) fsn ON true
ORDER BY b.score DESC
LIMIT %(k_out)s
"""


def normalize(term: str) -> str:
    t = unicodedata.normalize("NFKD", term.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


def to_prefix_query(text: str) -> str:
    words = [w for w in normalize(text).split() if w]
    return " & ".join(f"{w}:*" for w in words)


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)


def embed_query(text: str) -> str:
    vec = get_model().encode([text], normalize_embeddings=True)[0]
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def gemma_expand(query: str, language: str = "Spanish", timeout: float = 25.0) -> str | None:
    """Translate a clinician's note from `language` to English and expand it to standard clinical
    term(s) + synonyms. Best-effort: if it fails, returns None."""
    prompt = (
        f"You are a clinical terminology assistant. Given a fast clinical note written in {language} "
        "by a clinician, sometimes using acronyms, shorthand, or localisms, translate it to English "
        "and output the corresponding standard English clinical term(s) and close synonyms (expand "
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


def gemma_rerank(query: str, results: list[dict], timeout: float = 30.0) -> list[dict] | None:
    """Reorder candidates by faithfulness to the ORIGINAL query text, using gemma.
    Returns the reordered list, or None if gemma fails (caller keeps the original order)."""
    lines = [f"{i + 1}. {r['fsn'] or r['matched_term']}" for i, r in enumerate(results)]
    prompt = (
        "A clinician searched with the query below. Reorder the candidate SNOMED concepts by how "
        "faithfully each one matches the clinical meaning of the ORIGINAL query. Return ONLY the "
        "item numbers separated by commas, best first, no other text.\n"
        f"Query: {query}\nCandidates:\n" + "\n".join(lines)
    )
    try:
        r = httpx.post(
            f"{GEMMA_URL}/chat/completions",
            json={
                "model": GEMMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 120,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None

    order: list[int] = []
    for tok in re.findall(r"\d+", text):
        n = int(tok)
        if 1 <= n <= len(results) and n not in order:
            order.append(n)
    # Append any candidates gemma omitted, preserving their original order.
    for n in range(1, len(results) + 1):
        if n not in order:
            order.append(n)
    return [results[n - 1] for n in order]


def search_stream(query: str, k: int = 15, use_gemma: bool = True, rerank: bool = False,
                  lang: str = DEFAULT_LANG):
    """Generator version: yields a {"stage": ...} marker before each pipeline step,
    then a final {"stage": "done", ...full result...}. Lets the UI show the live stage.
    `lang` is the ISO code of the language the clinician wrote in (drives the translation prompt)."""
    t: dict[str, float] = {}
    t0 = time.perf_counter()

    yield {"stage": "expand" if use_gemma else "retrieve"}
    tg = time.perf_counter()
    language = LANGUAGES.get(lang, LANGUAGES[DEFAULT_LANG])
    expansion = gemma_expand(query, language=language) if use_gemma else None
    t["expand_ms"] = round((time.perf_counter() - tg) * 1000, 1)

    # The descriptions index is in ENGLISH. If gemma translated/expanded, we use that EN
    # version in BOTH channels: BioLORD embeds an English clinical phrase much better than short
    # Spanish lay jargon (empirical finding). Without gemma, we fall back to the raw query (works for EN).
    search_text = expansion or query
    tsq = to_prefix_query(search_text)

    yield {"stage": "retrieve"}
    tr = time.perf_counter()
    qvec = embed_query(search_text)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(RRF_SQL, {
            "tsq": tsq, "qvec": qvec, "cand": CANDIDATES,
            "k": RRF_K, "k_out": k,
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
        }
        for cid, term, tag, score, in_lex, in_vec, fsn in rows
    ]

    # Preview: emit the base (pre-rerank) results immediately so the UI can show them
    # while the (slower) rerank runs. The UI animates the reordering when "done" arrives.
    yield {"stage": "results", "query": query, "expansion": expansion, "tsquery": tsq,
           "reranked": False, "timings": dict(t), "results": results}

    reranked = False
    if rerank and use_gemma and len(results) > 1:
        yield {"stage": "rerank"}
        trr = time.perf_counter()
        new_order = gemma_rerank(query, results)
        if new_order is not None:
            results = new_order
            reranked = True
        t["rerank_ms"] = round((time.perf_counter() - trr) * 1000, 1)

    t["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    yield {"stage": "done", "query": query, "expansion": expansion, "tsquery": tsq,
           "reranked": reranked, "timings": t, "results": results}


def search(query: str, k: int = 15, use_gemma: bool = True, rerank: bool = False,
           lang: str = DEFAULT_LANG) -> dict:
    """Non-streaming convenience wrapper: drains search_stream and returns the final result."""
    final: dict = {}
    for event in search_stream(query, k=k, use_gemma=use_gemma, rerank=rerank, lang=lang):
        final = event
    final = dict(final)
    final.pop("stage", None)
    return final


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--k", type=int, default=15)
    ap.add_argument("--no-gemma", action="store_true")
    ap.add_argument("--rerank", action="store_true", help="reorder results with gemma by faithfulness")
    ap.add_argument("--lang", default=DEFAULT_LANG, choices=list(LANGUAGES), help="clinician's language")
    args = ap.parse_args()

    out = search(args.query, k=args.k, use_gemma=not args.no_gemma, rerank=args.rerank, lang=args.lang)
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
