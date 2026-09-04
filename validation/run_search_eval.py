#!/usr/bin/env python3
"""
Validation (SEARCH ONLY) — normalization/linking accuracy of the hybrid search, independent of our
entity extractor. For every gold mention in the first N documents of a corpus, we feed the gold span
text directly to /api/search (query pre-processing is part of *search*, not extraction) and check the
rank of the gold SNOMED CT code. This mirrors the corpus's entity-linking/normalization subtask.

    cd snomed-search && .venv/bin/python validation/run_search_eval.py --corpus distemist --n 10

Config (defaults): gemma query normalization ON, rerank OFF, filter = Clinical finding (the corpus is
diseases — a legitimate hierarchy constraint), k = 10.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
API = os.environ.get("VALIDATION_API", "http://127.0.0.1:8090")
load_dotenv(HERE.parent / ".env")
sys.path.insert(0, str(HERE))
from run_corpus import CORPORA, hierarchy_hits, load_gold  # noqa: E402
import history  # noqa: E402

BROWSER = "https://browser.ihtsdotools.org/?perspective=full&conceptId1={}&edition=MAIN&languages=en"


def load_mentions(cfg: dict, n: int) -> list[tuple[str, str, str]]:
    """All (file, span, code) with a single numeric code across the first n files, deduped by span+code."""
    gold = load_gold(cfg, n)
    seen, out = set(), []
    for fn, ms in gold.items():
        for span, code in ms:
            code = code.strip()
            if not code.isdigit():
                continue
            key = (re.sub(r"\s+", " ", span.lower()).strip(), code)
            if key in seen:
                continue
            seen.add(key)
            out.append((fn, span, code))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=sorted(CORPORA), required=True)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--gemma", default="true")
    ap.add_argument("--rerank", default="false")
    ap.add_argument("--filter", default="404684003")   # Clinical finding; "none" to disable
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--scope", default="disorder,finding",
                    help="keep only gold whose (resolved) FSN tag is in this set; 'all' to disable")
    args = ap.parse_args()
    scope = None if args.scope.lower() == "all" else {t.strip() for t in args.scope.split(",")}
    cfg = CORPORA[args.corpus]
    dsn = os.environ["PG_DSN"]
    filt = None if args.filter.lower() == "none" else args.filter
    (HERE / "results").mkdir(exist_ok=True)

    conn = psycopg.connect(dsn)
    cur = conn.cursor()

    def fsn(code: str) -> str | None:
        cur.execute("SELECT term FROM descriptions WHERE concept_id=%s AND type_id=900000000000003001 LIMIT 1",
                    (int(code),))
        r = cur.fetchone()
        return r[0] if r else None

    def search(q: str) -> list[dict]:
        u = (f"{API}/api/search?q={urllib.parse.quote(q)}&k={args.k}"
             f"&gemma={args.gemma}&rerank={args.rerank}")
        if filt is not None:
            u += f"&filter={filt}"
        return httpx.get(u, timeout=90).json().get("results", [])

    mentions = load_mentions(cfg, args.n)
    assoc = history.load_assoc({c for _, _, c in mentions})   # historical associations for gold codes
    print(f"[{args.corpus}] SEARCH eval on {len(mentions)} unique mentions "
          f"(gemma={args.gemma} rerank={args.rerank} filter={filt} k={args.k} scope={args.scope})\n")

    rows, t0 = [], time.time()
    agg = {"total": 0, "resolved": 0, "scoped_out": 0, "hit1": 0, "hit5": 0, "hit10": 0,
           "h1": 0, "h5": 0, "h10": 0, "rr": 0.0}
    for i, (fn, span, code) in enumerate(mentions, 1):
        rcode, tag = history.resolve(cur, code, assoc)      # current active code + FSN tag
        if scope is not None and (tag is None or tag not in scope):
            agg["scoped_out"] += 1
            continue
        gold = rcode                                        # score against the resolved (current) code
        res = search(span)
        ids = [r["concept_id"] for r in res]
        rank = ids.index(gold) + 1 if gold in ids else None
        h1 = bool(hierarchy_hits({gold}, set(ids[:1]), dsn))
        h5 = bool(hierarchy_hits({gold}, set(ids[:5]), dsn))
        h10 = bool(hierarchy_hits({gold}, set(ids[:args.k]), dsn))
        top = res[0] if res else None
        rows.append({"file": fn, "span": span, "gold": code, "resolved": (rcode if rcode != code else None),
                     "tag": tag, "gold_fsn": fsn(gold), "rank": rank,
                     "top1": top["concept_id"] if top else None, "top1_fsn": top["fsn"] if top else None,
                     "hit1": rank == 1, "hit5": bool(rank and rank <= 5), "hit10": bool(rank),
                     "hier1": h1, "hier5": h5, "hier10": h10})
        agg["total"] += 1
        agg["resolved"] += int(rcode != code)
        agg["hit1"] += int(rank == 1)
        agg["hit5"] += int(bool(rank and rank <= 5))
        agg["hit10"] += int(bool(rank))
        agg["h1"] += int(h1); agg["h5"] += int(h5); agg["h10"] += int(h10)
        agg["rr"] += (1.0 / rank) if rank else 0.0
        if i % 10 == 0:
            print(f"  {i}/{len(mentions)}…")

    n = agg["total"] or 1
    r = lambda x: round(x / n, 3)
    summary = {
        "corpus": args.corpus,
        "config": {"gemma": args.gemma, "rerank": args.rerank, "filter": filt, "k": args.k, "scope": args.scope},
        "mentions": agg["total"], "scoped_out": agg["scoped_out"], "resolved_via_history": agg["resolved"],
        "elapsed_s": round(time.time() - t0, 1),
        "acc@1": r(agg["hit1"]), "recall@5": r(agg["hit5"]), "recall@10": r(agg["hit10"]),
        "hier@1": r(agg["h1"]), "hier@5": r(agg["h5"]), "hier@10": r(agg["h10"]),
        "MRR": r(agg["rr"]),
    }
    pp = "ON" if args.gemma.lower() == "true" else "OFF"
    rr = "ON" if args.rerank.lower() == "true" else "OFF"
    stem = f"{args.corpus}_search_eval_pp{pp}_rr{rr}"
    (HERE / "results" / f"{stem}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(HERE / "results" / f"{stem}.md", summary, rows)
    conn.close()

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nSaved: results/{stem}.json and .md")


def write_md(path: Path, s: dict, rows: list[dict]) -> None:
    link = lambda c: f"[{c}]({BROWSER.format(c)})" if c else "—"
    outcome = lambda x: ("exact@1" if x["hit1"] else "exact@5" if x["hit5"] else
                         "hier@5" if x["hier5"] else "exact@10" if x["hit10"] else
                         "hier@10" if x["hier10"] else "miss")
    c = s["config"]
    L = [f"# {s['corpus'].upper()} — search-only evaluation ({s['mentions']} gold mentions)\n",
         "## Methodology\n",
         "- **Task.** Normalization/linking of the hybrid search *alone*, independent of our entity",
         "  extractor: each gold mention's verbatim span is the query, and we score the rank of the gold",
         "  SNOMED CT code. This mirrors the corpus's entity-linking subtask.",
         f"- **Search config.** gemma query-normalization = `{c['gemma']}` (translation/abbreviation",
         f"  expansion is part of *search*, not extraction), rerank = `{c['rerank']}`, hierarchy filter =",
         f"  `{c['filter']}` (Clinical finding — the corpus is diseases), k = {c['k']}.",
         "- **Edition-drift correction.** The gold is annotated against an older SNOMED CT edition; some",
         "  gold concepts are inactive in our loaded release (International 2026-06-01). Each gold code is",
         "  resolved through SNOMED **historical associations** to its current active concept, in priority",
         "  order SAME AS → REPLACED BY → POSSIBLY REPLACED BY → POSSIBLY EQUIVALENT TO → ALTERNATIVE",
         "  (MOVED TO / WAS A excluded). Scoring uses the resolved code.",
         f"  Here **{s['resolved_via_history']}** gold codes were resolved this way.",
         f"- **Scope.** Only gold whose resolved FSN tag is in `{c['scope']}` is evaluated, so the system",
         "  is not penalized for representation choices outside its target (gold coded as *substance* or",
         f"  *morphologic abnormality* rather than *disorder/finding*). **{s['scoped_out']}** mentions were",
         "  scoped out.",
         "- **Metrics.** *strict* = our concept id equals the (resolved) gold; *hierarchy-aware* = our",
         "  concept is the gold, an ancestor, or a descendant of it. acc@1 = correct at rank 1; recall@k =",
         "  correct within top-k; MRR = mean reciprocal rank. Codes link to the SNOMED International browser.\n",
         "## Results\n",
         "Read recall@5/@10 as a concept picker: is the gold on the short list the user would scan?",
         "*near-miss* is the extra fraction whose top-k holds a parent/child of the gold (strict + near-miss",
         "= same-lineage).\n",
         "| metric | strict | + near-miss | = same-lineage |",
         "|---|---|---|---|",
         f"| acc@1 | {s['acc@1']} | +{round(s['hier@1']-s['acc@1'],3)} | {s['hier@1']} |",
         f"| recall@5 | {s['recall@5']} | +{round(s['hier@5']-s['recall@5'],3)} | {s['hier@5']} |",
         f"| recall@10 | {s['recall@10']} | +{round(s['hier@10']-s['recall@10'],3)} | {s['hier@10']} |",
         f"\nMRR {s['MRR']} · {s['resolved_via_history']} gold resolved via history · "
         f"{s['scoped_out']} scoped out (non-{s['config']['scope']}) · elapsed {s['elapsed_s']}s\n",
         "| # | mention (query) | gold (→ resolved) | gold FSN | our top-1 | top-1 FSN | rank | outcome |",
         "|---|---|---|---|---|---|---|---|"]
    for i, x in enumerate(rows, 1):
        gcell = link(x["gold"]) + (f" → {link(x['resolved'])}" if x.get("resolved") else "")
        L.append(f"| {i} | {x['span']} | {gcell} | {x['gold_fsn'] or '—'} | "
                 f"{link(x['top1'])} | {x['top1_fsn'] or '—'} | {x['rank'] or '—'} | {outcome(x)} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
