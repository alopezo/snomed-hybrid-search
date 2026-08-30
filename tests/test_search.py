"""Retrieval quality tests.

`test_case` runs the parametrized collection in `cases.py` (each case documents which mechanism it
proves). The three `test_*_differential` tests below prove the *marginal* value of a feature by
running the search with and without it — the strongest evidence for why the pipeline is hybrid.

Run everything (needs DB + models + gemma):      make test
Skip the LLM-dependent cases:                     .venv/bin/python -m pytest -m "not llm"
Just the rerank/filter differentials:             .venv/bin/python -m pytest -k differential
"""
import psycopg
import pytest

from api.search import PG_DSN, search
from tests.cases import (
    PLAIN_XRAY_CHEST,
    PROCEDURE_ROOT,
    SINGLE_CASES,
    THORACIC_RADIOLOGY,
)


def _ids(res):
    return [r["concept_id"] for r in res["results"]]


def _rank(res, cid):
    ids = _ids(res)
    return ids.index(cid) + 1 if cid in ids else None


def _params():
    out = []
    for c in SINGLE_CASES:
        marks = []
        if c.get("gemma"):
            marks.append(pytest.mark.llm)
        if c.get("rerank"):
            marks.append(pytest.mark.rerank)
        out.append(pytest.param(c, id=c["id"], marks=marks))
    return out


@pytest.mark.parametrize("case", _params())
def test_case(case, llm_ok):
    if case.get("gemma") and not llm_ok:
        pytest.skip("gemma endpoint not available — run `make llm`")

    res = search(case["query"], k=15, use_gemma=case.get("gemma", False),
                 rerank=case.get("rerank", False), filter_concept=case.get("filter"))
    results, ids, exp = res["results"], _ids(res), case["expect"]
    cid = exp["concept"]

    assert cid in ids, f"{cid} not retrieved for {case['query']!r}; top-5={ids[:5]}"
    row = results[ids.index(cid)]
    rank = ids.index(cid) + 1

    if "top_k" in exp:
        assert rank <= exp["top_k"], f"{cid} at rank {rank}, expected within top-{exp['top_k']}"
    if "rank_le" in exp:
        assert rank <= exp["rank_le"], f"{cid} at rank {rank}, expected ≤ {exp['rank_le']}"
    if "channel" in exp:
        assert row["channel"] in exp["channel"], \
            f"{cid} came via {row['channel']!r}, expected one of {exp['channel']}"
    if exp.get("is_exact"):
        assert row["is_exact"] is True, f"{cid} not flagged is_exact"
    if "expansion_contains" in exp:
        got = (res.get("expansion") or "")
        assert exp["expansion_contains"].lower() in got.lower(), \
            f"expansion {got!r} does not contain {exp['expansion_contains']!r}"


@pytest.mark.rerank
def test_rerank_lifts_precise_over_generic():
    """Without rerank the generic qualifier 'Thoracic radiology' outranks the precise procedure
    'Plain X-ray of chest'. The cross-encoder (blended with RRF) flips that — the marginal value
    of the rerank stage."""
    off = search("chest x-ray", k=15, use_gemma=False, rerank=False)
    on = search("chest x-ray", k=15, use_gemma=False, rerank=True)

    assert _rank(off, PLAIN_XRAY_CHEST) is not None and _rank(on, PLAIN_XRAY_CHEST) is not None
    # rerank pulls the precise procedure up...
    assert _rank(on, PLAIN_XRAY_CHEST) < _rank(off, PLAIN_XRAY_CHEST)
    assert _rank(on, PLAIN_XRAY_CHEST) == 1
    # ...and pushes the generic qualifier down.
    assert _rank(on, THORACIC_RADIOLOGY) > _rank(off, THORACIC_RADIOLOGY)


def test_hierarchy_filter_excludes_out_of_branch():
    """The descendant filter must drop concepts outside the requested subtree. 'Thoracic radiology'
    is a Qualifier value (not a Procedure): present unfiltered, gone when filtered to Procedure,
    while the actual procedure survives."""
    no_filter = search("chest x-ray", k=15, use_gemma=False)
    filtered = search("chest x-ray", k=15, use_gemma=False, filter_concept=PROCEDURE_ROOT)

    assert THORACIC_RADIOLOGY in _ids(no_filter)       # a qualifier value shows up unfiltered
    assert THORACIC_RADIOLOGY not in _ids(filtered)    # ...and is excluded under Procedure
    assert PLAIN_XRAY_CHEST in _ids(filtered)          # the real procedure survives the filter


def test_hierarchy_filter_returns_only_descendants():
    """Directly verify the transitive closure: every filtered result is a descendant-or-self of the
    requested root, per concept_ancestors."""
    filtered = search("chest x-ray", k=15, use_gemma=False, filter_concept=PROCEDURE_ROOT)
    ids = [int(i) for i in _ids(filtered)]
    assert ids, "expected some procedures"
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT concept_id FROM concept_ancestors "
            "WHERE concept_id = ANY(%s) AND NOT (ancestors @> ARRAY[%s::bigint])",
            (ids, PROCEDURE_ROOT),
        )
        stragglers = [r[0] for r in cur.fetchall()]
    assert not stragglers, f"results not under Procedure({PROCEDURE_ROOT}): {stragglers}"
