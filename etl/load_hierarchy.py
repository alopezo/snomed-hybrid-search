#!/usr/bin/env python3
"""
Phase 1b — Hierarchy: RF2 IS-A relationships -> transitive ancestors per concept.

Builds `concept_ancestors(concept_id, ancestors bigint[])` where `ancestors` holds every transitive
IS-A ancestor of the concept PLUS the concept itself (descendant-or-self / `<<` semantics). A GIN
index makes "is C under X?" a single indexed lookup: `ancestors @> ARRAY[X]` — no per-query
hierarchy traversal. Rebuild once per SNOMED release (like the rest of the ETL).

Usage:
    cd snomed-search && source .venv/bin/activate
    python etl/load_hierarchy.py
"""
from __future__ import annotations

import glob
import os
import sys

import psycopg
from dotenv import load_dotenv

IS_A = "116680003"


def find_file(snapshot_dir: str, pattern: str) -> str:
    matches = glob.glob(os.path.join(snapshot_dir, "**", pattern), recursive=True)
    if not matches:
        sys.exit(f"ERROR: no file found matching {pattern} in {snapshot_dir}")
    return matches[0]


def load_active_concepts(path: str) -> set[int]:
    active: set[int] = set()
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            c = line.rstrip("\n").split("\t")
            if c[2] == "1":
                active.add(int(c[0]))
    return active


def load_parents(path: str, active: set[int]) -> dict[int, list[int]]:
    """child -> [direct parents] from active IS-A relationships between active concepts."""
    parents: dict[int, list[int]] = {}
    with open(path, encoding="utf-8") as f:
        next(f)
        for line in f:
            c = line.rstrip("\n").split("\t")
            # id, effectiveTime, active, moduleId, sourceId, destinationId, group, typeId, ...
            if c[2] != "1" or c[7] != IS_A:
                continue
            child, parent = int(c[4]), int(c[5])
            if child in active and parent in active:
                parents.setdefault(child, []).append(parent)
    return parents


def build_ancestors(active: set[int], parents: dict[int, list[int]]) -> dict[int, set[int]]:
    """Transitive ancestors (incl. self) per concept, memoized. IS-A is a DAG (no cycles)."""
    sys.setrecursionlimit(100_000)
    cache: dict[int, set[int]] = {}

    def anc(c: int) -> set[int]:
        hit = cache.get(c)
        if hit is not None:
            return hit
        acc = {c}
        for p in parents.get(c, ()):
            acc |= anc(p)
        cache[c] = acc
        return acc

    for c in active:
        anc(c)
    return cache


def main() -> None:
    load_dotenv()
    dsn = os.environ["PG_DSN"]
    snapshot = os.environ["SNOMED_SNAPSHOT_DIR"]

    concept_file = find_file(snapshot, "sct2_Concept_Snapshot_*.txt")
    rel_file = find_file(snapshot, "sct2_Relationship_Snapshot_*.txt")

    print("Active concepts...")
    active = load_active_concepts(concept_file)
    print(f"  active: {len(active):,}")

    print("Loading IS-A edges...")
    parents = load_parents(rel_file, active)
    print(f"  concepts with a parent: {len(parents):,}")

    print("Computing transitive ancestors (incl. self)...")
    ancestors = build_ancestors(active, parents)
    total = sum(len(v) for v in ancestors.values())
    print(f"  ancestor pairs (closure size): {total:,}  (~{total/len(active):.1f} per concept)")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        print("Writing concept_ancestors...")
        cur.execute("DROP TABLE IF EXISTS concept_ancestors")
        cur.execute("CREATE TABLE concept_ancestors (concept_id BIGINT PRIMARY KEY, ancestors BIGINT[])")
        with cur.copy("COPY concept_ancestors (concept_id, ancestors) FROM STDIN") as cp:
            for cid, anc in ancestors.items():
                cp.write_row((cid, "{" + ",".join(map(str, anc)) + "}"))
        print("Building GIN index on ancestors...")
        cur.execute("CREATE INDEX ix_ca_ancestors ON concept_ancestors USING gin (ancestors)")
        cur.execute("ANALYZE concept_ancestors")
        conn.commit()

    print(f"\nDone. concept_ancestors: {len(ancestors):,} rows.")
    print("Filter usage:  WHERE ancestors @> ARRAY[<concept_id>]   (descendant-or-self of that concept)")


if __name__ == "__main__":
    main()
