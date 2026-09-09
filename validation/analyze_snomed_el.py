#!/usr/bin/env python3
"""
Stratify a scope-by-type SNOMED EL run by clinical domain, and dump the discrepancies for a critical
review. One scope-by-type run (each mention scoped to its own domain) already contains every domain,
so the *finding* stratum is the DisTEMIST-comparable number and the others (procedure, body structure,
medication, …) come for free.

    cd snomed-search && .venv/bin/python validation/analyze_snomed_el.py \
        --json validation/results/snomed_el_search_eval_bytype_ppOFF_rrON.json

Writes results/<stem>_by_domain.md (stratified metrics + discrepancy summary) and
results/<stem>_discrepancies.csv (every non-exact@1 mention, for sorting/triage).
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

BROWSER = "https://browser.ihtsdotools.org/?perspective=full&conceptId1={}&edition=MAIN&languages=en"
DOMAIN_NAME = {404684003: "finding", 71388002: "procedure", 123037004: "body structure",
               763158003: "medication", 105590001: "substance", 410607006: "organism", None: "(unfiltered)"}


def domain_of(row) -> str:
    d = row.get("domain")
    return DOMAIN_NAME.get(d, str(d))


def metrics(rows) -> dict:
    n = len(rows) or 1
    hit1 = sum(1 for r in rows if r["rank"] == 1)
    hit5 = sum(1 for r in rows if r["rank"] and r["rank"] <= 5)
    hit10 = sum(1 for r in rows if r["rank"])
    h10 = sum(1 for r in rows if r["hier10"])
    rr = sum((1.0 / r["rank"]) if r["rank"] else 0.0 for r in rows)
    return {"n": len(rows), "acc@1": round(hit1 / n, 3), "recall@5": round(hit5 / n, 3),
            "recall@10": round(hit10 / n, 3), "MRR": round(rr / n, 3),
            "near@10": round((h10 - hit10) / n, 3)}


def category(r) -> str:
    if r["rank"] == 1:
        return "exact@1"
    if r["rank"] and r["rank"] <= 5:
        return "in top-5 (not #1)"
    if r["rank"] and r["rank"] <= 10:
        return "in top-10 (not top-5)"
    if r["hier10"]:
        return "hierarchy-adjacent (near-miss)"
    return "miss (not retrieved / cross-hierarchy)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    args = ap.parse_args()
    p = Path(args.json)
    data = json.loads(p.read_text(encoding="utf-8"))
    rows = data["rows"]
    stem = p.stem

    # --- stratify by domain ---
    by_dom = defaultdict(list)
    for r in rows:
        by_dom[domain_of(r)].append(r)
    L = [f"# SNOMED EL — stratified by clinical domain ({p.name})\n",
         "One field-scoped run, split by the gold's domain. The **finding** row is directly comparable to",
         "DisTEMIST (findings, filtered). *near@10* = extra fraction whose top-10 holds a parent/child.\n",
         "| domain | n | acc@1 | recall@5 | recall@10 | MRR | +near@10 |",
         "|---|--:|--:|--:|--:|--:|--:|"]
    for dom in sorted(by_dom, key=lambda d: -len(by_dom[d])):
        m = metrics(by_dom[dom])
        L.append(f"| **{dom}** | {m['n']} | {m['acc@1']} | {m['recall@5']} | {m['recall@10']} | "
                 f"{m['MRR']} | +{m['near@10']} |")
    mo = metrics(rows)
    L.append(f"| **ALL** | {mo['n']} | {mo['acc@1']} | {mo['recall@5']} | {mo['recall@10']} | "
             f"{mo['MRR']} | +{mo['near@10']} |")

    # --- discrepancy summary (non-exact@1) ---
    cats = defaultdict(list)
    for r in rows:
        if r["rank"] != 1:
            cats[category(r)].append(r)
    total_disc = sum(len(v) for v in cats.values())
    L += ["", "## Discrepancies (non-exact@1)\n",
          f"{total_disc} of {len(rows)} mentions are not exact at rank 1. By category:\n",
          "| category | count | share |", "|---|--:|--:|"]
    order = ["in top-5 (not #1)", "in top-10 (not top-5)", "hierarchy-adjacent (near-miss)",
             "miss (not retrieved / cross-hierarchy)"]
    for c in order:
        v = cats.get(c, [])
        L.append(f"| {c} | {len(v)} | {round(100*len(v)/(len(rows) or 1),1)}% |")
    L += ["", "### Sample of hard misses (rank None, no hierarchy credit)\n",
          "| span | gold | gold FSN | our top-1 | top-1 FSN |", "|---|---|---|---|---|"]
    hard = cats.get("miss (not retrieved / cross-hierarchy)", [])
    for r in hard[:40]:
        gid = r.get("resolved") or r["gold"]
        L.append(f"| {r['span'][:40]} | [{gid}]({BROWSER.format(gid)}) | {r['gold_fsn'] or '—'} | "
                 f"{('['+str(r['top1'])+']('+BROWSER.format(r['top1'])+')') if r['top1'] else '—'} | "
                 f"{r['top1_fsn'] or '—'} |")

    out_md = p.with_name(f"{stem}_by_domain.md")
    out_md.write_text("\n".join(L) + "\n", encoding="utf-8")

    # --- full discrepancy CSV (for triage/sorting) ---
    out_csv = p.with_name(f"{stem}_discrepancies.csv")
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "domain", "span", "gold", "resolved", "gold_fsn", "rank",
                    "category", "top1", "top1_fsn", "topk_fsns"])
        for r in rows:
            if r["rank"] == 1:
                continue
            w.writerow([r["file"], domain_of(r), r["span"], r["gold"], r.get("resolved") or "",
                        r["gold_fsn"] or "", r["rank"] or "", category(r), r["top1"] or "",
                        r["top1_fsn"] or "", " | ".join(f"{c}:{fs}" for c, fs in (r.get("topk") or []))])

    print("\n".join(L[:14]))
    print(f"\nwrote {out_md}\nwrote {out_csv}  ({total_disc} discrepancies)")


if __name__ == "__main__":
    main()
