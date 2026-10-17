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
    CLINICAL_FINDING,
    DIABETES_MELLITUS,
    DURLACHERS_CORN,
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


def test_semantic_floor_drops_noise(monkeypatch):
    """'diab mel' embeds to noise (cosine ~0.7). Without the floor, RRF scores the semantic #1
    ('Durlachers corn') exactly like the lexical #1 ('Diabetes mellitus') — rank-only fusion. The
    SEM_MIN_SIM floor drops semantic-only candidates that weak, leaving the lexical hits."""
    import api.search as s

    monkeypatch.setattr(s, "SEM_MIN_SIM", 0.0)
    monkeypatch.setattr(s, "SEM_MIN_SIM_MULTI", 0.0)
    off = search("diab mel", k=15, use_gemma=False, filter_concept=CLINICAL_FINDING)
    monkeypatch.undo()
    on = search("diab mel", k=15, use_gemma=False, filter_concept=CLINICAL_FINDING)

    assert DURLACHERS_CORN in _ids(off)[:5]          # the noise the floor exists for...
    assert DURLACHERS_CORN not in _ids(on)           # ...is gone with it
    assert _rank(on, DIABETES_MELLITUS) == 1
    assert all(r["channel"] != "semantic" for r in on["results"][:5])


def test_truncation_detector():
    """The floor keys on the query's shape: 2+ prefix-like tokens -> strict floor, one -> the lenient
    one (it may be an acronym), any whole word -> off. 'water on the knee' has 10+ longer completions
    for every word, so only the word-frequency ratio keeps it from looking truncated."""
    import api.search as s

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        floors = {q: s.truncation_floor(cur, q) for q in
                  ("fam bre ca", "diab mel", "pneu", "CID", "water on the knee", "tumor", "pnuemonia")}
    assert floors["fam bre ca"] == floors["diab mel"] == s.SEM_MIN_SIM_MULTI
    assert floors["pneu"] == floors["CID"] == s.SEM_MIN_SIM
    assert floors["water on the knee"] == floors["tumor"] == floors["pnuemonia"] == 0.0


def test_multi_prefix_drops_foreign_word_noise():
    """BioLORD reads 'fam bre ca' as three short Romance words and lands on foreign loanwords (Cafard
    0.86, Cacique 0.83) — above the lenient 0.80 floor. The strict multi-token floor drops them, so
    every result is lexical (or corroborated) and Familial cancer of breast leads."""
    out = search("fam bre ca", k=15, use_gemma=False, rerank=True)
    assert all(r["channel"] != "semantic" for r in out["results"])
    assert _rank(out, "254843006") == 1     # Familial cancer of breast


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


def test_llm_disabled_never_calls_the_llm(monkeypatch):
    """LLM_ENABLED=false (LLM-less deployment): a request that asks for pre-process and LLM pick must
    not reach the LLM, still return the normal hybrid results, and health must say 'disabled'."""
    import api.search as s
    from api.search import health

    def boom(*a, **kw):
        raise AssertionError("the LLM was called with LLM_ENABLED=false")

    monkeypatch.setattr(s, "LLM_ENABLED", False)
    monkeypatch.setattr(s, "gemma_expand", boom)
    monkeypatch.setattr(s, "llm_select", boom)
    out = search("chest x-ray", k=15, use_gemma=True, use_llm_select=True, filter_concept=PROCEDURE_ROOT)

    assert out["expansion"] is None and not out["llm_selected"]
    assert PLAIN_XRAY_CHEST in _ids(out)
    assert health()["llm"] == {"ok": False, "enabled": False, "model": None}


def test_llm_disabled_extract_unavailable(monkeypatch):
    """With the LLM disabled the extract API answers 503 (not a silent empty list) and the page 404s."""
    from fastapi.testclient import TestClient
    import api.server as srv

    monkeypatch.setattr(srv, "LLM_ENABLED", False)
    client = TestClient(srv.app)   # no `with`: skip the startup warm-up
    assert client.post("/api/extract", json={"text": "chest pain"}).status_code == 503
    assert client.get("/extract").status_code == 404
