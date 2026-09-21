#!/usr/bin/env python3
"""
Summarize the synonym-value ablation: read the *_synab result JSONs for a corpus and print (and write) a
table of acc@1 / recall@10 / MRR by (retrieval channel x description scope), with the two isolated deltas:
  * synonyms beyond PT = all - fsn_pt   (value of the extra acceptable synonyms)
  * preferred term      = fsn_pt - fsn  (value of the preferred term itself)

    cd snomed-search && .venv/bin/python validation/synonym_ablation/summarize.py --corpus distemist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"
SCOPES = ["all", "fsn_pt", "fsn"]


def load(corpus: str) -> dict:
    """(channel, rerank, desc_scope) -> summary, from every {corpus}_search_eval_*_synab.json."""
    out = {}
    for p in RESULTS.glob(f"{corpus}_search_eval_*_synab.json"):
        c = json.loads(p.read_text())["summary"]
        cfg = c["config"]
        out[(cfg.get("channel", "both"), cfg["rerank"], cfg.get("desc_scope", "all"))] = c
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="distemist")
    args = ap.parse_args()
    data = load(args.corpus)
    if not data:
        raise SystemExit(f"no *_synab results for {args.corpus} in {RESULTS}")

    rows = [("semantic", "false", "semantic channel (rerank off)"),
            ("lexical", "false", "lexical channel (rerank off)"),
            ("both", "true", "fused, served (rerank on)")]
    L = [f"# Synonym-value ablation — {args.corpus.upper()}\n",
         "How much do SNOMED CT synonyms add to retrieval, isolated per channel. *desc scope*: **all** = FSN",
         "+ every synonym; **fsn_pt** = FSN + preferred term; **fsn** = FSN only. Each block shows acc@1 /",
         "recall@10 / MRR at the three scopes, then the two isolated contributions.\n",
         "| channel / config | metric | all | fsn+PT | fsn only | Δ synonyms>PT (all−fsn_pt) | Δ PT (fsn_pt−fsn) |",
         "|---|---|--:|--:|--:|--:|--:|"]
    for ch, rr, label in rows:
        s = {ds: data.get((ch, rr, ds)) for ds in SCOPES}
        if not all(s.values()):
            L.append(f"| **{label}** | — | (missing runs) | | | | |")
            continue
        for m in ["acc@1", "recall@10", "MRR"]:
            v = {ds: s[ds][m] for ds in SCOPES}
            dsyn = round(v["all"] - v["fsn_pt"], 3)
            dpt = round(v["fsn_pt"] - v["fsn"], 3)
            first = f"**{label}**" if m == "acc@1" else ""
            L.append(f"| {first} | {m} | {v['all']:.3f} | {v['fsn_pt']:.3f} | {v['fsn']:.3f} | "
                     f"{dsyn:+.3f} | {dpt:+.3f} |")
    n = next(iter(data.values()))["mentions"]
    L.append(f"\n_n = {n} mentions · gemma off · LOINC-excluded._")
    text = "\n".join(L) + "\n"
    (HERE / f"summary_{args.corpus}.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {HERE / f'summary_{args.corpus}.md'}")


if __name__ == "__main__":
    main()
