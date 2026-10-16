#!/usr/bin/env python3
"""
Summarize the semantic-floor ablation: read the *_floor* result JSONs for a corpus and write a table of
the strict / hierarchy-aware metrics per variant, plus every mention whose outcome changed vs floor-off.

    cd snomed-search && .venv/bin/python validation/semantic_floor/summarize.py --corpus distemist
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"
VARIANTS = [("floor000", "floor off (SEM_MIN_SIM=0)"),
            ("floor080", "floor 0.80 on every query (rejected)"),
            ("floor080trunc", "**floor 0.80 only on truncated queries (served)**")]
METRICS = ["acc@1", "recall@5", "recall@10", "hier@1", "hier@10", "MRR"]


def load(corpus: str, tag: str) -> dict | None:
    p = RESULTS / f"{corpus}_search_eval_ppOFF_rrOFF_{tag}.json"
    return json.loads(p.read_text()) if p.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="distemist")
    args = ap.parse_args()
    data = {tag: load(args.corpus, tag) for tag, _ in VARIANTS}
    base = data["floor000"]
    if base is None:
        raise SystemExit(f"no _floor000 baseline for {args.corpus} in {RESULTS}")

    L = [f"# Semantic-floor ablation — {args.corpus.upper()}\n",
         "Search-only linking, pre-process OFF, rerank OFF, Clinical finding filter, k = 10. Only",
         "SEM_MIN_SIM / the truncation gate differ. See README.md for the rationale.\n",
         "| variant | " + " | ".join(METRICS) + " |",
         "|---|" + "--:|" * len(METRICS)]
    for tag, label in VARIANTS:
        d = data[tag]
        if d is None:
            L.append(f"| {label} | " + " | ".join("—" for _ in METRICS) + " |")
            continue
        L.append(f"| {label} | " + " | ".join(f"{d['summary'][m]:.3f}" for m in METRICS) + " |")

    b = {(r["span"], r["gold"]): r for r in base["rows"]}
    for tag, label in VARIANTS[1:]:
        if data[tag] is None:
            continue
        rows = {(r["span"], r["gold"]): r for r in data[tag]["rows"]}
        changed = [(k, b[k], rows[k]) for k in b if k in rows and b[k]["rank"] != rows[k]["rank"]]
        lost = sum(1 for _, x, y in changed if x["hit10"] and not y["hit10"])
        won = sum(1 for _, x, y in changed if y["hit10"] and not x["hit10"])
        L += [f"\n## {label.strip('*')} vs floor off\n",
              f"{len(changed)} mentions changed rank · lost@10 {lost} · won@10 {won}\n",
              "| mention | gold | rank off → on |", "|---|---|---|"]
        for (span, _), x, y in sorted(changed, key=lambda c: c[0][0].lower()):
            L.append(f"| {' '.join(span.split())[:60]} | {x['gold_fsn']} | {x['rank'] or '—'} → {y['rank'] or '—'} |")

    L.append(f"\n_n = {base['summary']['mentions']} mentions · gemma off._")
    text = "\n".join(L) + "\n"
    (HERE / f"summary_{args.corpus}.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {HERE / f'summary_{args.corpus}.md'}")


if __name__ == "__main__":
    main()
