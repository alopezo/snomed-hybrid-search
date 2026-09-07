#!/usr/bin/env python3
"""
A/B a semantic encoder WITHOUT touching the running server or the production BioLORD column.

For every gold DisTEMIST mention it embeds the span with the chosen model and runs a *semantic-only*
ANN over the chosen vector column (e.g. the production `embedding` = BioLORD, or an experiment column
`embedding_bge` = BGE-M3), then scores the rank of the gold SNOMED concept. This mirrors the
semantic-only row of the channel ablation, so results are directly comparable to
`distemist_search_eval_chsemantic_ppOFF_rrON` (BioLORD semantic-only: acc@1 0.607).

    # BioLORD baseline (existing production column):
    cd snomed-search && .venv/bin/python validation/eval_semantic_backend.py --corpus distemist --n 100 \
        --model FremyCompany/BioLORD-2023-M --column embedding

    # BGE-M3 experiment (after encoding into embedding_bge):
    cd snomed-search && EMBED_DEVICE=mps .venv/bin/python validation/eval_semantic_backend.py \
        --corpus distemist --n 100 --model BAAI/bge-m3 --column embedding_bge

    cd snomed-search && .venv/bin/python validation/eval_semantic_backend.py --compare
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
load_dotenv(HERE.parent / ".env")
import sys
sys.path.insert(0, str(HERE))
from run_corpus import CORPORA, hierarchy_hits          # noqa: E402
from run_search_eval import load_mentions               # noqa: E402
import history                                           # noqa: E402


def vec_to_pg(vec) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def run(args) -> None:
    scope = None if args.scope.lower() == "all" else {t.strip() for t in args.scope.split(",")}
    cfg = CORPORA[args.corpus]
    dsn = os.environ["PG_DSN"]
    filt = None if str(args.filter).lower() == "none" else int(args.filter)
    RES.mkdir(exist_ok=True)

    from sentence_transformers import SentenceTransformer  # noqa: WPS433
    device = os.environ.get("EMBED_DEVICE", "cpu")
    print(f"Loading {args.model} (device={device}) · column descriptions.{args.column}")
    model = SentenceTransformer(args.model, device=device)

    conn = psycopg.connect(dsn); cur = conn.cursor()
    if filt is not None:
        cur.execute("SET hnsw.iterative_scan = relaxed_order")

    ann = (f"SELECT d.concept_id FROM descriptions d "
           f"WHERE d.{args.column} IS NOT NULL "
           + ("AND EXISTS (SELECT 1 FROM concept_ancestors ca WHERE ca.concept_id=d.concept_id "
              "AND ca.ancestors @> ARRAY[%(filt)s::bigint]) " if filt is not None else "")
           + f"ORDER BY d.{args.column} <=> %(qv)s::vector LIMIT 300")

    mentions = load_mentions(cfg, args.n)
    assoc = history.load_assoc({c for _, _, c in mentions})
    print(f"[{args.corpus}] SEMANTIC-only eval on {len(mentions)} mentions "
          f"(model={args.model} column={args.column} filter={filt} k={args.k} scope={args.scope})\n")

    agg = {"total": 0, "scoped_out": 0, "resolved": 0, "hit1": 0, "hit5": 0, "hit10": 0,
           "h1": 0, "h5": 0, "h10": 0, "rr": 0.0}
    t0 = time.time()
    for i, (fn, span, code) in enumerate(mentions, 1):
        rcode, tag = history.resolve(cur, code, assoc)
        if scope is not None and (tag is None or tag not in scope):
            agg["scoped_out"] += 1
            continue
        gold = rcode
        qv = vec_to_pg(model.encode([span], normalize_embeddings=True)[0])
        params = {"qv": qv} if filt is None else {"qv": qv, "filt": filt}
        cur.execute(ann, params)
        seen, concepts = set(), []
        for (cid,) in cur.fetchall():
            cid = str(cid)                       # gold codes are strings; DB concept_id is bigint
            if cid not in seen:
                seen.add(cid); concepts.append(cid)
        top = concepts[: args.k]
        rank = top.index(gold) + 1 if gold in top else None
        h1 = bool(hierarchy_hits({gold}, set(top[:1]), dsn))
        h5 = bool(hierarchy_hits({gold}, set(top[:5]), dsn))
        h10 = bool(hierarchy_hits({gold}, set(top[:args.k]), dsn))
        agg["total"] += 1
        agg["resolved"] += int(rcode != code)
        agg["hit1"] += int(rank == 1)
        agg["hit5"] += int(bool(rank and rank <= 5))
        agg["hit10"] += int(bool(rank))
        agg["h1"] += int(h1); agg["h5"] += int(h5); agg["h10"] += int(h10)
        agg["rr"] += (1.0 / rank) if rank else 0.0
        if i % 50 == 0:
            print(f"  {i}/{len(mentions)}…")
    conn.close()

    n = agg["total"] or 1
    r = lambda x: round(x / n, 3)
    summary = {
        "corpus": args.corpus, "model": args.model, "column": args.column,
        "config": {"filter": filt, "k": args.k, "scope": args.scope},
        "mentions": agg["total"], "scoped_out": agg["scoped_out"], "resolved_via_history": agg["resolved"],
        "elapsed_s": round(time.time() - t0, 1),
        "acc@1": r(agg["hit1"]), "recall@5": r(agg["hit5"]), "recall@10": r(agg["hit10"]),
        "hier@1": r(agg["h1"]), "hier@5": r(agg["h5"]), "hier@10": r(agg["h10"]), "MRR": r(agg["rr"]),
    }
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", args.model)
    out = RES / f"{args.corpus}_sembackend_{slug}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\nSaved {out}")


def compare() -> None:
    files = sorted(glob.glob(str(RES / "*_sembackend_*.json")))
    runs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]
    if not runs:
        print("no *_sembackend_*.json yet"); return
    print(f"{'model':30} {'column':16} {'acc@1':>6} {'r@5':>6} {'r@10':>6} {'MRR':>6} {'near@10':>8}")
    for s in runs:
        nm = round(s["hier@10"] - s["recall@10"], 3)
        print(f"{s['model'][:30]:30} {s['column']:16} {s['acc@1']:>6} {s['recall@5']:>6} "
              f"{s['recall@10']:>6} {s['MRR']:>6} {('+'+str(nm)):>8}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=sorted(CORPORA), default="distemist")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--model", default=os.environ.get("EMBED_MODEL", "FremyCompany/BioLORD-2023-M"))
    ap.add_argument("--column", default="embedding")
    ap.add_argument("--filter", default="404684003")   # Clinical finding; "none" to disable
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--scope", default="disorder,finding")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.compare:
        compare()
    else:
        run(args)


if __name__ == "__main__":
    main()
