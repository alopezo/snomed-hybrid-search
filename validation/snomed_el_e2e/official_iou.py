#!/usr/bin/env python3
"""
Official SNOMED CT Entity Linking Challenge metric: character-level, per-concept IoU, macro-averaged.

This is a dependency-light re-implementation of the challenge's scoring function `iou_per_class`
(DrivenData / PhysioNet `snomed-ct-entity-linking`, Apache-2.0; the identical routine appears in the
top-3 winners' code, e.g. `2nd Place/submission/iou.py`). The reference builds one sparse
docs x characters matrix, writes each annotation's `concept_id` into `[start:end)` (later rows overwrite
earlier ones on overlap), then for every concept in gold U pred computes

    IoU(concept) = |chars where gold == concept AND pred == concept|
                 / |chars where gold == concept  OR pred == concept|

summed over all documents, and finally takes the unweighted mean over the concepts present in gold U pred.
Concepts predicted but absent from the gold (and vice-versa) enter the average with IoU 0, so both missed
and spurious concepts are penalized.

We reproduce those exact counts with per-note character arrays instead of a global sparse matrix (each
character position belongs to exactly one note, so per-note counts sum to the same totals). Verified
against a hand-computed case in `_selftest()`.

Records may be dicts with keys {note_id, start, end, concept_id} or 4-tuples in that order. `start`/`end`
are half-open character offsets (the gold stores them as floats, e.g. "180.0" -> 180).
"""
from __future__ import annotations

from collections import defaultdict


def _norm(records) -> list[tuple[str, int, int, int]]:
    out = []
    for r in records:
        if isinstance(r, dict):
            nid, s, e, c = r["note_id"], r["start"], r["end"], r["concept_id"]
        else:
            nid, s, e, c = r
        out.append((str(nid), int(float(s)), int(float(e)), int(float(c))))
    return out


def _char_maps(records: list[tuple[str, int, int, int]]) -> dict[str, dict[int, int]]:
    """note_id -> {char_index: concept_id}. Later annotations overwrite earlier ones on overlap,
    matching the reference matrix assignment `mtx[doc, start:end] = concept_id`."""
    maps: dict[str, dict[int, int]] = defaultdict(dict)
    for nid, s, e, c in records:
        m = maps[nid]
        for i in range(s, e):
            m[i] = c
    return maps


def iou_per_class(pred, gold, mean: bool = True):
    """Char-level IoU per concept over gold U pred, macro-averaged (mean=True) or as a
    {concept_id: iou} dict (mean=False). Empty gold and pred -> 0.0 / {}."""
    g = _norm(gold)
    p = _norm(pred)
    gmap = _char_maps(g)
    pmap = _char_maps(p)

    cats = {c for _, _, _, c in g} | {c for _, _, _, c in p}
    inter: dict[int, int] = defaultdict(int)
    union: dict[int, int] = defaultdict(int)

    notes = set(gmap) | set(pmap)
    for nid in notes:
        gm = gmap.get(nid, {})
        pm = pmap.get(nid, {})
        for i, gc in gm.items():
            pc = pm.get(i)
            if gc == pc:
                inter[gc] += 1
                union[gc] += 1
            else:
                union[gc] += 1
                if pc is not None:
                    union[pc] += 1
        for i, pc in pm.items():           # predicted chars with no gold concept at that position
            if i not in gm:
                union[pc] += 1

    ious = {c: (inter[c] / union[c] if union[c] else 0.0) for c in cats}
    if mean:
        return sum(ious.values()) / len(ious) if ious else 0.0
    return ious


def score(pred, gold) -> dict:
    """Convenience summary: macro IoU + concept-count breakdown, for the report."""
    g = _norm(gold)
    p = _norm(pred)
    gcats = {c for _, _, _, c in g}
    pcats = {c for _, _, _, c in p}
    ious = iou_per_class(p, g, mean=False)
    macro = sum(ious.values()) / len(ious) if ious else 0.0
    return {
        "macro_iou": round(macro, 4),
        "concepts_union": len(gcats | pcats),
        "concepts_gold": len(gcats),
        "concepts_pred": len(pcats),
        "concepts_shared": len(gcats & pcats),
        "concepts_iou_gt0": sum(1 for v in ious.values() if v > 0),
        "per_concept_iou": {str(c): round(v, 4) for c, v in sorted(ious.items(), key=lambda kv: -kv[1])},
    }


def _selftest() -> None:
    # One note, 20 chars. Gold: [0,10)=A, [10,20)=B. Pred: [0,5)=A (half of A, exact), [10,20)=C (wrong).
    gold = [("n1", 0, 10, 111), ("n1", 10, 20, 222)]
    pred = [("n1", 0, 5, 111), ("n1", 10, 20, 333)]
    ious = iou_per_class(pred, gold, mean=False)
    # A(111): inter 5, union 10 -> 0.5 ; B(222): inter 0, union 10 -> 0 ; C(333): inter 0, union 10 -> 0
    assert abs(ious[111] - 0.5) < 1e-9, ious
    assert ious[222] == 0.0 and ious[333] == 0.0, ious
    macro = iou_per_class(pred, gold, mean=True)
    assert abs(macro - 0.5 / 3) < 1e-9, macro
    # Perfect match -> 1.0
    assert abs(iou_per_class(gold, gold, mean=True) - 1.0) < 1e-9
    print("official_iou selftest OK  (macro on toy case =", round(macro, 4), ")")


if __name__ == "__main__":
    _selftest()
