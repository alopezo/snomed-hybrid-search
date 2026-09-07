#!/usr/bin/env python3
"""
LLM benchmark: latency + qualitative extraction for a given model, so we can compare Gemma 3 vs
Gemma 4 on the two LLM roles of the pipeline (query normalization + entity extraction), independent
of the retrieval/DB stack. Runs the *actual* gemma_expand() and extract_entities() from api.search,
just monkeypatching the model name — so it exercises the real prompts and parsing.

    cd snomed-search && .venv/bin/python validation/bench_llm.py --model gemma3:12b
    cd snomed-search && .venv/bin/python validation/bench_llm.py --model gemma4:12b-it-qat
    cd snomed-search && .venv/bin/python validation/bench_llm.py --compare

Saves results/llm_bench_<model>.json; --compare prints both side by side.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
import sys
sys.path.insert(0, str(HERE.parent))
import api.search as S  # noqa: E402

# --- fixed inputs -------------------------------------------------------------------------------
# Short queries for the query-normalization role (mixed EN/ES, abbreviations, typos).
NORM_QUERIES = [
    "radiografia de torax", "EPOC", "inf myo", "dolor abdominal",
    "chest xray", "htn", "IAM", "insuf renal cronica",
]

# Notes for the entity-extraction role (negation, history/past, Spanish, abbreviations, a long one).
EXTRACT_NOTES = {
    "negation_history": (
        "A 62-year-old woman with a history of stroke presents with chest pain. "
        "She denies fever. No shortness of breath. BP 92/52, pulse 55."
    ),
    "spanish": (
        "Paciente con diabetes mellitus tipo 2 y disnea de esfuerzo. Antecedente de EPOC. "
        "Se solicita radiografía de tórax. Niega dolor torácico."
    ),
    "abbrev_imaging": (
        "Patient with COPD exacerbation. CT chest ordered, ruled out PE. History of MI. "
        "Started on furosemide and prednisone."
    ),
    "long_note": (
        "68-year-old man admitted for community-acquired pneumonia. Past medical history: type 2 "
        "diabetes mellitus, hypertension, chronic kidney disease stage 3, prior myocardial infarction "
        "with PCI. On admission he reported productive cough, fever and pleuritic chest pain; denied "
        "hemoptysis. Chest X-ray showed right lower lobe consolidation. Blood cultures were drawn. He "
        "was started on ceftriaxone and azithromycin. During the stay he developed acute kidney injury "
        "and mild hyperkalemia, managed conservatively. Echocardiogram revealed reduced ejection "
        "fraction consistent with ischemic cardiomyopathy. No evidence of deep vein thrombosis on "
        "lower-limb ultrasound. He was discharged on metformin, lisinopril, atorvastatin and a short "
        "prednisone taper, with pulmonology follow-up."
    ),
}


def slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", model)


def run(model: str, reasoning: str | None = None, extract_max_tokens: int | None = None) -> None:
    S.GEMMA_MODEL = model
    S.GEMMA_REASONING = reasoning or None
    if extract_max_tokens:
        S.EXTRACT_MAX_TOKENS = extract_max_tokens
    label = f"{model} [think={reasoning or 'default'}]"
    RES.mkdir(exist_ok=True)
    print(f"[bench] model = {label}  extract_max_tokens = {S.EXTRACT_MAX_TOKENS}  endpoint = {S.GEMMA_URL}\n")

    # warm-up (loads weights into the LLM server; not timed)
    print("warming up…")
    S.gemma_expand("warmup")

    # --- query normalization ---
    print("\n== query normalization ==")
    norm, norm_ms = [], []
    for q in NORM_QUERIES:
        t = time.perf_counter()
        out = S.gemma_expand(q)
        ms = (time.perf_counter() - t) * 1000
        norm_ms.append(ms)
        norm.append({"query": q, "out": out, "ms": round(ms, 1)})
        print(f"  {ms:7.0f} ms  {q!r:32} -> {out!r}")

    # --- entity extraction ---
    print("\n== entity extraction ==")
    extr, extr_ms = {}, []
    for name, note in EXTRACT_NOTES.items():
        t = time.perf_counter()
        ents = S.extract_entities(note)
        ms = (time.perf_counter() - t) * 1000
        extr_ms.append(ms)
        slim = [{"text": e.get("text"), "type": e.get("type"), "context": e.get("context"),
                 "clinicalTerm": e.get("clinicalTerm")} for e in ents]
        extr[name] = {"n": len(ents), "ms": round(ms, 1), "entities": slim}
        print(f"  {ms:7.0f} ms  {name:16} -> {len(ents)} entities")

    summary = {
        "model": label,
        "norm": {"mean_ms": round(statistics.mean(norm_ms), 1),
                 "median_ms": round(statistics.median(norm_ms), 1),
                 "total_ms": round(sum(norm_ms), 1)},
        "extract": {"mean_ms": round(statistics.mean(extr_ms), 1),
                    "median_ms": round(statistics.median(extr_ms), 1),
                    "total_ms": round(sum(extr_ms), 1),
                    "total_entities": sum(v["n"] for v in extr.values())},
    }
    out = RES / f"llm_bench_{slug(label)}.json"
    out.write_text(json.dumps({"summary": summary, "norm": norm, "extract": extr},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved {out}")
    print(f"norm    mean {summary['norm']['mean_ms']} ms  median {summary['norm']['median_ms']} ms")
    print(f"extract mean {summary['extract']['mean_ms']} ms  ({summary['extract']['total_entities']} entities total)")


def compare() -> None:
    files = sorted(glob.glob(str(RES / "llm_bench_*.json")))
    runs = [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]
    if len(runs) < 2:
        print("need at least two llm_bench_*.json (run each model first)"); return
    print("=== latency ===")
    print(f"{'model':28} {'norm mean':>10} {'norm med':>9} {'extr mean':>10} {'extr med':>9} {'entities':>9}")
    for r in runs:
        s = r["summary"]
        print(f"{s['model']:28} {s['norm']['mean_ms']:>10} {s['norm']['median_ms']:>9} "
              f"{s['extract']['mean_ms']:>10} {s['extract']['median_ms']:>9} "
              f"{s['extract']['total_entities']:>9}")
    print("\n=== query normalization (side by side) ===")
    for i, q in enumerate(NORM_QUERIES):
        print(f"\n  {q!r}")
        for r in runs:
            print(f"     {r['summary']['model']:26} -> {r['norm'][i]['out']!r}")
    print("\n=== entity extraction (entities per note) ===")
    for name in EXTRACT_NOTES:
        print(f"\n  [{name}]")
        for r in runs:
            ents = r["extract"][name]["entities"]
            print(f"     {r['summary']['model']:26} ({len(ents)}):")
            for e in ents:
                print(f"        - {e['text']!r:34} [{e['type']}/{e['context']}] -> {e['clinicalTerm']!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--reasoning", default=None, help="reasoning_effort: none|low|medium|high")
    ap.add_argument("--extract-max-tokens", type=int, default=None)
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.compare:
        compare()
    elif args.model:
        run(args.model, reasoning=args.reasoning, extract_max_tokens=args.extract_max_tokens)
    else:
        ap.error("pass --model <name> or --compare")


if __name__ == "__main__":
    main()
