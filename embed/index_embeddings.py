#!/usr/bin/env python3
"""
Phase 2 (step 2): generates embeddings for the terms and stores them in `descriptions.embedding`.

Strategy:
  - Deduplicates by `term_norm` (many descriptions share the same normalized form).
  - Encodes in batches with BioLORD-2023-M (MPS on Apple Silicon), normalizing (cosine = dot).
  - Dumps to a staging table (unlogged) in chunks to avoid accumulating everything in RAM.
  - UPDATE via join propagates the vector to all rows with the same term_norm.

Usage:
    python embed/index_embeddings.py                 # everything
    python embed/index_embeddings.py --limit 5000    # quick test

Afterwards, create the HNSW index (see sql/02_indexes.sql).
"""
from __future__ import annotations

import argparse
import os
import time

import psycopg
from dotenv import load_dotenv


def vec_to_pg(vec) -> str:
    """Textual representation that pgvector accepts via COPY: '[v1,v2,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="process only N unique terms (test)")
    ap.add_argument("--batch", type=int, default=256, help="encode batch size")
    ap.add_argument("--chunk", type=int, default=20000, help="terms per dump to staging")
    ap.add_argument("--column", default="embedding",
                    help="target vector column in descriptions (e.g. embedding_bge for an A/B encoder)")
    ap.add_argument("--dim", type=int, default=768, help="embedding dimensionality of the model")
    args = ap.parse_args()

    load_dotenv()
    dsn = os.environ["PG_DSN"]
    model_name = os.environ.get("EMBED_MODEL", "FremyCompany/BioLORD-2023-M")
    device = os.environ.get("EMBED_DEVICE", "cpu")

    from sentence_transformers import SentenceTransformer  # noqa: WPS433

    print(f"Loading {model_name} (device={device}) -> descriptions.{args.column} vector({args.dim})...")
    model = SentenceTransformer(model_name, device=device)

    with psycopg.connect(dsn) as conn:
        # For a non-default (A/B experiment) column, ensure it exists — reversible with DROP COLUMN.
        # The default production column is never touched by DDL here.
        if args.column != "embedding":
            with conn.cursor() as cur:
                cur.execute(f"ALTER TABLE descriptions ADD COLUMN IF NOT EXISTS {args.column} vector({args.dim})")
            conn.commit()
            print(f"ensured column descriptions.{args.column} vector({args.dim})")
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT term_norm FROM descriptions")
            terms = [r[0] for r in cur.fetchall()]
        if args.limit:
            terms = terms[: args.limit]
        total = len(terms)
        print(f"Unique terms to encode: {total:,}")

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS emb_stage")
            cur.execute(
                f"CREATE UNLOGGED TABLE emb_stage "
                f"(term_norm TEXT PRIMARY KEY, embedding vector({args.dim}))"
            )
        conn.commit()

        t0 = time.time()
        done = 0
        for start in range(0, total, args.chunk):
            chunk = terms[start : start + args.chunk]
            vecs = model.encode(
                chunk, batch_size=args.batch, normalize_embeddings=True,
                show_progress_bar=False,
            )
            with conn.cursor() as cur, cur.copy(
                "COPY emb_stage (term_norm, embedding) FROM STDIN"
            ) as cp:
                for term, vec in zip(chunk, vecs):
                    cp.write_row((term, vec_to_pg(vec)))
            conn.commit()
            done += len(chunk)
            rate = done / (time.time() - t0)
            eta = (total - done) / rate if rate else 0
            print(f"  {done:,}/{total:,}  ({rate:.0f}/s, ETA {eta/60:.1f} min)")

        print(f"Propagating vectors to descriptions.{args.column} (UPDATE join)...")
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE descriptions d SET {args.column} = s.embedding "
                "FROM emb_stage s WHERE d.term_norm = s.term_norm"
            )
            cur.execute("DROP TABLE emb_stage")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FILTER (WHERE {args.column} IS NOT NULL), count(*) FROM descriptions")
            with_emb, total_rows = cur.fetchone()
    idx = "ix_desc_emb" if args.column == "embedding" else f"ix_desc_{args.column}"
    print(f"\nDone. Rows with {args.column}: {with_emb:,}/{total_rows:,}")
    print(f"Next: create HNSW -> docker compose exec -T db psql -U snomed -d snomed_search "
          f"-c \"CREATE INDEX {idx} ON descriptions USING hnsw ({args.column} vector_cosine_ops);\"")


if __name__ == "__main__":
    main()
