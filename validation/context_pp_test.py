#!/usr/bin/env python3
"""
Quantify a CONTEXT-AWARE pre-process on the SNOMED EL hard misses.

A hard miss = a mention the search missed with the bare span (rank None, no hierarchy credit). For a
sample of them we compare, per mention (each scoped to its own domain, as in the field-scoped run):
  - isolated-pp : gemma normalizes the bare span (the current --gemma path), then search
  - context-pp  : gemma normalizes the span given its surrounding chunk (one call), then search
The dense EHR shorthand (AOx3, EOMI, CTAB, RRR, …) is ambiguous in isolation and gemma hallucinates
(MMM -> "myocardial infarction"); the hypothesis is that the local context disambiguates it.

    cd snomed-search && .venv/bin/python validation/context_pp_test.py \
        --json validation/results/snomed_el_search_eval_bytype_ppOFF_rrON.json --n 250
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import urllib.parse
from collections import defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
API = "http://127.0.0.1:8090"
LLM = "http://localhost:11434/v1/chat/completions"
D = HERE / "snomed_el"
import sys
sys.path.insert(0, str(HERE))
from run_search_eval import TAG_TO_FILTER   # noqa: E402


def context_pp(span: str, context: str) -> str:
    prompt = ("You are a clinical terminology assistant. Below is an excerpt from a clinical note. Using "
              "the surrounding context to disambiguate abbreviations and shorthand, rewrite the MENTION as "
              "ONE concise standard English clinical term (canonical concept name; expand abbreviations; "
              "keep the POSITIVE concept). Output only that term.\n\nExcerpt: " + context + "\n\nMENTION: " + span)
    try:
        j = httpx.post(LLM, json={"model": "gemma4:12b-it-qat", "messages": [{"role": "user", "content": prompt}],
                                  "temperature": 0, "max_tokens": 40, "reasoning_effort": "none"}, timeout=60).json()
        return j["choices"][0]["message"]["content"].strip()
    except Exception:
        return span


def search_rank(q: str, gold: str, filt, gemma: bool) -> int | None:
    q = " ".join(q.split())
    if not q:
        return None
    u = (f"{API}/api/search?q={urllib.parse.quote(q)}&k=10&gemma={'true' if gemma else 'false'}&rerank=true")
    if filt is not None:
        u += f"&filter={filt}"
    try:
        ids = [r["concept_id"] for r in httpx.get(u, timeout=90).json().get("results", [])]
    except Exception:
        return None
    return ids.index(gold) + 1 if gold in ids else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--window", type=int, default=160)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rows = json.loads(Path(args.json).read_text(encoding="utf-8"))["rows"]
    hard = [r for r in rows if r["rank"] is None and not r["hier10"]]

    # offsets for context + note texts
    csv.field_size_limit(10 ** 7)
    off: dict[tuple, tuple] = {}
    for r in csv.DictReader(open(D / "train_annotations.csv", newline="", encoding="utf-8")):
        k = (r["note_id"], r["span"].strip())
        off.setdefault(k, (int(float(r["start"])), int(float(r["end"]))))
    notes = {r["note_id"]: r["text"] for r in csv.DictReader(open(D / "train_notes.csv", newline="", encoding="utf-8"))}

    random.seed(args.seed)
    random.shuffle(hard)
    agg = {"n": 0, "iso@10": 0, "iso@1": 0, "ctx@10": 0, "ctx@1": 0}
    examples = []
    for r in hard:
        if agg["n"] >= args.n:
            break
        key = (r["file"], r["span"])
        if key not in off:
            continue
        s, e = off[key]
        context = notes[r["file"]][max(0, s - args.window): e + args.window].replace("\n", " ")
        gold = r.get("resolved") or r["gold"]
        filt = TAG_TO_FILTER.get(r.get("tag"))
        rk_iso = search_rank(r["span"], gold, filt, gemma=True)
        term = context_pp(r["span"], context)
        rk_ctx = search_rank(term, gold, filt, gemma=False)
        agg["n"] += 1
        agg["iso@10"] += int(bool(rk_iso)); agg["iso@1"] += int(rk_iso == 1)
        agg["ctx@10"] += int(bool(rk_ctx)); agg["ctx@1"] += int(rk_ctx == 1)
        if len(examples) < 30 and (rk_ctx and not rk_iso):
            examples.append((r["span"], term, rk_ctx, r["gold_fsn"]))
        if agg["n"] % 25 == 0:
            print(f"  {agg['n']}/{args.n}…  iso@10={agg['iso@10']} ctx@10={agg['ctx@10']}")

    n = agg["n"] or 1
    print("\n=== CONTEXT-PP on hard misses (n = {}) ===".format(agg["n"]))
    print(f"  isolated-pp  recovered @10: {agg['iso@10']} ({round(100*agg['iso@10']/n,1)}%)  @1: {agg['iso@1']}")
    print(f"  context-pp   recovered @10: {agg['ctx@10']} ({round(100*agg['ctx@10']/n,1)}%)  @1: {agg['ctx@1']}")
    print(f"  delta (context - isolated) @10: +{agg['ctx@10']-agg['iso@10']}")
    print("\n  examples context-pp recovered (isolated did not):")
    for span, term, rk, gfsn in examples[:15]:
        print(f"    {span!r:16} -> {term!r:34} @{rk}  (gold: {gfsn})")

    out = Path(args.json).with_name("snomed_el_context_pp.json")
    out.write_text(json.dumps({"summary": agg, "examples": examples}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
