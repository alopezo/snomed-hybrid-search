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

MAPPING_VERSION = "2.0.2"
OCL_PAGE_SIZE = 100
OCL_MAX_RESULTS = 200
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
            mapping_version text NOT NULL DEFAULT '2.0.2',
            mai_code text NOT NULL,
            mai_display text NOT NULL,
            ocl_concept_id text NOT NULL,
            ocl_display text NOT NULL,
            ocl_display_en text NOT NULL DEFAULT '',
            selected boolean NOT NULL DEFAULT false,
            required_component boolean NOT NULL DEFAULT true,
            mapping_confirmed boolean NOT NULL DEFAULT false,
            confirmed_at timestamptz,
            sort_order integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_key, ocl_concept_id)
        );
        CREATE INDEX IF NOT EXISTS mai_decomposition_v2_mai_code_idx
            ON mai_decomposition_v2 (mai_code);
        ALTER TABLE mai_decomposition_v2
            ADD COLUMN IF NOT EXISTS mapping_confirmed boolean NOT NULL DEFAULT false;
        ALTER TABLE mai_decomposition_v2
            ADD COLUMN IF NOT EXISTS confirmed_at timestamptz;
        ALTER TABLE mai_decomposition_v2
            ALTER COLUMN mapping_version SET DEFAULT '2.0.2';
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
    limit = max(1, min(int(limit), OCL_MAX_RESULTS))
    base_query_norm = _normalized_query(query)
    if not base_query_norm:
        return {"query": query, "components": [], "source": "empty", "total_found": 0}
    query_norm = f"{base_query_norm}|limit:{limit}"

    url = f"{OCL_API_URL}/orgs/{OCL_ORG}/sources/{OCL_SOURCE}/concepts/"
    try:
        raw_items: list[dict] = []
        total_found = 0
        page = 1
        while len(raw_items) < limit:
            page_limit = min(OCL_PAGE_SIZE, limit - len(raw_items))
            response = httpx.get(
                url,
                params={"q": query, "limit": page_limit, "page": page, "verbose": "true"},
                headers={"Authorization": f"Token {OCL_TOKEN}"},
                timeout=30,
            )
            response.raise_for_status()
            raw = response.json()
            if not isinstance(raw, list):
                raw = raw.get("results", [])
            total_found = max(total_found, int(response.headers.get("num_found") or 0))
            if not raw:
                break
            raw_items.extend(raw)
            if len(raw) < page_limit or (total_found and len(raw_items) >= total_found):
                break
            page += 1

        deduplicated: dict[str, dict] = {}
        for item in raw_items:
            component = _component(item)
            if not item.get("retired") and component["id"] and component["display"]:
                deduplicated.setdefault(component["id"], component)
        components = list(deduplicated.values())[:limit]
        payload = {
            "query": query,
            "components": components,
            "source": "live",
            "total_found": max(total_found, len(components)),
            "requested_limit": limit,
        }
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
                     selected, required_component, mapping_confirmed, confirmed_at,
                     sort_order, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s THEN now() ELSE NULL END, %s, now())
                ON CONFLICT (workspace_key, ocl_concept_id) DO UPDATE SET
                    ocl_display = excluded.ocl_display,
                    ocl_display_en = excluded.ocl_display_en,
                    selected = excluded.selected,
                    required_component = excluded.required_component,
                    mapping_confirmed = excluded.mapping_confirmed,
                    confirmed_at = CASE
                        WHEN excluded.mapping_confirmed THEN
                            COALESCE(mai_decomposition_v2.confirmed_at, excluded.confirmed_at)
                        ELSE NULL
                    END,
                    sort_order = excluded.sort_order,
                    updated_at = excluded.updated_at
                """,
                (
                    key, MAPPING_VERSION, mai_code.strip(), mai_display.strip(), str(item["id"]),
                    str(item.get("display") or ""), str(item.get("display_en") or ""),
                    bool(item.get("selected")), bool(item.get("required", True)),
                    bool(item.get("confirmed")), bool(item.get("confirmed")), order,
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
                   required_component, mapping_confirmed, confirmed_at, sort_order, updated_at
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
                "selected": row[3], "required": row[4], "confirmed": row[5],
                "confirmed_at": row[6].isoformat() if row[6] else "",
                "sort_order": row[7], "updated_at": row[8].isoformat(),
            }
            for row in rows
        ],
    }
