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
import json
import os
import re
import threading
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
# How much the cross-encoder gets to reorder. Both the RRF score and the rerank score are min-max
# normalized to [0,1] per query, then blended: final = W*rerank + (1-W)*rrf.
#   W = 1.0 -> rerank fully dictates (old behavior; over-favors broad/high-frequency concepts)
#   W = 0.0 -> rerank ignored (pure RRF)
# 0.5 lets the reranker refine RRF instead of overriding it, so a precise lexical/semantic hit
# isn't demoted below a generic parent just because the CE likes the parent's phrasing.
RERANK_WEIGHT = 0.4

# Entity type (from extract_entities) -> the SNOMED top-level hierarchy to constrain that entity's
# search to. Used as the `filter_concept` for per-entity mapping; falls back to no filter if the
# filtered search finds nothing (the LLM occasionally mislabels the type).
TYPE_TO_HIERARCHY = {
    "finding": 404684003,          # Clinical finding
    "procedure": 71388002,         # Procedure
    "body structure": 123037004,   # Body structure
    "medication": 763158003,       # Medicinal product
    # tolerate common off-enum labels the LLM sometimes emits, mapped to the nearest hierarchy.
    # morphology is intentionally treated as a finding (those descriptors are searched as findings).
    "morphology": 404684003, "diagnosis": 404684003, "disorder": 404684003, "symptom": 404684003,
    "drug": 763158003, "substance": 105590001,
}

