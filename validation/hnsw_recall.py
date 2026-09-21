#!/usr/bin/env python3
"""
HNSW recall diagnostic (Phase 2 of the ef_search fix).

For a sample of gold mentions we compare the semantic channel's approximate nearest neighbours (the
pgvector HNSW index) against the EXACT nearest neighbours (brute force: `enable_indexscan = off` forces a
sequential scan that computes the real cosine distance over every embedded description). This isolates the
index behaviour from BioLORD and from the LLM: any miss here is the index, not the model.

We sweep `hnsw.ef_search` and report, per value:
  * recall@10   = |HNSW top-10 ∩ exact top-10| / 10, averaged over the sample (how faithful the ANN is)
  * collapses   = how many queries land at recall <= 0.2 (the number the average hides: a stuck graph
                  traversal that returns almost none of the true neighbours)
  * rows        = mean rows actually returned for a LIMIT = CANDIDATES request (ef_search caps this)

The point is to (1) quantify how bad the old default (40) was, (2) find the smallest ef_search that removes
collapses and reaches ~1.0 recall, and (3) tell whether the query-time knob is enough or the index itself
needs rebuilding with a higher m / ef_construction.

    cd snomed-search && .venv/bin/python validation/hnsw_recall.py --corpus distemist --sample 100
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))               # run_corpus, run_search_eval
sys.path.insert(0, str(HERE.parent / "api"))  # embed_query

from run_corpus import CORPORA               # noqa: E402
from run_search_eval import load_mentions    # noqa: E402
from search import CANDIDATES, embed_query   # noqa: E402

load_dotenv(HERE.parent / ".env")
PG_DSN = os.environ["PG_DSN"]

EXACT_SQL = "SELECT id FROM descriptions ORDER BY embedding <=> %(qv)s::vector LIMIT %(k)s"
ANN_SQL = "SELECT id FROM descriptions ORDER BY embedding <=> %(qv)s::vector LIMIT %(cand)s"


def exact_top(cur, qv: str, k: int) -> list[int]:
    cur.execute("SET LOCAL enable_indexscan = off")
    cur.execute("SET LOCAL enable_bitmapscan = off")
    cur.execute(EXACT_SQL, {"qv": qv, "k": k})
    return [r[0] for r in cur.fetchall()]


def ann_rows(cur, qv: str, ef: int) -> list[int]:
    cur.execute("SET LOCAL enable_indexscan = on")
    cur.execute("SET LOCAL enable_bitmapscan = on")
    cur.execute(f"SET LOCAL hnsw.ef_search = {ef}")
    cur.execute(ANN_SQL, {"qv": qv, "cand": CANDIDATES})
    return [r[0] for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=sorted(CORPORA), default="distemist")
    ap.add_argument("--sample", type=int, default=100, help="mentions to test")
    ap.add_argument("--n-files", type=int, default=100000, help="draw mentions from the first N files")
    ap.add_argument("--efs", default="40,100,200,400", help="comma-separated ef_search values to sweep")
    ap.add_argument("--k", type=int, default=10, help="recall@k")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    efs = [int(x) for x in args.efs.split(",")]

    cfg = CORPORA[args.corpus]
    mentions = load_mentions(cfg, args.n_files)
    random.Random(args.seed).shuffle(mentions)
    mentions = mentions[: args.sample]
    spans = [span for _fn, span, _code in mentions]
    print(f"[{args.corpus}] {len(spans)} mentions · exact-vs-HNSW · recall@{args.k} · ef sweep {efs}")

    # pre-embed all queries once (BioLORD), reused across every ef value
    t0 = time.perf_counter()
    qvecs = [embed_query(s) for s in spans]  # embed_query already returns the pgvector '[...]' literal
    print(f"  embedded {len(qvecs)} queries in {time.perf_counter() - t0:.1f}s")

    per_ef = {ef: {"recalls": [], "rows": []} for ef in efs}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("LOAD 'vector'")
        for i, qv in enumerate(qvecs, 1):
            gold = set(exact_top(cur, qv, args.k))
            for ef in efs:
                rows = ann_rows(cur, qv, ef)
                hit = len(gold & set(rows[: args.k]))
                per_ef[ef]["recalls"].append(hit / len(gold) if gold else 0.0)
                per_ef[ef]["rows"].append(len(rows))
            if i % 25 == 0:
                print(f"  ... {i}/{len(qvecs)}")

    lines = [f"# HNSW recall diagnostic — {args.corpus.upper()}\n",
             f"Approximate (HNSW) vs exact (brute-force) nearest neighbours over {len(spans)} gold mentions.",
             f"recall@{args.k} = overlap with the exact top-{args.k}; a *collapse* is recall <= 0.2 (graph",
             "traversal stuck). `rows` = mean rows returned for a LIMIT = CANDIDATES request "
             f"({CANDIDATES}); ef_search caps it.\n",
             f"| ef_search | mean recall@{args.k} | collapses (<=0.2) | mean rows (of {CANDIDATES}) |",
             "|--:|--:|--:|--:|"]
    for ef in efs:
        rc = per_ef[ef]["recalls"]
        coll = sum(1 for r in rc if r <= 0.2)
        star = "  ← shipped" if ef == CANDIDATES else ""
        lines.append(f"| {ef}{star} | {statistics.mean(rc):.3f} | {coll} / {len(rc)} | "
                     f"{statistics.mean(per_ef[ef]['rows']):.0f} |")
    text = "\n".join(lines) + "\n"
    out = HERE / f"hnsw_recall_{args.corpus}.md"
    out.write_text(text, encoding="utf-8")
    print("\n" + text)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
