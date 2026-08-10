#!/usr/bin/env python3
"""
Phase 1 checkpoint: tests the multi-prefix ignore-order lexical channel.
Converts a query into to_tsquery('w1:* & w2:* ...') and shows results.

Usage:
    python etl/smoke_test_lexical.py "diab mell"
    python etl/smoke_test_lexical.py "infarct myocard"
"""
from __future__ import annotations

import os
import sys
import unicodedata

import psycopg
from dotenv import load_dotenv


def normalize(term: str) -> str:
    t = unicodedata.normalize("NFKD", term.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


def to_prefix_query(q: str) -> str:
    words = [w for w in normalize(q).split() if w]
    return " & ".join(f"{w}:*" for w in words)


def main() -> None:
    load_dotenv()
    dsn = os.environ["PG_DSN"]
    query = sys.argv[1] if len(sys.argv) > 1 else "diab mell"
    tsq = to_prefix_query(query)
    print(f"query: {query!r}  ->  to_tsquery('unaccent_simple', {tsq!r})\n")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT concept_id, term, semantic_tag, pref_us "
            "FROM descriptions "
            "WHERE term_tsv @@ to_tsquery('unaccent_simple', %s) "
            "ORDER BY ts_rank(term_tsv, to_tsquery('unaccent_simple', %s)) DESC "
            "LIMIT 15",
            (tsq, tsq),
        )
        rows = cur.fetchall()

    if not rows:
        print("(no results)")
        return
    for concept_id, term, tag, pref in rows:
        star = "★" if pref else " "
        tagtxt = f" ({tag})" if tag else ""
        print(f"  {star} {concept_id}  {term}{tagtxt}")


if __name__ == "__main__":
    main()
