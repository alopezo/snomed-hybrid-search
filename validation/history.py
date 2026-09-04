"""SNOMED CT historical-association resolution + semantic tag lookup for the validation scripts.

Gold corpora are annotated against an older SNOMED CT edition; by our loaded release some gold concepts
have been inactivated and carry a historical association (SAME AS / REPLACED BY / …) to a current
active concept. `resolve()` maps a gold code to its current active equivalent so the evaluation is not
penalized by edition drift. `semantic_tag()` returns the FSN's tag (e.g. "disorder", "finding",
"substance", "morphologic abnormality") for scope filtering.
"""
from __future__ import annotations

import glob
import os
import re
from functools import lru_cache

# Association refsets that point an inactive concept to a replacement, in resolution priority order.
# MOVED TO (…524003) and WAS A (…528000) are intentionally excluded (no clinical successor in core).
ASSOC_PRIORITY = {
    "900000000000527005": 0,   # SAME AS
    "900000000000526001": 1,   # REPLACED BY
    "1186924009":         2,   # POSSIBLY REPLACED BY
    "900000000000523009": 3,   # POSSIBLY EQUIVALENT TO
    "900000000000530003": 4,   # ALTERNATIVE
}


def _assoc_file() -> str | None:
    sd = os.environ.get("SNOMED_SNAPSHOT_DIR", "")
    m = glob.glob(os.path.join(sd, "**", "der2_cRefset_AssociationSnapshot*.txt"), recursive=True)
    return m[0] if m else None


def load_assoc(codes: set[str]) -> dict[str, list[tuple[str, str]]]:
    """One pass over the association snapshot; keep active rows whose referencedComponentId is in codes.
    Returns {inactive_code: [(refsetId, targetComponentId), …]}."""
    f = _assoc_file()
    out: dict[str, list[tuple[str, str]]] = {}
    if not f:
        return out
    codes = set(codes)
    with open(f, encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 7 and p[2] == "1" and p[5] in codes:   # active, referenced ∈ codes
                out.setdefault(p[5], []).append((p[4], p[6]))
    return out


def semantic_tag(cur, code: str) -> str | None:
    """FSN semantic tag of an ACTIVE concept in our release; None if the concept is not active here."""
    cur.execute("SELECT term FROM descriptions WHERE concept_id=%s AND type_id=900000000000003001 LIMIT 1",
                (int(code),))
    r = cur.fetchone()
    if not r:
        return None
    m = re.search(r"\(([^)]+)\)\s*$", r[0])
    return m.group(1) if m else ""


def resolve(cur, code: str, assoc: dict) -> tuple[str | None, str | None]:
    """Return (current_active_code, tag). If `code` is active in our release, returns it unchanged;
    otherwise follows the highest-priority historical association to an active concept. (None, None) if
    it cannot be resolved to an active concept."""
    tag = semantic_tag(cur, code)
    if tag is not None:
        return code, tag
    for refset, tgt in sorted(assoc.get(code, []), key=lambda x: ASSOC_PRIORITY.get(x[0], 99)):
        if refset not in ASSOC_PRIORITY:
            continue
        t = semantic_tag(cur, tgt)
        if t is not None:
            return tgt, t
    return None, None
