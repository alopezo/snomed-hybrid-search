#!/usr/bin/env python3
"""
Backfill a `module_id` column on the `descriptions` table from the RF2 Description snapshots, so searches
can be scoped to (or filtered against) a SNOMED module — e.g. to EXCLUDE an appended extension such as the
LOINC Extension (module 11010000107) without deleting its rows or rebuilding embeddings.

Fresh loads no longer need this: `load_descriptions.py` now populates `module_id` at COPY time from the
same RF2 col 3, and `sql/01_schema.sql` declares the column. Use this script ONLY to backfill a database
that was loaded BEFORE that change, or to (re)assign modules after appending an extension to an existing
base without a full reload.

WARNING — run this before building the HNSW index, or expect a long, blocking run. The `embedding` column
lives on `descriptions`, so UPDATEing every row rewrites each tuple into the HNSW graph (MVCC): on a base
with the index already built this took >10 min and held an exclusive lock on the table the whole time.
On a fresh load the column is filled at COPY time (before `make index-hnsw`), so this cost never arises.

Pure metadata backfill: it reads `id` (col 0) and `moduleId` (col 3) from each snapshot's
`sct2_Description_Snapshot*` file, loads them into a temp table, and UPDATEs `descriptions.module_id`
by matching description `id`. It never touches term/embedding/tsvector.

    cd snomed-search && .venv/bin/python etl/backfill_module_id.py \
        --snapshot "$SNOMED_SNAPSHOT_DIR" \
        --snapshot ~/Downloads/SnomedCT_LOINCExtension_PRODUCTION_LO1010000_20260321T120000Z/Snapshot

Pass every snapshot that was loaded (International + each appended extension) in LOAD ORDER — the same
order you loaded them (International first, extensions after). On a description `id` present in more than
one snapshot (the LOINC Extension re-issues ~3.9k International description ids), the EARLIER snapshot
wins, mirroring `load_descriptions.py --append`, which skips ids already in the table. So a reused
International description keeps its International module and is NOT treated as LOINC. Idempotent.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import psycopg
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))


def find_desc_file(snapshot_dir: str) -> str:
    m = glob.glob(os.path.join(snapshot_dir, "**", "sct2_Description_Snapshot*.txt"), recursive=True)
    if not m:
        sys.exit(f"ERROR: no sct2_Description_Snapshot*.txt under {snapshot_dir}")
    return m[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", action="append", required=True,
                    help="a Snapshot dir (repeat for International + each extension)")
    args = ap.parse_args()
    dsn = os.environ["PG_DSN"]

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE descriptions ADD COLUMN IF NOT EXISTS module_id BIGINT")
        temps: list[str] = []
        for i, snap in enumerate(args.snapshot):
            path = find_desc_file(snap)
            t = f"tmp_mod_{i}"
            cur.execute(f"CREATE TEMP TABLE {t} (id BIGINT PRIMARY KEY, module_id BIGINT) ON COMMIT DROP")
            n = 0
            with cur.copy(f"COPY {t} (id, module_id) FROM STDIN") as cp:
                with open(path, encoding="utf-8") as f:
                    next(f)  # header
                    for line in f:
                        c = line.rstrip("\n").split("\t")
                        if len(c) < 4:
                            continue
                        cp.write_row((int(c[0]), int(c[3])))
                        n += 1
            temps.append(t)
            print(f"  [{i}] {os.path.basename(path)}: {n:,} description rows")
        # Apply in REVERSE load order, so the EARLIEST snapshot (International) is written LAST and wins on
        # any shared id (matches --append skip-existing semantics).
        updated = 0
        for t in reversed(temps):
            cur.execute(f"UPDATE descriptions d SET module_id = t.module_id FROM {t} t WHERE d.id = t.id")
            updated += cur.rowcount
        cur.execute("CREATE INDEX IF NOT EXISTS descriptions_module_id_idx ON descriptions (module_id)")
        conn.commit()
        cur.execute("SELECT module_id, count(*) FROM descriptions GROUP BY 1 ORDER BY 2 DESC")
        rows = cur.fetchall()
    print(f"updated {updated:,} rows. module_id distribution in descriptions:")
    for mid, cnt in rows:
        print(f"  {str(mid):>16}  {cnt:,}")


if __name__ == "__main__":
    main()
