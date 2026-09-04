#!/usr/bin/env python3
"""
Build a per-mention Markdown report for a validation run: for every gold mention, show the source
text, the gold SNOMED code and our mapped code (both linked to the SNOMED International browser), and
a note characterizing the relationship (exact / parent / child / representation mismatch / not
extracted / different concept). Reads the JSON produced by run_corpus.py plus the gold TSV.

    cd snomed-search && .venv/bin/python validation/report.py --corpus distemist
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
import sys
sys.path.insert(0, str(HERE))
from run_corpus import CORPORA, load_gold  # noqa: E402

BROWSER = "https://browser.ihtsdotools.org/?perspective=full&conceptId1={}&edition=MAIN&languages=en"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def align(span: str, entities: list[dict]) -> dict | None:
    """Find our entity whose verbatim text best matches the gold span (substring, else word overlap)."""
    s = norm(span)
    best, score = None, 0.0
    sw = set(s.split())
    for e in entities:
        t = norm(e.get("text") or "")
        if not t:
            continue
        if t in s or s in t:
            ov = min(len(t), len(s)) / max(len(t), len(s))
            if ov > score:
                best, score = e, ov
        else:
            j = len(sw & set(t.split())) / max(1, len(sw | set(t.split())))
            if j > score and j >= 0.5:
                best, score = e, j
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=sorted(CORPORA), required=True)
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    cfg = CORPORA[args.corpus]
    res = json.loads((HERE / "results" / f"{args.corpus}_first{args.n}.json").read_text(encoding="utf-8"))
    files = {f["file"]: f for f in res["files"]}
    gold = load_gold(cfg, args.n)

    conn = psycopg.connect(os.environ["PG_DSN"])
    cur = conn.cursor()

    def info(code: str):
        """FSN + semantic tag for a concept id; (None, None) if absent from our release."""
        if not code or not code.isdigit():
            return None, None
        cur.execute("SELECT term FROM descriptions WHERE concept_id=%s AND type_id=900000000000003001 LIMIT 1",
                    (int(code),))
        r = cur.fetchone()
        if not r:
            return None, None
        m = re.search(r"\(([^)]+)\)\s*$", r[0])
        return r[0], (m.group(1) if m else "")

    def ancestors(code: str) -> set[int]:
        cur.execute("SELECT ancestors FROM concept_ancestors WHERE concept_id=%s", (int(code),))
        r = cur.fetchone()
        return set(r[0]) if r else set()

    def link(code: str) -> str:
        return f"[{code}]({BROWSER.format(code)})" if code and code.isdigit() else (code or "—")

    rows = []
    for fn, mentions in gold.items():
        pf = files.get(fn, {})
        ents = pf.get("entities", [])
        topk = {c for e in ents for c in e.get("topk", [])}
        for span, gcode in mentions:
            gfsn, gtag = info(gcode)
            al = align(span, ents)
            ocode = (al or {}).get("best")
            ofsn, otag = info(ocode) if ocode else (None, None)
            # note + category
            if not gcode.isdigit():
                note, cat = f"gold is post-coordinated ({gcode})", "gold composite"
                ocode, ofsn = None, None                       # text alignment is meaningless here
            elif gfsn is None:
                note, cat = "gold code not in our release", "gold not in release"
            elif al is None:
                note = "not extracted here; gold recovered by another entity" if gcode in topk else "not extracted"
                cat = "not extracted"
            elif ocode == gcode:
                note, cat = "exact", "exact"
            else:
                ag, ao = ancestors(gcode), (ancestors(ocode) if ocode else set())
                if ocode and int(ocode) in ag:
                    note, cat = "we returned a parent (more general)", "parent"
                elif ocode and int(gcode) in ao:
                    note, cat = "we returned a child (more specific)", "child"
                elif gtag and otag and gtag != otag:
                    note, cat = f"representation mismatch: gold *{gtag}* vs our *{otag}*", "representation mismatch"
                elif gcode in topk:
                    note, cat = "different aligned concept; gold in our top-5 elsewhere", "different concept"
                else:
                    note, cat = "different concept", "different concept"
            rows.append((fn, span, gcode, gfsn, ocode, ofsn, note, cat))

    # ---- write markdown ----
    s = res["summary"]
    from collections import Counter
    tally = Counter(cat for *_, cat in rows)
    order = ["exact", "child", "parent", "representation mismatch", "different concept",
             "not extracted", "gold composite", "gold not in release"]
    tally_line = " · ".join(f"**{tally[c]}** {c}" for c in order if tally.get(c))
    L = [f"# {args.corpus.upper()} — per-mention report (first {args.n} cases)\n",
         f"Gold mentions: **{len(rows)}**. Strict recall (top-5) {s['recall_topk_over_gold']}, "
         f"hierarchy-aware {s['recall_topk_hier_over_gold']}. Codes link to the SNOMED International browser.\n",
         f"**By category:** {tally_line}\n",
         "> *exact*/*child*/*parent* count as hierarchy-aware hits (child/parent = same IS-A lineage).",
         "> *representation mismatch* = same clinical thing modelled under a different hierarchy",
         "> (e.g. a *substance*/*morphologic abnormality* concept vs a *disorder*); not credited by either metric.\n",
         "| # | file | source text (gold span) | gold code | gold FSN | our code | our FSN | note |",
         "|---|---|---|---|---|---|---|---|"]
    for i, (fn, span, gc, gf, oc, of, note, cat) in enumerate(rows, 1):
        L.append(f"| {i} | `{fn[-6:]}` | {span} | {link(gc)} | {gf or '—'} | {link(oc)} | {of or '—'} | {note} |")
    out = HERE / "results" / f"{args.corpus}_report.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    conn.close()
    print(f"wrote {out}  ({len(rows)} mentions)")


if __name__ == "__main__":
    main()
