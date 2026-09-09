#!/usr/bin/env python3
"""
Full-corpus CONTEXT-AWARE pre-process over the SNOMED EL challenge, field-scoped.

Same population, domains and metrics as the baseline field-scoped run (Table 3), but instead of
searching the bare span, each mention is normalized by a single LLM call that sees the span *within its
note chunk*, then searched (gemma off, rerank on) under its domain filter. The delta vs the baseline is
the whole-corpus lift of a context-aware pre-process. Long (~hours): run it in a terminal, not monitored.

    cd snomed-search && .venv/bin/python validation/run_context_pp_full.py            # all 272 notes
    cd snomed-search && .venv/bin/python validation/run_context_pp_full.py --resume    # continue a run

Writes results/snomed_el_search_eval_bytype_ctxpp_ppctx_rrON.json (summary + rows, same schema as
run_search_eval, so analyze_snomed_el.py works on it), checkpointing every --checkpoint mentions.
"""
from __future__ import annotations

import argparse
import csv
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
LLM = os.environ.get("GEMMA_URL", "http://localhost:11434/v1") + "/chat/completions"
MODEL = os.environ.get("GEMMA_MODEL", "gemma4:12b-it-qat")
load_dotenv(HERE.parent / ".env")
import sys
sys.path.insert(0, str(HERE))
from run_corpus import CORPORA, hierarchy_hits           # noqa: E402
from run_search_eval import TAG_TO_FILTER                # noqa: E402
import history                                           # noqa: E402

OUT = HERE / "results" / "snomed_el_search_eval_bytype_ctxpp_ppctx_rrON.json"


def load_mentions_with_ctx(n: int, window: int) -> list[dict]:
    """Deduped (span, code) mentions over the first n notes, each with a context window from its first
    occurrence (matches the baseline population, plus the surrounding text needed for context-pp)."""
    cfg = CORPORA["snomed_el"]
    notes = {r["note_id"]: r["text"] for r in csv.DictReader(open(cfg["notes"], newline="", encoding="utf-8"))}
    keep = set(sorted(notes)[:n])
    seen, out = set(), []
    csv.field_size_limit(10 ** 7)
    for r in csv.DictReader(open(cfg["csv"], newline="", encoding="utf-8")):
        nid = r["note_id"]
        if nid not in keep:
            continue
        code = r["concept_id"].strip()
        if not code.isdigit():
            continue
        key = (" ".join(r["span"].split()).lower(), code)
        if key in seen:
            continue
        seen.add(key)
        s, e = int(float(r["start"])), int(float(r["end"]))
        ctx = notes[nid][max(0, s - window): e + window].replace("\n", " ")
        out.append({"file": nid, "span": r["span"].strip(), "code": code, "context": ctx})
    return out


def context_pp(span: str, context: str) -> str:
    prompt = ("You are a clinical terminology assistant. Below is an excerpt from a clinical note. Using "
              "the surrounding context to disambiguate abbreviations and shorthand, rewrite the MENTION as "
              "ONE concise standard English clinical term (canonical concept name; expand abbreviations; "
              "keep the POSITIVE concept). Output only that term.\n\nExcerpt: " + context + "\n\nMENTION: " + span)
    try:
        j = httpx.post(LLM, json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                                  "temperature": 0, "max_tokens": 40, "reasoning_effort": "none"}, timeout=60).json()
        return j["choices"][0]["message"]["content"].strip()
    except Exception:
        return span


def search(q: str, filt, k: int) -> list[str]:
    q = " ".join(q.split())
    if not q:
        return []
    u = f"{API}/api/search?q={urllib.parse.quote(q)}&k={k}&gemma=false&rerank=true"
    if filt is not None:
        u += f"&filter={filt}"
    try:
        r = httpx.get(u, timeout=90); r.raise_for_status()
        return [x["concept_id"] for x in r.json().get("results", [])]
    except Exception as e:
        print(f"    [search error on {q!r}: {str(e)[:70]}]")
        return []


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    hit1 = sum(1 for r in rows if r["rank"] == 1)
    hit5 = sum(1 for r in rows if r["rank"] and r["rank"] <= 5)
    hit10 = sum(1 for r in rows if r["rank"])
    h10 = sum(1 for r in rows if r["hier10"])
    rr = sum((1.0 / r["rank"]) if r["rank"] else 0.0 for r in rows)
    return {"corpus": "snomed_el", "config": {"pre-process": "context-aware", "rerank": "true",
            "filter": "by-type", "k": 10, "scope": "all"}, "mentions": len(rows),
            "acc@1": round(hit1 / n, 3), "recall@5": round(hit5 / n, 3), "recall@10": round(hit10 / n, 3),
            "hier@10": round(h10 / n, 3), "MRR": round(rr / n, 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=272)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--window", type=int, default=160)
    ap.add_argument("--checkpoint", type=int, default=200)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    dsn = os.environ["PG_DSN"]
    (HERE / "results").mkdir(exist_ok=True)

    mentions = load_mentions_with_ctx(args.n, args.window)
    assoc = history.load_assoc({m["code"] for m in mentions})

    rows, done = [], set()
    if args.resume and OUT.exists():
        prev = json.loads(OUT.read_text(encoding="utf-8"))
        rows = prev.get("rows", [])
        done = {(r["file"], r["span"], r["gold"]) for r in rows}
        print(f"resuming: {len(rows)} mentions already scored")

    conn = psycopg.connect(dsn); cur = conn.cursor()

    def fsn(code):
        cur.execute("SELECT term FROM descriptions WHERE concept_id=%s AND type_id=900000000000003001 LIMIT 1",
                    (int(code),))
        r = cur.fetchone(); return r[0] if r else None

    todo = [m for m in mentions if (m["file"], m["span"], m["code"]) not in done]
    print(f"[context-pp full] {len(todo)} mentions to score (of {len(mentions)}); model={MODEL}\n")
    t0 = time.time()
    for i, m in enumerate(todo, 1):
        rcode, tag = history.resolve(cur, m["code"], assoc)
        if rcode is None:
            continue
        gold = rcode
        filt = TAG_TO_FILTER.get(tag)
        term = context_pp(m["span"], m["context"])
        ids = search(term, filt, args.k)
        rank = ids.index(gold) + 1 if gold in ids else None
        h10 = bool(hierarchy_hits({gold}, set(ids[:args.k]), cur=cur))
        rows.append({"file": m["file"], "span": m["span"], "gold": m["code"],
                     "resolved": (rcode if rcode != m["code"] else None), "tag": tag,
                     "domain": filt, "gold_fsn": fsn(gold), "ctx_term": term, "rank": rank,
                     "top1": ids[0] if ids else None, "hier10": h10})
        if i % 25 == 0:
            rate = i / (time.time() - t0); eta = (len(todo) - i) / rate / 60
            print(f"  {i}/{len(todo)}  ({rate:.1f}/s, ETA {eta:.0f} min)  acc@1={summarize(rows)['acc@1']}")
        if i % args.checkpoint == 0:
            OUT.write_text(json.dumps({"summary": summarize(rows), "rows": rows}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    conn.close()

    OUT.write_text(json.dumps({"summary": summarize(rows), "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    s = summarize(rows)
    print("\n=== CONTEXT-PP FULL SUMMARY ===")
    for k, v in s.items():
        print(f"  {k}: {v}")
    print(f"\nSaved {OUT}\nCompare to baseline (Table 3): acc@1 0.52 / recall@10 0.73")


if __name__ == "__main__":
    main()
