#!/usr/bin/env python3
"""
Phase 4 — FastAPI API + static demo.

    cd snomed-search
    uvicorn api.server:app --port 8090
    # open http://127.0.0.1:8090
"""
from __future__ import annotations

import os

import json
import time

from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api.search import (
    TYPE_TO_HIERARCHY,
    extract_entities,
    gemma_expand,
    get_model,
    health,
    search,
    search_stream,
    warmup,
)

app = FastAPI(title="SNOMED hybrid search")

DEMO_DIR = os.path.join(os.path.dirname(__file__), "..", "demo")


@app.on_event("startup")
def _warmup() -> None:
    get_model()  # load the embedding model once (avoids latency on the 1st query)
    try:
        gemma_expand("warmup")  # load the LLM into memory so the first real query isn't cold
    except Exception:
        pass


@app.get("/api/health")
def api_health():
    return health()


@app.get("/api/warmup")
def api_warmup():
    return warmup()


@app.get("/api/testcases")
def api_testcases():
    """Expose the test collection (from tests/cases.py) so the interactive runner uses the same
    single source of truth as `make test`. Best-effort: returns empty if tests/ isn't present."""
    try:
        from tests.cases import (
            PLAIN_XRAY_CHEST,
            PROCEDURE_ROOT,
            SINGLE_CASES,
            THORACIC_RADIOLOGY,
        )
    except Exception:
        return {"single": [], "constants": {}}
    return {
        "single": SINGLE_CASES,
        "constants": {
            "plain_xray_chest": PLAIN_XRAY_CHEST,
            "thoracic_radiology": THORACIC_RADIOLOGY,
            "procedure_root": PROCEDURE_ROOT,
        },
    }


@app.post("/api/extract")
def api_extract(text: str = Body(..., embed=True, min_length=1)):
    """Extract structured clinical entities from a free-text note (one LLM call). The page then maps
    each entity through /api/search. `type_filters` lets the page constrain each entity's search to the
    matching SNOMED hierarchy (with a client-side fallback to no filter)."""
    t0 = time.perf_counter()
    entities = extract_entities(text)
    return {
        "entities": entities,
        "type_filters": TYPE_TO_HIERARCHY,
        "extract_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


@app.get("/extract")
def extract_page() -> FileResponse:
    return FileResponse(os.path.join(DEMO_DIR, "extract.html"))


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1),
    k: int = Query(15, ge=1, le=50),
    gemma: bool = Query(True),
    rerank: bool = Query(False),
    filter: int | None = Query(None),
):
    return search(q, k=k, use_gemma=gemma, rerank=rerank, filter_concept=filter)


@app.get("/api/search_stream")
def api_search_stream(
    q: str = Query(..., min_length=1),
    k: int = Query(15, ge=1, le=50),
    gemma: bool = Query(True),
    rerank: bool = Query(False),
    filter: int | None = Query(None),
):
    def gen():
        for event in search_stream(q, k=k, use_gemma=gemma, rerank=rerank, filter_concept=filter):
            yield json.dumps(event) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(DEMO_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=DEMO_DIR), name="static")