RRF_SQL = """
WITH q AS (SELECT to_tsquery('unaccent_simple', %(tsq)s) AS ts),
lex AS (
    -- Rank with native ts_rank, normalized by length AND unique-word count (flags 1|8): divide by
    -- 1+log(length) and by the number of distinct words, so a concise canonical term outranks a
    -- verbose one that merely accrues more prefix hits (e.g. 'myo:*' matching both 'myofibrillar' and
    -- 'myopathy'). Word-based because ts_rank operates on the tsvector (lexemes), which is the native,
    -- index-supported ranking; equivalent to Snowstorm's shorter-is-better intent without leaving ts_rank.
    SELECT d.id, row_number() OVER (ORDER BY ts_rank(d.term_tsv, q.ts, 1|8) DESC) AS r
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


# FastAPI runs sync endpoints in a threadpool, so several requests can hit the shared PyTorch models
# at once. Concurrent forward passes on the SAME model (especially on MPS) stall/wedge, so serialize
# all model inference through one lock. Inference is fast (~tens of ms), so serializing is cheap.
_infer_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)


def embed_query(text: str) -> str:
    with _infer_lock:
        vec = get_model().encode([text], normalize_embeddings=True)[0]
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def gemma_expand(query: str, timeout: float = 25.0) -> str | None:
    """Pre-process a clinician's note: auto-detect the language, translate to English, and normalize
    it to ONE canonical English clinical term (expand abbreviations, fix typos). It does NOT list
    synonyms — the semantic channel handles vocabulary mismatch. Best-effort: returns None on error."""
    prompt = (
        "You are a clinical terminology assistant. A clinician typed a quick note (any language; may "
        "contain acronyms, shorthand, typos, or localisms). Rewrite it as ONE concise, standard "
        "English clinical term — the canonical name a clinician would use (e.g. 'radiografia de torax' "
        "-> 'chest x-ray'; 'EPOC' -> 'chronic obstructive pulmonary disease'). Translate to English, "
        "expand abbreviations, fix typos. Do NOT add synonyms, alternative phrasings, broader or "
        "related concepts, or any word not implied by the note. Output only that single term, nothing "
        "else.\nNote: " + query
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


EXTRACT_SYSTEM = (
    "You are a clinical NLP entity extractor. From a free-text clinical note, extract ONLY diagnoses, "
    "clinical findings, procedures, and medications. Be thorough with imaging/procedure mentions even "
    "when abbreviated (CT, MRI, X-ray, ultrasound, ECG). Do NOT extract raw measurements or numeric "
    "values — vital signs (blood pressure, pulse, temperature, respiratory rate, O2 saturation) and "
    "laboratory results are OUT OF SCOPE, and do not infer a finding from a raw value (skip 'BP 92/52', "
    "'pulse 55', 'platelets 43', 'O2 sat 95%'). Only when the clinician explicitly names a clinical "
    "interpretation (e.g. 'hypotension', 'fever', 'bradycardia', 'thrombocytopenia') do you extract it.\n"
    "Return STRICT JSON of the form "
    '{"terms":[{"text","type","context","language","clinicalTerm","generalTerm","severity","laterality"}]}. '
    "Field rules:\n"
    "- text: the clinical term copied VERBATIM from the note (keep the source language), WITHOUT "
    "surrounding/trailing punctuation and WITHOUT leading articles (a/an/the). Never paraphrase here.\n"
    "- type: the clinical CATEGORY of the entity, one of finding | procedure | medication | body "
    "structure. This is NOT a status — NEVER put present/absent/past/unknown in this field. Treat "
    "cellular/morphologic descriptors (e.g. schistocytes, anisocytosis, spherocytes) as finding.\n"
    "- context: one of present | absent | past | unknown. Use 'absent' ONLY for explicit negation "
    "(no / denies / without / ruled out / negative for). Use 'past' for historical or resolved "
    "conditions ('history of X', 'past X', 'prior X', 'previous X', 'status post X') — the finding DID "
    "occur (keep the POSITIVE concept); NEVER mark a historical condition as absent. Use 'unknown' for "
    "uncertain/possible mentions. Otherwise use 'present'.\n"
    "- clinicalTerm: the standard SNOMED preferred term IN ENGLISH (map lay phrasing to formal "
    "terminology, correct spelling, e.g. 'low platelet count' -> 'thrombocytopenia'). It MUST always be "
    "the POSITIVE concept even when the mention is negated (e.g. 'no fever' -> 'fever').\n"
    "- generalTerm: a broader English term dropping specific qualifiers (e.g. 'bilateral pelvic masses' "
    "-> 'mass'). Positive concept, in English.\n"
    "- language: the English name of the source language of 'text' (e.g. 'English', 'Spanish'). If the "
    "note is not in English, keep 'text' verbatim in the source language but give clinicalTerm and "
    "generalTerm in English.\n"
    "- severity: one of mild | moderate | severe | null.\n"
    "- laterality: one of left | right | bilateral | null.\n"
    "Examples (note type is the category and context is the status — keep them separate): "
    '"history of stroke" -> {"text":"stroke","type":"finding","context":"past"}; '
    '"denies chest pain" -> {"text":"chest pain","type":"finding","context":"absent"}; '
    '"CT of chest" -> {"text":"CT","type":"procedure","context":"present"}.\n'
    "Return ONLY the JSON object, no prose."
)


def _parse_terms(content: str) -> list[dict]:
    """Parse the LLM's JSON entity list, tolerating malformed output. The small model occasionally
    emits an invalid value (e.g. `"laterality":"null}`) or degenerates into repeated whitespace, which
    breaks the whole document; rather than dropping the note, salvage every well-formed entity object."""
    try:
        data = json.loads(content)
        terms = data.get("terms", data) if isinstance(data, dict) else data
        if isinstance(terms, list):
            return [t for t in terms if isinstance(t, dict)]
    except Exception:
        pass
    # Salvage: brace-match individual {...} objects (entities are flat, no nesting) and keep the ones
    # that parse and look like an entity. Starts after the "terms" key to skip the wrapper object.
    out: list[dict] = []
    i0 = content.find('"terms"')
    scan = content[i0:] if i0 != -1 else content
    depth, start = 0, None
    for i, ch in enumerate(scan):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(scan[start:i + 1])
                    if isinstance(obj, dict) and "text" in obj:
                        out.append(obj)
                except Exception:
                    pass
                start = None
    return out


def _chunks(text: str, max_chars: int = 800) -> list[str]:
    """Split a note into sentence-aligned chunks under max_chars. The small model degenerates on long,
    dense structured outputs — it can loop mid-generation and never emit the tail of the note — so we
    extract chunk-by-chunk and merge. Short notes return a single chunk."""
    sents = re.split(r"(?<=[.;\n])\s+", text.strip())
    chunks: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip() if cur else s
    if cur:
        chunks.append(cur)
    return chunks or [text]


def _extract_one(text: str, timeout: float) -> list[dict]:
    """One LLM extraction call over a (short) piece of text. Best-effort: [] on error."""
    try:
        r = httpx.post(
            f"{GEMMA_URL}/chat/completions",
            json={
                "model": GEMMA_MODEL,
                "messages": [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": "Extract clinical entities from this note:\n" + text},
                ],
                "temperature": 0.0,
                "max_tokens": 4000,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except Exception:
        return []
    terms = _parse_terms(content)
    for t in terms:  # the model sometimes emits the string "null" instead of a JSON null
        for k in ("severity", "laterality"):
            if isinstance(t.get(k), str) and t[k].strip().lower() in ("null", "none", ""):
                t[k] = None
    return terms


def extract_entities(text: str, timeout: float = 180.0) -> list[dict]:
    """Extract structured clinical entities from a free-text note via the LLM (JSON mode). Long notes
    are split into chunks and the results merged (deduplicated by text + clinicalTerm), so the small
    model does not degenerate and drop the tail of the note. Best-effort: [] on error. Each entity's
    `clinicalTerm` is the normalized English query for the hybrid engine, and `type` maps to a hierarchy
    filter via TYPE_TO_HIERARCHY."""
    chunks = _chunks(text)
    if len(chunks) == 1:
        return _extract_one(chunks[0], timeout)
    out: list[dict] = []
    seen: set = set()
    for ch in chunks:
        for e in _extract_one(ch, timeout):
            key = ((e.get("text") or "").strip().lower(), (e.get("clinicalTerm") or "").strip().lower())
            if key not in seen:
                seen.add(key)
                out.append(e)
    return out


@lru_cache(maxsize=1)
def get_reranker():
    from sentence_transformers import CrossEncoder
    return CrossEncoder(RERANK_MODEL, device=EMBED_DEVICE)


def _minmax(vals: list[float]) -> list[float]:
    """Scale to [0,1]. If all equal (no signal), return 0.5 so the channel adds no ordering bias."""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        return [0.5] * len(vals)
    return [(v - lo) / (hi - lo) for v in vals]


def crossencoder_rerank(text: str, results: list[dict]) -> list[dict] | None:
    """Reorder candidates by BLENDING the RRF score with a cross-encoder relevance score.
    A purpose-built neural reranker (deterministic, multilingual) — not an LLM opinion, not lexical.
    `text` should be the clinical query (the gemma expansion when available; it scores far better
    against a normalized clinical phrase than against short lay input).

    Both scores are min-max normalized per query and combined as
    RERANK_WEIGHT*rerank + (1-RERANK_WEIGHT)*rrf, so the CE refines the fused ranking instead of
    overriding it — a precise hit isn't demoted below a generic parent the CE happens to like.
    Returns the reordered list, or None on failure (caller keeps the original order)."""
    if len(results) < 2:
        return results
    try:
        ce = get_reranker()
        with _infer_lock:   # serialize model inference (see embed_query)
            ce_scores = [float(s) for s in ce.predict([(text, r["fsn"] or r["matched_term"]) for r in results])]
    except Exception:
        return None
    ce_norm = _minmax(ce_scores)
    rrf_norm = _minmax([float(r["score"]) for r in results])
    w = RERANK_WEIGHT
    combined = [w * ce_norm[i] + (1 - w) * rrf_norm[i] for i in range(len(results))]
    order = sorted(range(len(results)), key=lambda i: combined[i], reverse=True)
    return [dict(results[i], rerank_score=round(ce_scores[i], 4),
                 combined_score=round(combined[i], 4)) for i in order]


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
    te = time.perf_counter()
    qvec = embed_query(search_text)          # BioLORD encodes the query -> 768d vector
    t["embed_ms"] = round((time.perf_counter() - te) * 1000, 1)
    tr = time.perf_counter()
    # Retrieval = lexical + semantic + RRF fusion + FSN, all in ONE SQL plan. Kept as a single
    # query on purpose: both channels are single-digit ms, so splitting them into parallel queries
    # would only add a 2nd-connection round-trip, not save time (the embed above dominates anyway).
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
