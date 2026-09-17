#!/usr/bin/env python3
"""
EXHAUSTIVE two-pass extractor — an ALTERNATIVE to the production `/api/extract`, built only for the
SNOMED CT Entity Linking Challenge end-to-end evaluation. The production extractor is deliberately
selective (diagnoses / findings / procedures / medications; labs, vital-sign values and numeric readings
out of scope) and its single dense-JSON pass makes a small model degenerate on long notes, forcing tiny
chunks. The challenge gold, by contrast, is *exhaustive*: it annotates lab/vital-sign NAMES, anatomy and
heavy abbreviations (UreaN, RDW, AST, EOMI, PERRL, LLE, common bile duct, ...) — almost never the numeric
values (only ~2.5% of gold spans contain a digit).

This module trades the production extractor's precision-oriented design for recall, along three axes the
production path could not combine safely on a 12B QAT model:

  Pass 1 — DETECTION (cheap, high recall). Plain text, one verbatim span per line (no JSON), so the model
  emits far fewer tokens and does not loop/degenerate; that lets us use much larger chunks than the
  production 800-char split. Casts a wide net: findings, symptoms, procedures/imaging, medications,
  substances, body structures/anatomy, organisms, devices, AND lab/vital-sign NAMES — but not numbers.

  Pass 2 — TYPING + NORMALIZATION (batched). For the detected spans, one compact call per batch returns
  `type` (the hierarchy filter) and an English `clinicalTerm` (abbreviation/lay expansion). The hybrid
  search then runs exactly as in the production path.

Nothing here touches api/. The model is configurable (EXHAUSTIVE_MODEL, default gemma3:27b) so detection
can use a stronger model than production without changing production. `extract(text)` returns the same
shape as validation/run_corpus.extract — {"entities": [{text, type, clinicalTerm}, ...]} — so run_e2e can
reuse its mapping + offset-projection unchanged.
"""
from __future__ import annotations

import os
import re

import httpx

GEMMA_URL = os.environ.get("GEMMA_URL", "http://localhost:11434/v1")
EXHAUSTIVE_MODEL = os.environ.get("EXHAUSTIVE_MODEL", "gemma3:27b")
# Plain-text detection output is compact, so a small model no longer degenerates -> large chunks are safe.
CHUNK_CHARS = int(os.environ.get("EXHAUSTIVE_CHUNK_CHARS", "2500"))
DETECT_MAX_TOKENS = int(os.environ.get("EXHAUSTIVE_DETECT_MAX_TOKENS", "2048"))
TYPIFY_BATCH = int(os.environ.get("EXHAUSTIVE_TYPIFY_BATCH", "40"))
# Section-aware detection (#1): split the note by its section headers and detect per section, so the model
# sees the section framing and section-specific mentions are found. Set EXHAUSTIVE_SECTIONS=0 to A/B off.
SECTIONS = os.environ.get("EXHAUSTIVE_SECTIONS", "1") != "0"

# Types the hybrid search understands as a hierarchy filter (api.search.TYPE_TO_HIERARCHY keys/aliases).
VALID_TYPES = {"finding", "procedure", "medication", "body structure", "substance", "organism"}

DETECT_SYSTEM = (
    "You are an exhaustive clinical entity SPAN detector for SNOMED CT coding. From the clinical note "
    "text, list EVERY span that names a clinically codable concept. INCLUDE: diagnoses, signs, symptoms "
    "and findings; the NAMES of laboratory tests and vital signs (e.g. 'hemoglobin', 'platelet count', "
    "'UreaN', 'AST', 'blood pressure', 'O2 sat'); procedures and imaging (including abbreviations like "
    "CT, MRI, EOMI, PERRL); medications and substances; body structures and anatomy (e.g. 'kidney', "
    "'common bile duct', 'LLE'); organisms; and medical devices. Include heavily abbreviated clinical "
    "terms verbatim. DO NOT include numeric values, units or measurements (skip '92/52', '7.9', '95%', "
    "dates, ages) — only the clinical NAME. \n"
    "Output rules: ONE span per line, copied VERBATIM from the note (exact characters, keep source "
    "casing), no numbering, no bullets, no commentary, no blank lines. If a term appears several times, "
    "you may list it once. If there is nothing codable, output nothing."
)

TYPIFY_SYSTEM = (
    "You assign a SNOMED CT category and an English clinical term to each clinical span taken from a "
    "clinical note. You are given the FULL note first, then a numbered list of spans found in it, each "
    "prefixed with its note section in [brackets] as an extra clue. "
    "USE THE NOTE AS CONTEXT to disambiguate abbreviations and short forms — the same letters mean "
    "different things in different notes (e.g. 'MR' may be mitral regurgitation or an MRI scan; 'II' may "
    "be cranial nerve II or grade II; 'PT' may be prothrombin time, physical therapy or a patient). "
    "Resolve each span by how it is used in THIS note.\n"
    "For every numbered span, output exactly one line: `<index>\\t<type>\\t<clinicalTerm>` (tab-separated).\n"
    "- <type> is ONE of: finding | procedure | medication | body structure | substance | organism. "
    "Choose the SNOMED top-level hierarchy the concept belongs to (lab/vital-sign NAMES and symptoms are "
    "'finding'; anatomy is 'body structure'; drugs are 'medication').\n"
    "- <clinicalTerm> is the standard English SNOMED term, expanding abbreviations and lay phrasing "
    "(e.g. 'AST' -> 'aspartate aminotransferase'; 'LLE' -> 'left lower extremity'; 'EOMI' -> "
    "'extraocular movements intact'; 'low platelets' -> 'thrombocytopenia'). Positive concept even if "
    "negated in the note.\n"
    "Output ONLY the tab-separated lines, one per input index, in order. No headers, no prose."
)


