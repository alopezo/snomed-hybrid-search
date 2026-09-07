#!/usr/bin/env python3
"""
Channel ablation: how much does the hybrid (lexical + semantic RRF fusion) buy over each channel
alone? All three configs share query pre-processing ON and rerank OFF; only the retrieval channel
changes. "both" is the existing pp-on/rr-off run (channel = both); "lexical"/"semantic" are the
ch* runs. Writes a side-by-side table.

    cd snomed-search && .venv/bin/python validation/compare_channels.py --corpus distemist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

# (label, filename stem) in the order we want them in the table. The served/best config
# (no query pre-processing, re-ranking on) is what we feature; the pp-on raw-channel runs
# remain on disk (chlexical/chsemantic_ppON_rrOFF) for the analysis in the discussion.
CONFIGS = [
    ("lexical only",   "{c}_search_eval_chlexical_ppOFF_rrON"),
    ("semantic only",  "{c}_search_eval_chsemantic_ppOFF_rrON"),
    ("both (fusion)",  "{c}_search_eval_ppOFF_rrON"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    args = ap.parse_args()

    rows = []
    for label, stem in CONFIGS:
        f = RES / f"{stem.format(c=args.corpus)}.json"
        if not f.exists():
            print(f"missing: {f.name} (run it first)")
            continue
        rows.append((label, json.loads(f.read_text(encoding="utf-8"))["summary"]))
    if not rows:
        print("no runs found"); return

    n = rows[0][1]["mentions"]
    L = [f"# {args.corpus.upper()} — retrieval-channel ablation\n",
         f"Search-only linking on the first-100-doc sample (n = {n} scoped mentions). All configs use the",
         "served best setting — **no query pre-processing, re-ranking on** — and vary only the retrieval",
         "channel. *both* is the RRF fusion of the two channels. Strict exact-concept metrics;",
         "*+near@10* is the near-miss increment (top-10 holds a parent/child); *=lin@10* = recall@10 +",
         "near-miss (same-lineage, optimistic).\n",
         "| channel | MRR | acc@1 | recall@5 | recall@10 | +near@10 | =lin@10 |",
         "|---|---|---|---|---|---|---|"]
    for label, s in rows:
        nm = round(s["hier@10"] - s["recall@10"], 3)
        L.append(f"| **{label}** | {s['MRR']} | {s['acc@1']} | {s['recall@5']} | {s['recall@10']} | "
                 f"+{nm} | {s['hier@10']} |")
    L.append("\n*DisTEMIST gold spans are complete, clean disease terms, so this corpus does not exercise "
             "the lexical channel's order-independent multi-prefix matching (partial tokens, clinician "
             "abbreviations); it therefore understates the lexical channel's value for interactive typed input.*")
    out = RES / f"{args.corpus}_channel_ablation.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
