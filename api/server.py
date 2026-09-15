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
import tempfile
import time
import uuid

from fastapi import Body, FastAPI, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from api.mapper import best_match, read_table, resolve_columns, write_table
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

# token -> (path, download_name, media_type) for generated mapping files (served once, then removed).
MAP_JOBS: dict[str, tuple[str, str, str]] = {}

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
    channel: str = Query("both", pattern="^(both|lexical|semantic)$"),
):
    return search(q, k=k, use_gemma=gemma, rerank=rerank, filter_concept=filter, channel=channel)


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


@app.post("/api/map/preview")
async def api_map_preview(file: UploadFile = File(...)):
    """Parse an uploaded .csv/.xlsx and report its columns + a small sample, so the page can confirm
    which column is `code` and which is `term` before running the (potentially long) mapping."""
    try:
        headers, data, fmt = read_table(await file.read(), file.filename or "")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    code_idx, term_idx = resolve_columns(headers)
    return {"columns": headers, "n_rows": len(data), "filetype": fmt,
            "code_idx": code_idx, "term_idx": term_idx, "sample": data[:5]}


@app.post("/api/map")
async def api_map(
    file: UploadFile = File(...),
    code_col: str | None = Form(None),
    term_col: str | None = Form(None),
    out_format: str | None = Form(None),
    filter: str | None = Form(None),
    channel: str = Form("semantic"),
    rerank: bool = Form(False),
):
    """Map every row's term to its best SNOMED concept and append `snomed_code` + `snomed_term`.
    `filter` scopes every row to descendants-or-self of a hierarchy root (e.g. 363787002 Observable
    entity or 386053000 Evaluation procedure for a lab-test catalog). `channel` ("semantic"|"both"|
    "lexical") and `rerank` let dirtier vocabularies use lexical fusion + the cross-encoder; gemma stays
    off. Streams NDJSON progress (one line per row) and a final {"stage":"done", token} line; the file is
    fetched from /api/map/download/{token}."""
    try:
        headers, data, fmt = read_table(await file.read(), file.filename or "")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    code_idx, term_idx = resolve_columns(headers, code_col, term_col)
    out_fmt = (out_format or fmt).lower()
    if out_fmt not in ("csv", "xlsx", "ods"):
        out_fmt = "csv"
    if channel not in ("semantic", "both", "lexical"):
        channel = "semantic"
    try:
        filter_concept = int(filter) if filter not in (None, "", "none") else None
    except ValueError:
        filter_concept = None
    base = os.path.splitext(os.path.basename(file.filename or "table"))[0]
    n = len(data)
    width = len(headers)

    def gen():
        out_rows: list[list[str]] = []
        t0 = time.perf_counter()
        for i, row in enumerate(data, 1):
            row = [str(c) for c in row] + [""] * (width - len(row))
            term = row[term_idx] if term_idx < len(row) else ""
            match_code, match_term = best_match(term, filter_concept, channel, rerank)
            out_rows.append(row + [match_code, match_term])
            yield json.dumps({"stage": "row", "i": i, "n": n,
                              "code": row[code_idx] if code_idx < len(row) else "",
                              "term": term, "match_code": match_code,
                              "match_term": match_term}) + "\n"
        token = uuid.uuid4().hex
        path = os.path.join(tempfile.gettempdir(), f"snomedmap_{token}.{out_fmt}")
        write_table(headers, out_rows, out_fmt, path)
        media = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 "ods": "application/vnd.oasis.opendocument.spreadsheet",
                 "csv": "text/csv"}[out_fmt]
        MAP_JOBS[token] = (path, f"{base}_mapped.{out_fmt}", media)
        yield json.dumps({"stage": "done", "token": token, "n": n,
                          "elapsed_s": round(time.perf_counter() - t0, 1)}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/map/download/{token}")
def api_map_download(token: str):
    job = MAP_JOBS.pop(token, None)
    if not job:
        return JSONResponse({"error": "unknown or expired download token"}, status_code=404)
    path, name, media = job
    return FileResponse(path, media_type=media, filename=name,
                        background=BackgroundTask(lambda: os.path.exists(path) and os.remove(path)))


@app.get("/map")
def map_page() -> FileResponse:
    return FileResponse(os.path.join(DEMO_DIR, "map.html"))


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(DEMO_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=DEMO_DIR), name="static")