_HEADER_RE = re.compile(r"^([A-Z][A-Za-z0-9 ()/,'&-]{1,40}):\s*(.*)$")


def _sections(text: str) -> list[tuple[str, str]]:
    """Split a discharge summary into (header, body) blocks on header lines like 'Chief Complaint:' or
    'Past Medical History:'. Falls back to a single ('', text) block when no headers are found."""
    out: list[tuple[str, str]] = []
    header, body = "", []
    for ln in text.split("\n"):
        m = _HEADER_RE.match(ln.strip())
        if m and len(m.group(1).split()) <= 6:            # a short 'Title:' line => new section
            if body:
                out.append((header, "\n".join(body)))
            header, body = m.group(1), ([m.group(2)] if m.group(2) else [])
        else:
            body.append(ln)
    if body:
        out.append((header, "\n".join(body)))
    return out or [("", text)]


def _chunks(text: str, max_chars: int) -> list[str]:
    """Sentence-aligned chunks under max_chars (same idea as api.search._chunks, replicated to keep this
    module free of an api/ import). Plain-text detection tolerates large chunks."""
    sents = re.split(r"(?<=[.;\n])\s+", text.strip())
    chunks: list[str] = []
    cur = ""
    for s in sents:
        if cur and len(cur) + len(s) + 1 > max_chars:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip() if cur else s
    if cur:
        chunks.append(cur)
    return chunks or [text]


def _chat(system: str, user: str, max_tokens: int, timeout: float) -> str:
    r = httpx.post(
        f"{GEMMA_URL}/chat/completions",
        json={
            "model": EXHAUSTIVE_MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def detect(text: str, timeout: float = 300.0) -> list[tuple[str, str]]:
    """Pass 1: exhaustive verbatim span detection over the note. Returns unique (span, section) in
    first-seen order. Only spans that occur verbatim in the note are kept (the model must copy, not
    paraphrase). With SECTIONS on, detection runs per section and the section header is fed as context."""
    blocks = _sections(text) if SECTIONS else [("", text)]
    seen: dict[str, str] = {}
    for header, body in blocks:
        for ch in _chunks(body, CHUNK_CHARS):
            sysmsg = DETECT_SYSTEM + (f"\nThis text is from the note section '{header}'." if header else "")
            try:
                out = _chat(sysmsg, "Clinical note text:\n" + ch, DETECT_MAX_TOKENS, timeout)
            except Exception as e:
                print(f"    [detect error: {str(e)[:80]}]")
                continue
            for line in out.splitlines():
                span = line.strip().strip("-•*\t ").strip()
                if not span or span.isdigit():
                    continue
                if span in text and span not in seen:   # keep only verbatim spans, first occurrence
                    seen[span] = header
    return list(seen.items())


def typify(spans: list[tuple[str, str]], note_text: str = "", timeout: float = 300.0) -> list[dict]:
    """Pass 2: batched typing + English normalization, WITH the full note as context so abbreviations are
    expanded by how they are used in this note. `spans` is a list of (span, section). Returns
    [{text, type, clinicalTerm, section}] aligned to `spans`; spans the model fails to type fall back to
    type='finding', clinicalTerm=span."""
    out: list[dict] = [{"text": s, "type": "finding", "clinicalTerm": s, "section": sec}
                       for s, sec in spans]
    ctx = f"FULL NOTE (context for disambiguation):\n{note_text}\n\n" if note_text else ""
    for b in range(0, len(spans), TYPIFY_BATCH):
        batch = spans[b:b + TYPIFY_BATCH]
        listing = "\n".join(f"{i}\t[{sec or 'note'}]\t{s}" for i, (s, sec) in enumerate(batch))
        try:
            resp = _chat(TYPIFY_SYSTEM, f"{ctx}SPANS to classify (index<TAB>span):\n" + listing,
                         max_tokens=min(4096, 60 * len(batch) + 256), timeout=timeout)
        except Exception as e:
            print(f"    [typify error: {str(e)[:80]}]")
            continue
        for line in resp.splitlines():
            parts = line.split("\t")
            if len(parts) < 3 or not parts[0].strip().isdigit():
                continue
            idx = int(parts[0].strip())
            if idx >= len(batch):
                continue
            typ = parts[1].strip().lower()
            term = parts[2].strip()
            o = out[b + idx]
            o["type"] = typ if typ in VALID_TYPES else "finding"
            if term:
                o["clinicalTerm"] = term
    return out


def extract(text: str, timeout: float = 300.0) -> dict:
    """Full two-pass extraction. Same return shape as validation/run_corpus.extract so run_e2e can reuse
    its mapping + offset projection unchanged."""
    spans = detect(text, timeout=timeout)
    entities = typify(spans, note_text=text, timeout=timeout) if spans else []
    return {"entities": entities, "model": EXHAUSTIVE_MODEL, "n_detected": len(spans)}


if __name__ == "__main__":
    import sys
    # Synthetic illustrative note (not from any corpus) — exercises labs, abbreviations and anatomy.
    sample = ("Chief Complaint: chest pain and shortness of breath. Procedure: coronary angiography. "
              "Labs: Hgb 11.2, AST 30, BUN 18, platelets 140. Exam: EOMI, PERRL, bilateral pedal edema, "
              "no fever.")
    txt = sys.stdin.read() if not sys.stdin.isatty() else sample
    print(f"model={EXHAUSTIVE_MODEL}  chunk_chars={CHUNK_CHARS}")
    res = extract(txt)
    print(f"detected {res['n_detected']} spans:")
    for e in res["entities"]:
        print(f"  {e['text']!r:30s} [{e['type']}] -> {e['clinicalTerm']}")
