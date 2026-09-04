#!/usr/bin/env python3
"""
Validation — run the entity extractor + hybrid mapping over the first N cases of a Spanish
gold-standard corpus (BSC: SympTEMIST / DisTEMIST / …) and compare the mapped SNOMED CT concepts
against the corpus's gold entity-linking annotations.

These BioCreative/BioASQ corpora share the same shape: one .txt per clinical case, plus a linking TSV
with a mention span and a SNOMED CT `code` per row (columns differ per corpus — see CORPORA below).
The gold scope is broader than our extractor (it includes lab-value / normal-finding mentions we skip
and asks for one exact code), so recall is a lower bound, not a full accuracy figure.

Usage (API must be running — `make up`):
    cd snomed-search && .venv/bin/python validation/run_corpus.py --corpus distemist --n 10
    .venv/bin/python validation/run_corpus.py --corpus symptemist --n 10
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
API = os.environ.get("VALIDATION_API", "http://127.0.0.1:8090")
load_dotenv(HERE.parent / ".env")

_SYMP = HERE / "symptemist/extracted/symptemist-train_all_subtasks+gazetteer+multilingual+test_all_subtasks+bg_231006/symptemist_train"
_DIST = HERE / "distemist/extracted/distemist/training"

CORPORA = {
    # name: {tsv, txt, text_col, code_col} — 0-based column indices in the linking TSV
    "symptemist": {
        "tsv": _SYMP / "subtask2-linking/symptemist_tsv_train_subtask2.tsv",
        "txt": _SYMP / "subtask1-ner/txt",
        "text_col": 4, "code_col": 5,
    },
    "distemist": {
        "tsv": _DIST / "subtrack2_linking/distemist_subtrack2_training1_linking.tsv",
        "txt": _DIST / "text_files",
        "text_col": 5, "code_col": 6,
    },
}


def load_gold(cfg: dict, n: int) -> "dict[str, list[tuple[str, str]]]":
    """file -> [(mention_text, gold_code)] for the first n files by filename.
    Read the whole TSV (rows may be sorted by mention, not grouped by file), then pick n files."""
    tc, cc = cfg["text_col"], cfg["code_col"]
    allg: dict[str, list[tuple[str, str]]] = {}
    with open(cfg["tsv"], encoding="utf-8") as f:
        next(f)
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) <= max(tc, cc):
                continue
            allg.setdefault(c[0], []).append((c[tc], c[cc]))
    files = sorted(allg)[:n]
    return {fn: allg[fn] for fn in files}


def numeric_codes(mentions: list[tuple[str, str]]) -> set[str]:
    """Gold codes that are a single SNOMED id (drop NO_CODE and '+'/'-' composites)."""
    return {code.strip() for _, code in mentions if code.strip().isdigit()}


def codes_in_release(codes: set[str], dsn: str) -> set[str]:
    if not codes:
        return set()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT concept_id FROM concept_ancestors WHERE concept_id = ANY(%s)",
                    ([int(c) for c in codes],))
        return {str(r[0]) for r in cur.fetchall()}


def hierarchy_hits(gold: set[str], our: set[str], dsn: str) -> set[str]:
    """Lenient, hierarchy-aware credit: the subset of gold codes for which some retrieved concept is
    the gold itself, an ancestor of it, or a descendant of it (same IS-A lineage). Uses concept_ancestors
    (ancestors-incl-self). This credits parent/child equivalents but NOT cross-hierarchy choices
    (e.g. gold 'Renal stone (substance)' vs our 'Kidney stone (disorder)')."""
    g_ids = [int(g) for g in gold]
    o_ids = [int(o) for o in our]
    if not g_ids or not o_ids:
        return set()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT concept_id, ancestors FROM concept_ancestors WHERE concept_id = ANY(%s)", (g_ids,))
        anc_gold = {r[0]: set(r[1]) for r in cur.fetchall()}
        cur.execute("SELECT concept_id, ancestors FROM concept_ancestors WHERE concept_id = ANY(%s)", (o_ids,))
        anc_our = {r[0]: set(r[1]) for r in cur.fetchall()}
    o_set = set(o_ids)
    hit = set()
    for g in g_ids:
        ancestors_of_g = anc_gold.get(g, {g})           # our concept is ancestor-or-self of gold
        is_desc_of_g = any(g in anc_our.get(c, set()) for c in o_ids)  # our concept is descendant-or-self
        if (o_set & ancestors_of_g) or is_desc_of_g:
            hit.add(str(g))
    return hit


def extract(note: str) -> dict:
    r = httpx.post(f"{API}/api/extract", json={"text": note}, timeout=300)
    r.raise_for_status()
    return r.json()


def search(q: str, filt: int | None) -> list[dict]:
    u = f"{API}/api/search?q={urllib.parse.quote(q)}&gemma=false&rerank=false&k=5"
    if filt is not None:
        u += f"&filter={filt}"
    return httpx.get(u, timeout=60).json().get("results", [])


def map_entities(entities: list[dict], type_filters: dict) -> list[dict]:
    out = []
    for e in entities:
        q = (e.get("clinicalTerm") or e.get("text") or "").strip()
        filt = type_filters.get(e.get("type"))
        res = search(q, filt) if q else []
        relaxed = False
        if filt is not None and not res:
            res = search(q, None)
            relaxed = True
        topk = [r["concept_id"] for r in res]
        out.append({
            "text": e.get("text"), "type": e.get("type"), "context": e.get("context"),
            "clinicalTerm": q, "relaxed": relaxed,
            "best": topk[0] if topk else None,
            "best_fsn": res[0]["fsn"] if res else None,
            "topk": topk,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=sorted(CORPORA), required=True)
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    cfg = CORPORA[args.corpus]
    dsn = os.environ["PG_DSN"]
    results_dir = HERE / "results"
    results_dir.mkdir(exist_ok=True)

    gold = load_gold(cfg, args.n)
    print(f"[{args.corpus}] running {len(gold)} cases against {API}\n")

    per_file = []
    agg = {"gold_mappable": 0, "in_release": 0, "hit_best": 0, "hit_topk": 0,
           "hit_best_hier": 0, "hit_topk_hier": 0}
    t0 = time.time()

    for i, (fn, mentions) in enumerate(gold.items(), 1):
        note = (cfg["txt"] / f"{fn}.txt").read_text(encoding="utf-8")
        gcodes = numeric_codes(mentions)
        greach = codes_in_release(gcodes, dsn)

        ex = extract(note)
        mapped = map_entities(ex.get("entities", []), ex.get("type_filters", {}))
        our_best = {m["best"] for m in mapped if m["best"]}
        our_topk = {c for m in mapped for c in m["topk"]}
        hit_best = gcodes & our_best
        hit_topk = gcodes & our_topk
        hit_best_h = hierarchy_hits(gcodes, our_best, dsn)   # exact OR ancestor/descendant of gold
        hit_topk_h = hierarchy_hits(gcodes, our_topk, dsn)

        per_file.append({
            "file": fn, "gold_codes": sorted(gcodes), "gold_in_release": sorted(greach),
            "entities": mapped, "hit_best": sorted(hit_best), "hit_topk": sorted(hit_topk),
            "hit_best_hier": sorted(hit_best_h), "hit_topk_hier": sorted(hit_topk_h),
            "recall_best": round(len(hit_best) / len(gcodes), 3) if gcodes else None,
            "recall_topk": round(len(hit_topk) / len(gcodes), 3) if gcodes else None,
            "recall_best_hier": round(len(hit_best_h) / len(gcodes), 3) if gcodes else None,
            "recall_topk_hier": round(len(hit_topk_h) / len(gcodes), 3) if gcodes else None,
        })
        agg["gold_mappable"] += len(gcodes)
        agg["in_release"] += len(greach)
        agg["hit_best"] += len(hit_best)
        agg["hit_topk"] += len(hit_topk)
        agg["hit_best_hier"] += len(hit_best_h)
        agg["hit_topk_hier"] += len(hit_topk_h)
        print(f"[{i}/{len(gold)}] {fn}  gold={len(gcodes)} in_release={len(greach)} "
              f"hit_best={len(hit_best)} hit_topk={len(hit_topk)} "
              f"hier_best={len(hit_best_h)} hier_topk={len(hit_topk_h)}  ({len(mapped)} entities)")

    gm, inr = agg["gold_mappable"] or 1, agg["in_release"] or 1
    summary = {
        "corpus": args.corpus, "n_files": len(gold), "elapsed_s": round(time.time() - t0, 1), **agg,
        "recall_best_over_gold": round(agg["hit_best"] / gm, 3),
        "recall_topk_over_gold": round(agg["hit_topk"] / gm, 3),
        "recall_best_over_in_release": round(agg["hit_best"] / inr, 3),
        "recall_topk_over_in_release": round(agg["hit_topk"] / inr, 3),
        "recall_best_hier_over_gold": round(agg["hit_best_hier"] / gm, 3),
        "recall_topk_hier_over_gold": round(agg["hit_topk_hier"] / gm, 3),
        "recall_best_hier_over_in_release": round(agg["hit_best_hier"] / inr, 3),
        "recall_topk_hier_over_in_release": round(agg["hit_topk_hier"] / inr, 3),
    }
    stem = f"{args.corpus}_first{args.n}"
    (results_dir / f"{stem}.json").write_text(
        json.dumps({"summary": summary, "files": per_file}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(results_dir / f"{stem}.md", summary, per_file)

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nSaved: {results_dir/stem}.json and .md")


def write_markdown(path: Path, summary: dict, per_file: list[dict]) -> None:
    L = [f"# {summary['corpus'].upper()} validation — first {summary['n_files']} cases\n",
         "Extractor + hybrid mapping vs. the gold entity-linking SNOMED CT codes. Two metrics: **strict**",
         "(our concept id equals the gold) and **hierarchy-aware** (our concept is the gold, an ancestor,",
         "or a descendant of it — crediting parent/child equivalents). Recall still has a built-in ceiling:",
         "the corpus scope includes mentions the extractor deliberately skips, and cross-hierarchy",
         "representation choices (e.g. a *substance* vs a *disorder* concept) are not credited by either.\n",
         "## Summary\n",
         f"- Files: **{summary['n_files']}**, elapsed {summary['elapsed_s']}s",
         f"- Gold mappable codes: **{summary['gold_mappable']}** "
         f"(in our release: **{summary['in_release']}**)",
         "",
         "| metric | best match | top-5 |",
         "|---|---|---|",
         f"| **strict** (recall of gold) | {summary['recall_best_over_gold']} | {summary['recall_topk_over_gold']} |",
         f"| **hierarchy-aware** (of gold) | {summary['recall_best_hier_over_gold']} | {summary['recall_topk_hier_over_gold']} |",
         f"| strict (of in-release) | {summary['recall_best_over_in_release']} | {summary['recall_topk_over_in_release']} |",
         f"| hierarchy-aware (of in-release) | {summary['recall_best_hier_over_in_release']} | {summary['recall_topk_hier_over_in_release']} |",
         "",
         "## Per file\n",
         "| file | gold | in-rel | hit best | hit top-5 | hier best | hier top-5 |",
         "|---|---|---|---|---|---|---|"]
    for pf in per_file:
        L.append(f"| `{pf['file']}` | {len(pf['gold_codes'])} | {len(pf['gold_in_release'])} | "
                 f"{len(pf['hit_best'])} | {len(pf['hit_topk'])} | "
                 f"{len(pf['hit_best_hier'])} | {len(pf['hit_topk_hier'])} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
