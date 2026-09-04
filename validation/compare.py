#!/usr/bin/env python3
"""
Compare the search-only evaluation across configurations. Reads every
results/<corpus>_search_eval_pp*_rr*.json and writes a side-by-side comparison table.

    cd snomed-search && .venv/bin/python validation/compare.py --corpus distemist
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    args = ap.parse_args()

    files = sorted(glob.glob(str(RES / f"{args.corpus}_search_eval_pp*_rr*.json")))
    runs = [json.loads(Path(f).read_text(encoding="utf-8"))["summary"] for f in files]
    if not runs:
        print("no runs found"); return

    def label(s):
        c = s["config"]
        return f"pp {'ON' if c['gemma']=='true' else 'OFF'} / rr {'ON' if c['rerank']=='true' else 'OFF'}"

    n = runs[0]["mentions"]
    L = [f"# {args.corpus.upper()} search-only — configuration comparison\n",
         f"Search-only normalization on the first-100-doc sample (n = {n} scoped mentions; gemma query",
         "normalization and cross-encoder rerank toggled). Gold resolved via historical associations and",
         "scoped to disorder/finding (see the per-config reports for methodology).\n",
         "| config | acc@1 | recall@5 | recall@10 | MRR | near-miss@10 |",
         "|---|---|---|---|---|---|"]
    for s in runs:
        nm = round(s["hier@10"] - s["recall@10"], 3)
        L.append(f"| **{label(s)}** | {s['acc@1']} | {s['recall@5']} | {s['recall@10']} | "
                 f"{s['MRR']} | +{nm} |")
    L.append("\n*Strict exact-concept metrics; recall@5/@10 = the picker-list framing (gold on the short "
             "list the user sees). near-miss@10 = extra fraction whose top-10 holds a parent/child of the gold.*")
    out = RES / f"{args.corpus}_search_eval_comparison.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
