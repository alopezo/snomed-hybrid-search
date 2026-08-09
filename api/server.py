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

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from api.search import gemma_expand, get_model, search, search_stream

app = FastAPI(title="SNOMED hybrid search")

DEMO_DIR = os.path.join(os.path.dirname(__file__), "..", "demo")


@app.on_event("startup")
def _warmup() -> None:
    get_model()  # load the embedding model once (avoids latency on the 1st query)
    try:
        gemma_expand("warmup")  # load the LLM into memory so the first real query isn't cold
    except Exception:
        pass


@app.get("/api/search")
def api_search(
    q: str = Query(..., min_length=1),
    k: int = Query(15, ge=1, le=50),
    gemma: bool = Query(True),
    rerank: bool = Query(False),
    lang: str = Query("es"),
):
    return search(q, k=k, use_gemma=gemma, rerank=rerank, lang=lang)


@app.get("/api/search_stream")
def api_search_stream(
    q: str = Query(..., min_length=1),
    k: int = Query(15, ge=1, le=50),
    gemma: bool = Query(True),
    rerank: bool = Query(False),
    lang: str = Query("es"),
):
    def gen():
        for event in search_stream(q, k=k, use_gemma=gemma, rerank=rerank, lang=lang):
            yield json.dumps(event) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(DEMO_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=DEMO_DIR), name="static")
