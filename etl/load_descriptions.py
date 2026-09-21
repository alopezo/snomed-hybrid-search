#!/usr/bin/env python3
"""
Phase 1 — ETL: RF2 (Snapshot) -> `descriptions` table in PostgreSQL.

What it does:
  1. Reads sct2_Concept_Snapshot          -> set of ACTIVE concepts.
  2. Reads sct2_Description_Snapshot (pass 1) -> conceptId -> semantic_tag map (from the FSN).
  3. Reads sct2_Description_Snapshot (pass 2) -> bulk COPY of active descriptions
     (synonyms + FSN) of active concepts, with term_norm, semantic_tag and module_id
     (moduleId, RF2 col 3; used by exclude_module scoping to tell International from extensions).
  4. Builds term_tsv = to_tsvector('unaccent_simple', term)  (lexical channel, accent-insensitive).
  5. Reads der2_cRefset_LanguageSnapshot   -> marks pref_us / pref_gb (preferred term per dialect).

Usage:
    cd snomed-search
    source .venv/bin/activate
    python etl/load_descriptions.py                      # full rebuild from SNOMED_SNAPSHOT_DIR
    python etl/load_descriptions.py --snapshot DIR --append   # add an extension on top (no TRUNCATE)

Config via .env (see .env.example): PG_DSN, SNOMED_SNAPSHOT_DIR.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import unicodedata

import psycopg
from dotenv import load_dotenv

# --- SNOMED constants (sctids) ---
TYPE_FSN = "900000000000003001"
TYPE_SYNONYM = "900000000000013009"
ACCEPT_PREFERRED = "900000000000548007"
REFSET_US = "900000000000509007"
REFSET_GB = "900000000000508004"

FSN_TAG_RE = re.compile(r"\(([^()]+)\)\s*$")


def find_file(snapshot_dir: str, pattern: str) -> str:
    matches = glob.glob(os.path.join(snapshot_dir, "**", pattern), recursive=True)
    if not matches:
        sys.exit(f"ERROR: no file found matching pattern {pattern} in {snapshot_dir}")
    if len(matches) > 1:
        print(f"WARNING: multiple matches for {pattern}, using the first:\n  " + "\n  ".join(matches))
    return matches[0]


def normalize(term: str) -> str:
    """lower + strip accents (NFKD) + collapse spaces. Matches the query normalization."""
    t = unicodedata.normalize("NFKD", term.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.split())


def semantic_tag(fsn_term: str) -> str | None:
    m = FSN_TAG_RE.search(fsn_term)
    return m.group(1).strip() if m else None


def load_active_concepts(path: str) -> set[str]:
    active: set[str] = set()
    with open(path, encoding="utf-8") as f:
        next(f)  # header
        for line in f:
            cols = line.rstrip("\n").split("\t")
            # id, effectiveTime, active, moduleId, definitionStatusId
            if cols[2] == "1":
                active.add(cols[0])
    return active


def build_fsn_tags(path: str, active_concepts: set[str]) -> dict[str, str]:
    """Pass 1: conceptId -> semantic_tag (from the active FSN)."""
    tags: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            cols = line.rstrip("\n").split("\t")
            # id, effectiveTime, active, moduleId, conceptId, languageCode, typeId, term, caseSignificanceId
            if cols[2] != "1" or cols[6] != TYPE_FSN:
                continue
            concept_id = cols[4]
            if concept_id not in active_concepts:
                continue
            tag = semantic_tag(cols[7])
            if tag:
                tags[concept_id] = tag
    return tags


def copy_descriptions(conn, path: str, active_concepts: set[str], fsn_tags: dict[str, str],
                      skip_ids: set[int] | None = None) -> int:
    """Pass 2: COPY of active descriptions (FSN + synonym) of active concepts.

    In append mode, skip_ids holds description ids already in the table (a few extension descriptions
    are shipped in the International edition too); we keep the existing row and don't re-COPY them.
    """
    n = 0
    copy_sql = (
        "COPY descriptions (id, concept_id, term, term_norm, type_id, semantic_tag, module_id) FROM STDIN"
    )
    with conn.cursor() as cur, cur.copy(copy_sql) as cp, open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if cols[2] != "1":
                continue
            type_id = cols[6]
            if type_id not in (TYPE_FSN, TYPE_SYNONYM):
                continue
            concept_id = cols[4]
            if concept_id not in active_concepts:
                continue
            if skip_ids is not None and int(cols[0]) in skip_ids:
                continue
            term = cols[7]
            cp.write_row((
                int(cols[0]),                 # id
                int(concept_id),              # concept_id
                term,                         # term
                normalize(term),              # term_norm
                int(type_id),                 # type_id
                fsn_tags.get(concept_id),     # semantic_tag (may be None)
                int(cols[3]),                 # module_id (RF2 col 3: moduleId) -> exclude_module scoping
            ))
            n += 1
            if n % 200_000 == 0:
                print(f"  ... {n:,} descriptions copied")
    return n


def mark_preferred(conn, path: str) -> None:
    """Loads the Language refset into a temp table and marks pref_us / pref_gb."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE lang_pref (desc_id BIGINT, refset BIGINT) ON COMMIT DROP"
        )
        copy_sql = "COPY lang_pref (desc_id, refset) FROM STDIN"
        n = 0
        with cur.copy(copy_sql) as cp, open(path, encoding="utf-8") as f:
            next(f)
            for line in f:
                cols = line.rstrip("\n").split("\t")
                # id, effectiveTime, active, moduleId, refsetId, referencedComponentId, acceptabilityId
                if cols[2] != "1" or cols[6] != ACCEPT_PREFERRED:
                    continue
                refset = cols[4]
                if refset not in (REFSET_US, REFSET_GB):
                    continue
                cp.write_row((int(cols[5]), int(refset)))
                n += 1
        print(f"  'preferred' rows loaded: {n:,}")
        cur.execute("CREATE INDEX ON lang_pref (desc_id)")
        cur.execute(
            "UPDATE descriptions d SET pref_us = true "
            "FROM lang_pref lp WHERE lp.desc_id = d.id AND lp.refset = %s",
            (int(REFSET_US),),
        )
        cur.execute(
            "UPDATE descriptions d SET pref_gb = true "
            "FROM lang_pref lp WHERE lp.desc_id = d.id AND lp.refset = %s",
            (int(REFSET_GB),),
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", help="Snapshot dir to load (default: SNOMED_SNAPSHOT_DIR from .env)")
    ap.add_argument("--append", action="store_true",
                    help="add these descriptions on top of what's loaded (skip TRUNCATE) — for extensions")
    args = ap.parse_args()

    load_dotenv()
    dsn = os.environ.get("PG_DSN")
    snapshot_dir = args.snapshot or os.environ.get("SNOMED_SNAPSHOT_DIR")
    if not dsn or not snapshot_dir:
        sys.exit("ERROR: set PG_DSN and SNOMED_SNAPSHOT_DIR in .env (or pass --snapshot)")

    concept_file = find_file(snapshot_dir, "sct2_Concept_Snapshot_*.txt")
    desc_file = find_file(snapshot_dir, "sct2_Description_Snapshot-en_*.txt")
    lang_file = find_file(snapshot_dir, "der2_cRefset_LanguageSnapshot-en_*.txt")

    print("Active concepts...")
    active_concepts = load_active_concepts(concept_file)
    print(f"  active: {len(active_concepts):,}")

    print("Pass 1: semantic tags from FSN...")
    fsn_tags = build_fsn_tags(desc_file, active_concepts)
    print(f"  tags: {len(fsn_tags):,}")

    with psycopg.connect(dsn) as conn:
        if args.append:
            print("Append mode: keeping existing rows (no TRUNCATE).")
        else:
            print("Truncating descriptions table...")
            with conn.cursor() as cur:
                cur.execute("TRUNCATE descriptions")

        skip_ids: set[int] | None = None
        if args.append:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM descriptions")
                skip_ids = {r[0] for r in cur}
            print(f"  existing description ids to skip on conflict: {len(skip_ids):,}")

        print("Pass 2: COPY of descriptions...")
        total = copy_descriptions(conn, desc_file, active_concepts, fsn_tags, skip_ids)
        print(f"  total copied: {total:,}")

        print("Building term_tsv (lexical channel)...")
        with conn.cursor() as cur:
            # In append mode only the newly-copied rows are missing term_tsv; don't reprocess the rest.
            cur.execute("UPDATE descriptions SET term_tsv = to_tsvector('unaccent_simple', term) "
                        "WHERE term_tsv IS NULL" if args.append else
                        "UPDATE descriptions SET term_tsv = to_tsvector('unaccent_simple', term)")

        print("Marking preferred terms (US/GB)...")
        mark_preferred(conn, lang_file)

        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE pref_us), "
                        "count(*) FILTER (WHERE type_id = %s) FROM descriptions",
                        (int(TYPE_FSN),))
            total_rows, pref_us_rows, fsn_rows = cur.fetchone()
    print("\nDone.")
    print(f"  descriptions:       {total_rows:,}")
    print(f"  preferred (US):     {pref_us_rows:,}")
    print(f"  FSN:                {fsn_rows:,}")
    print("\nNext: create lexical index -> "
          "docker compose exec -T db psql -U snomed -d snomed_search < sql/02_indexes.sql")


if __name__ == "__main__":
    main()
