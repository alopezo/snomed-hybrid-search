#!/usr/bin/env python3
"""
END-TO-END evaluation on the SNOMED CT Entity Linking Challenge (DrivenData / PhysioNet, MIMIC-IV notes).

Unlike the search-only harness (validation/run_search_eval.py), which feeds the *gold* mention span to the
search engine and isolates normalization, this runner scores the WHOLE pipeline on the raw note:

    note text --/api/extract--> entities --/api/search (best concept)--> (span, concept)
              --project to char offsets--> (start, end, concept_id) predictions
              --official character-IoU (official_iou.py) vs gold--> macro IoU

This mirrors the challenge's own task and its official metric, so the number is comparable *in magnitude*
to the public leaderboard (winners ~0.42 private / ~0.44 public). Two caveats keep it from being a
head-to-head: (1) we score the released TRAIN annotations (the leaderboard's test gold is private), and
(2) this is zero-shot (no training on the corpus), whereas the leaderboard entries are fine-tuned NER.

    cd snomed-search && .venv/bin/python validation/snomed_el_e2e/run_e2e.py --n 25
    # full corpus (slow, LLM-bound): --n 272

Search config defaults to the manuscript's interactive serving configuration for this corpus:
gemma query pre-processing OFF, re-ranking ON.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

import httpx
import psycopg
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
VALID = HERE.parent
API = os.environ.get("VALIDATION_API", "http://127.0.0.1:8090")
load_dotenv(VALID.parent / ".env")
sys.path.insert(0, str(VALID))
from run_corpus import CORPORA, extract  # noqa: E402  (reuse the extract client + corpus config)
import history  # noqa: E402
from snomed_el_e2e.official_iou import score as iou_score  # noqa: E402

BROWSER = "https://browser.ihtsdotools.org/?perspective=full&conceptId1={}&edition=MAIN&languages=en"

# Public leaderboard (challenge test split; character-IoU) for context in the report.
LEADERBOARD = [("1st — KIRIs", 0.4452, 0.4202), ("2nd — SNOBERT", 0.4447, 0.4194),
               ("3rd — MITEL-UNIUD", 0.4065, 0.3777)]


def load_notes(cfg: dict) -> dict[str, str]:
    import csv
    csv.field_size_limit(10 ** 7)
    with open(cfg["notes"], newline="", encoding="utf-8") as f:
        return {r["note_id"]: r["text"] for r in csv.DictReader(f)}


def load_gold_annotations(cfg: dict) -> dict[str, list[tuple[int, int, str]]]:
    """note_id -> [(start, end, concept_id)] straight from the annotations CSV (keeps offsets, which
    run_corpus.load_gold drops). Only single numeric codes are kept."""
    import csv
    csv.field_size_limit(10 ** 7)
    out: dict[str, list[tuple[int, int, str]]] = {}
    with open(cfg["csv"], newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r["concept_id"].strip()
            if not code.isdigit():
                continue
            out.setdefault(r["note_id"], []).append((int(float(r["start"])), int(float(r["end"])), code))
    return out


def project_spans(text: str, ents: list[tuple[str, int]]) -> tuple[list[tuple[int, int, int]], int]:
    """Recover character offsets for LLM-extracted spans by locating each verbatim span in the note.
    The server deduplicates spans across chunks, losing multiplicity, so we mark ALL non-overlapping
    literal occurrences of each span; on overlap the longer span claims the characters first. Returns
    (annotations, n_unlocated) where n_unlocated is the count of (span, concept) pairs never found."""
    occ = []
    unlocated = 0
    for span, cid in ents:
        span = span.strip()
        if not span:
            continue
        hits = list(re.finditer(re.escape(span), text))
        if not hits:
            unlocated += 1
            continue
        for m in hits:
            occ.append((m.start(), m.end(), cid, m.end() - m.start()))
    occ.sort(key=lambda o: (-o[3], o[0]))          # longer spans win contested characters
    claimed = bytearray(len(text))
    out = []
    for s, e, cid, _ in occ:
        if any(claimed[s:e]):
            continue
        for i in range(s, e):
            claimed[i] = 1
        out.append((s, e, cid))
    return out, unlocated


def search(q: str, filt, gemma: str, rerank: str, k: int,
           llm_select: bool = False, context: str = "") -> list[dict]:
    q = " ".join(q.split())
    if not q:
        return []
    u = (f"{API}/api/search?q={urllib.parse.quote(q)}&k={k}&gemma={gemma}&rerank={rerank}")
    if filt is not None:
        u += f"&filter={filt}"
    if llm_select:
        u += f"&llm_select=true&context={urllib.parse.quote(context)}"
    try:
        r = httpx.get(u, timeout=120)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        print(f"    [search error on {q!r}: {str(e)[:80]}]")
        return []


def local_context(note: str, span: str, section: str) -> str:
    """Context string for the LLM selection layer: the mention's section + the sentence it sits in."""
    i = note.find(span)
    if i < 0:
        return f"Section: {section or 'note'}."
    start = max(note.rfind(".", 0, i), note.rfind("\n", 0, i)) + 1
    end = note.find(".", i + len(span))
    sent = " ".join(note[start:(end if end > 0 else i + len(span) + 80)].split())
    return f"Section: {section or 'note'}. Sentence: {sent[:300]}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25, help="number of notes (sorted by note_id); 272 = full")
    ap.add_argument("--gemma", default="false")   # manuscript interactive config for this corpus
    ap.add_argument("--rerank", default="true")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--extractor", choices=["production", "exhaustive"], default="production",
                    help="production = /api/extract (selective, unchanged); exhaustive = two-pass "
                         "detect+typify module (validation/snomed_el_e2e/exhaustive_extract.py)")
    ap.add_argument("--llm-select", action="store_true",
                    help="add the context-aware LLM selection layer (#2) over each entity's search "
                         "top-k, using its section + sentence as context, instead of taking top-1")
    args = ap.parse_args()

    if args.extractor == "exhaustive":
        from snomed_el_e2e import exhaustive_extract
        extract_fn = exhaustive_extract.extract
        extractor_model = exhaustive_extract.EXHAUSTIVE_MODEL
    else:
        extract_fn = extract                       # run_corpus.extract -> /api/extract
        extractor_model = "api/extract (production)"
    cfg = CORPORA["snomed_el"]
    dsn = os.environ["PG_DSN"]
    (HERE / "results").mkdir(exist_ok=True)

    notes = load_notes(cfg)
    gold_all = load_gold_annotations(cfg)
    sel = sorted(notes)[:args.n]                    # stable order, aligned with run_corpus.load_gold

    conn = psycopg.connect(dsn)
    cur = conn.cursor()

    # Type -> hierarchy filter (same map the server exposes; import lazily to avoid a hard dep on api/).
    try:
        sys.path.insert(0, str(VALID.parent))
        from api.search import TYPE_TO_HIERARCHY
    except Exception:
        TYPE_TO_HIERARCHY = {"finding": 404684003, "procedure": 71388002, "body structure": 123037004,
                             "medication": 763158003, "substance": 105590001}

    # Resolve every gold code once through historical associations (edition-drift correction), caching.
    all_codes = {c for n in sel for _, _, c in gold_all.get(n, [])}
    assoc = history.load_assoc(all_codes)
    rcache: dict[str, str | None] = {}

    def resolve_gold(code: str) -> str | None:
        if code not in rcache:
            rcache[code] = history.resolve(cur, code, assoc)[0]
        return rcache[code]

    gold_ann: list[tuple[str, int, int, str]] = []
    n_gold_raw = n_gold_dropped = 0
    for nid in sel:
        for s, e, c in gold_all.get(nid, []):
            n_gold_raw += 1
            rc = resolve_gold(c)
            if rc is None:                          # inactive gold with no successor in our release
                n_gold_dropped += 1
                continue
            gold_ann.append((nid, s, e, rc))

    print(f"[snomed_el E2E] {len(sel)} notes | extractor={args.extractor} ({extractor_model}) | "
          f"gold mentions {n_gold_raw} (dropped {n_gold_dropped} unresolved) | "
          f"search gemma={args.gemma} rerank={args.rerank} k={args.k}\n")

    pred_ann: list[tuple[str, int, int, str]] = []
    per_note = []
    n_entities = n_mapped = n_unlocated = 0
    t0 = time.time()
    for i, nid in enumerate(sel, 1):
        text = notes[nid]
        try:
            ex = extract_fn(text)
        except Exception as e:
            print(f"  [{i}/{len(sel)}] {nid}: extract failed ({str(e)[:60]}) — 0 entities")
            ex = {"entities": []}
        ents_raw = ex.get("entities", [])
        span_code: list[tuple[str, int]] = []
        for ent in ents_raw:
            q = (ent.get("clinicalTerm") or ent.get("text") or "").strip()
            span = (ent.get("text") or "").strip()
            if not q or not span:
                continue
            filt = TYPE_TO_HIERARCHY.get(ent.get("type"))
            ctx = local_context(text, span, ent.get("section", "")) if args.llm_select else ""
            res = search(q, filt, args.gemma, args.rerank, args.k, args.llm_select, ctx)
            if filt is not None and not res:
                res = search(q, None, args.gemma, args.rerank, args.k, args.llm_select, ctx)
            if res:
                span_code.append((span, int(res[0]["concept_id"])))   # res[0] = LLM pick when --llm-select
        proj, unloc = project_spans(text, span_code)
        for s, e, cid in proj:
            pred_ann.append((nid, s, e, str(cid)))
        n_entities += len(ents_raw)
        n_mapped += len(span_code)
        n_unlocated += unloc
        per_note.append({"note_id": nid, "entities": len(ents_raw), "mapped": len(span_code),
                         "projected": len(proj), "unlocated": unloc,
                         "gold_mentions": len(gold_all.get(nid, []))})
        print(f"  [{i}/{len(sel)}] {nid}: {len(ents_raw)} entities -> {len(span_code)} mapped "
              f"-> {len(proj)} projected (unlocated {unloc}) | gold {len(gold_all.get(nid, []))}")

    s = iou_score(pred_ann, gold_ann)
    summary = {
        "corpus": "snomed_el", "split": "train", "notes": len(sel),
        "extractor": args.extractor, "extractor_model": extractor_model,
        "config": {"gemma": args.gemma, "rerank": args.rerank, "k": args.k,
                   "llm_select": args.llm_select,
                   "sections": (args.extractor == "exhaustive"
                                and os.environ.get("EXHAUSTIVE_SECTIONS", "1") != "0")},
        "gold_mentions_raw": n_gold_raw, "gold_dropped_unresolved": n_gold_dropped,
        "entities_extracted": n_entities, "entities_mapped": n_mapped, "spans_unlocated": n_unlocated,
        "predicted_annotations": len(pred_ann),
        "elapsed_s": round(time.time() - t0, 1),
        **{k: v for k, v in s.items() if k != "per_concept_iou"},
    }
    conn.close()

    tag = "" if args.extractor == "production" else f"_{args.extractor}"
    if args.llm_select:
        tag += "_llmsel"
    stem = f"snomed_el_e2e_first{len(sel)}{tag}"
    (HERE / "results" / f"{stem}.json").write_text(
        json.dumps({"summary": summary, "per_note": per_note,
                    "per_concept_iou": s["per_concept_iou"]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    write_md(HERE / "results" / f"{stem}.md", summary, per_note)

    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print(f"\n>>> Official macro character-IoU: {summary['macro_iou']}  "
          f"(leaderboard ~0.42 private / ~0.44 public, different split)")
    print(f"Saved: results/{stem}.json and .md")


def write_md(path: Path, s: dict, per_note: list[dict]) -> None:
    c = s["config"]
    L = [f"# SNOMED CT Entity Linking Challenge — END-TO-END ({s['notes']} notes, {s['split']} split)\n",
         "## Methodology\n",
         "- **Task.** The *complete* pipeline on the raw note: LLM entity extraction (`/api/extract`) →",
         "  per-entity hybrid search (`/api/search`, top-1 concept) → project each verbatim span back to",
         "  character offsets → score with the challenge's **official character-level IoU** (per concept,",
         "  macro-averaged over gold ∪ prediction). This is the challenge's own task and metric, so the",
         "  figure is comparable *in magnitude* to the public leaderboard.",
         f"- **Extractor.** `{s['extractor']}` — model `{s['extractor_model']}`. "
         "(`production` = the selective `/api/extract`; `exhaustive` = the two-pass detect+typify module.)",
         f"- **Search config.** gemma query pre-processing = `{c['gemma']}`, re-ranking = `{c['rerank']}`,",
         f"  k = {c['k']} (the manuscript's interactive serving configuration for this corpus).",
         "- **Edition-drift correction.** Gold codes are resolved through SNOMED historical associations to",
         f"  their current active concept; **{s['gold_dropped_unresolved']}** of {s['gold_mentions_raw']}",
         "  gold mentions had no active successor in our release and were dropped.",
         "- **Offset recovery.** The extractor returns verbatim spans without offsets and the server",
         "  deduplicates spans across chunks, so each span is projected onto **all** its non-overlapping",
         f"  literal occurrences (longer spans win contested characters). **{s['spans_unlocated']}** mapped",
         "  spans could not be located verbatim and were dropped.\n",
         "## Result\n",
         f"**Official macro character-IoU: `{s['macro_iou']}`**\n",
         "| system | public IoU | private IoU |", "|---|--:|--:|"]
    for name, pub, priv in LEADERBOARD:
        L.append(f"| {name} | {pub} | {priv} |")
    L += [f"| **this system (zero-shot, train split)** | — | **{s['macro_iou']}** |", "",
          "> Not head-to-head: the leaderboard scores the **private test** split with **fine-tuned NER**",
          "> models; this is **zero-shot** on the **released train** annotations. Read it as an order-of-",
          "> magnitude reference, not a ranking.\n",
          "### Pipeline counts\n",
          f"- Notes: **{s['notes']}**, elapsed {s['elapsed_s']}s",
          f"- Entities extracted: **{s['entities_extracted']}** → mapped to a concept: "
          f"**{s['entities_mapped']}** → projected to offsets: **{s['predicted_annotations']}**",
          f"- Concepts in union (gold ∪ pred): **{s['concepts_union']}** "
          f"(gold {s['concepts_gold']}, pred {s['concepts_pred']}, shared {s['concepts_shared']}, "
          f"IoU>0 {s['concepts_iou_gt0']})\n",
          "## Caveats\n",
          "- **Train ≠ leaderboard test split** (test gold is private).",
          "- **Zero-shot** vs the leaderboard's supervised, fine-tuned NER systems.",
          "- The extractor deliberately omits some annotated mention types (lab values, normal findings),",
          "  a structural recall ceiling the IoU metric penalizes.",
          "- One best concept per mention; no post-coordination.",
          "- Offsets are reconstructed heuristically (all verbatim occurrences), not emitted by the model.\n",
          "## Per note\n",
          "| note | gold | entities | mapped | projected | unlocated |", "|---|--:|--:|--:|--:|--:|"]
    for p in per_note:
        L.append(f"| `{p['note_id']}` | {p['gold_mentions']} | {p['entities']} | {p['mapped']} | "
                 f"{p['projected']} | {p['unlocated']} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
