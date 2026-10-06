"""Open Concept Lab integration for MAI imaging decomposition (mapping workflow v2)."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone

import httpx
import psycopg
from psycopg.types.json import Jsonb

from api.search import PG_DSN

MAPPING_VERSION = "2.0.0"
OCL_API_URL = os.environ.get("OCL_API_URL", "https://api.openconceptlab.org").rstrip("/")
OCL_ORG = os.environ.get("OCL_ORG", "MINSALQA")
OCL_SOURCE = os.environ.get("OCL_SOURCE", "manualimagenes")
OCL_USER = os.environ.get("OCL_USER", "")
OCL_TOKEN = os.environ.get("OCL_TOKEN", "")
OCL_ENABLED = os.environ.get("OCL_MAI_DECOMPOSITION_ENABLED", "false").lower() in {
    "1", "true", "yes", "on",
}


def _workspace_key(mai_code: str, mai_display: str) -> str:
    value = f"{mai_code.strip()}\n{' '.join(mai_display.lower().split())}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _schema(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS ocl_search_cache_v2 (
            query_norm text PRIMARY KEY,
            query_text text NOT NULL,
            org_id text NOT NULL,
            source_id text NOT NULL,
            response jsonb NOT NULL,
            fetched_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS mai_decomposition_v2 (
            workspace_key text NOT NULL,
            mapping_version text NOT NULL DEFAULT '2.0.0',
            mai_code text NOT NULL,
            mai_display text NOT NULL,
            ocl_concept_id text NOT NULL,
            ocl_display text NOT NULL,
            ocl_display_en text NOT NULL DEFAULT '',
            selected boolean NOT NULL DEFAULT false,
            required_component boolean NOT NULL DEFAULT true,
            sort_order integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_key, ocl_concept_id)
        );
        CREATE INDEX IF NOT EXISTS mai_decomposition_v2_mai_code_idx
            ON mai_decomposition_v2 (mai_code);
        """
    )


def config() -> dict:
    return {
        "version": MAPPING_VERSION,
        "enabled": OCL_ENABLED,
        "configured": bool(OCL_TOKEN),
        "org": OCL_ORG,
        "source": OCL_SOURCE,
        "user": OCL_USER,
    }


def _normalized_query(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _component(item: dict) -> dict:
    names = [n for n in (item.get("names") or []) if not n.get("retired")]

    def preferred(locale: str) -> str:
        choices = [n for n in names if n.get("locale") == locale]
        choices.sort(key=lambda n: (not bool(n.get("locale_preferred")), n.get("name_type") != "FULLY_SPECIFIED"))
        return str(choices[0].get("name") or "") if choices else ""

    display_es = preferred("es") or str(item.get("display_name") or "")
    return {
        "id": str(item.get("id") or ""),
        "display": display_es,
        "display_en": preferred("en"),
        "concept_class": str(item.get("concept_class") or ""),
        "retired": bool(item.get("retired")),
        "url": str(item.get("url") or ""),
    }


def search_components(query: str, limit: int = 20) -> dict:
    if not OCL_ENABLED:
        raise RuntimeError("OCL MAI decomposition is disabled")
    if not OCL_TOKEN:
        raise RuntimeError("OCL token is not configured")
    query = query.strip()
    query_norm = _normalized_query(query)
    if not query_norm:
        return {"query": query, "components": [], "source": "empty"}

    url = f"{OCL_API_URL}/orgs/{OCL_ORG}/sources/{OCL_SOURCE}/concepts/"
    try:
        response = httpx.get(
            url,
            params={"q": query, "limit": limit, "verbose": "true"},
            headers={"Authorization": f"Token {OCL_TOKEN}"},
            timeout=20,
        )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, list):
            raw = raw.get("results", [])
        components = [_component(item) for item in raw if not item.get("retired")]
        components = [item for item in components if item["id"] and item["display"]]
        payload = {"query": query, "components": components, "source": "live"}
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            _schema(cur)
            cur.execute(
                """
                INSERT INTO ocl_search_cache_v2
                    (query_norm, query_text, org_id, source_id, response, fetched_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (query_norm) DO UPDATE SET
                    query_text = excluded.query_text,
                    response = excluded.response,
                    fetched_at = excluded.fetched_at
                """,
                (query_norm, query, OCL_ORG, OCL_SOURCE, Jsonb(payload)),
            )
        return payload
    except Exception as exc:
        with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
            _schema(cur)
            cur.execute(
                "SELECT response, fetched_at FROM ocl_search_cache_v2 WHERE query_norm = %s",
                (query_norm,),
            )
            cached = cur.fetchone()
        if cached:
            payload = dict(cached[0])
            payload["source"] = "cache"
            payload["cached_at"] = cached[1].isoformat()
            return payload
        raise RuntimeError(f"OCL is unavailable and this query is not cached: {type(exc).__name__}") from exc


def save_decomposition(mai_code: str, mai_display: str, components: list[dict]) -> dict:
    key = _workspace_key(mai_code, mai_display)
    selected_ids = {str(item["id"]) for item in components if item.get("selected")}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        _schema(cur)
        for order, item in enumerate(components):
            cur.execute(
                """
                INSERT INTO mai_decomposition_v2
                    (workspace_key, mapping_version, mai_code, mai_display,
                     ocl_concept_id, ocl_display, ocl_display_en,
                     selected, required_component, sort_order, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (workspace_key, ocl_concept_id) DO UPDATE SET
                    ocl_display = excluded.ocl_display,
                    ocl_display_en = excluded.ocl_display_en,
                    selected = excluded.selected,
                    required_component = excluded.required_component,
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                (
                    key, MAPPING_VERSION, mai_code.strip(), mai_display.strip(), str(item["id"]),
                    str(item.get("display") or ""), str(item.get("display_en") or ""),
                    bool(item.get("selected")), bool(item.get("required", True)), order,
                ),
            )
    return {"workspace_key": key, "selected": len(selected_ids), "saved_at": datetime.now(timezone.utc).isoformat()}


def get_decomposition(mai_code: str, mai_display: str) -> dict:
    key = _workspace_key(mai_code, mai_display)
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        _schema(cur)
        cur.execute(
            """
            SELECT ocl_concept_id, ocl_display, ocl_display_en, selected,
                   required_component, sort_order, updated_at
            FROM mai_decomposition_v2
            WHERE workspace_key = %s ORDER BY sort_order, ocl_concept_id
            """,
            (key,),
        )
        rows = cur.fetchall()
    return {
        "workspace_key": key,
        "version": MAPPING_VERSION,
        "mai_code": mai_code,
        "mai_display": mai_display,
        "components": [
            {
                "id": row[0], "display": row[1], "display_en": row[2],
                "selected": row[3], "required": row[4], "sort_order": row[5],
                "updated_at": row[6].isoformat(),
            }
            for row in rows
        ],
    }
