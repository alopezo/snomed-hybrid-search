# A/B experiment: BGE-M3 vs BioLORD as the semantic encoder

Tests whether a modern generalist encoder (`BAAI/bge-m3`, 1024-d, 2024) beats the older domain-adapted
`FremyCompany/BioLORD-2023-M` (768-d, 2023) on SNOMED CT concept normalization.

**Fully reversible and isolated:** the production `descriptions.embedding` (BioLORD) column and its
`ix_desc_emb` index are never touched; the running server keeps using BioLORD throughout. The experiment
lives entirely in one extra column + one extra index, both dropped by the teardown below. No change to
`api/search.py` or `api/server.py`.

The number to beat: **BioLORD semantic-only, pp-off, rr-on → acc@1 0.607** (`distemist_search_eval_chsemantic_ppOFF_rrON.json`).

---

## 0. (optional) sanity-check the eval harness against BioLORD

Reproduce the BioLORD semantic-only number through the new eval path (should land near acc@1 0.607):

```
cd snomed-search
.venv/bin/python validation/eval_semantic_backend.py --corpus distemist --n 100 \
    --model FremyCompany/BioLORD-2023-M --column embedding
```

## 1. Encode SNOMED with BGE-M3 into a side column  (the long step, ~1–3 h on MPS)

The encoder auto-creates `descriptions.embedding_bge vector(1024)` (only because the column name is
non-default; the production `embedding` column is never altered):

```
cd snomed-search
EMBED_MODEL=BAAI/bge-m3 EMBED_DEVICE=mps \
  .venv/bin/python embed/index_embeddings.py --column embedding_bge --dim 1024
```

## 2. Build the experiment HNSW index

```
docker compose exec -T db psql -U snomed -d snomed_search \
  -c "SET maintenance_work_mem='2GB'; CREATE INDEX ix_desc_embedding_bge ON descriptions USING hnsw (embedding_bge vector_cosine_ops); ANALYZE descriptions;"
```

## 3. Evaluate BGE-M3 (semantic-only) and compare

```
cd snomed-search
EMBED_DEVICE=mps .venv/bin/python validation/eval_semantic_backend.py \
    --corpus distemist --n 100 --model BAAI/bge-m3 --column embedding_bge

.venv/bin/python validation/eval_semantic_backend.py --compare
```

Decision: if BGE-M3 clearly beats BioLORD's 0.607, integrate it into the full pipeline (fusion + rerank);
otherwise keep BioLORD. Either way, run the teardown to reclaim space.

## 4. Teardown — leaves zero residue

```
docker compose exec -T db psql -U snomed -d snomed_search -c \
  "DROP INDEX IF EXISTS ix_desc_embedding_bge; ALTER TABLE descriptions DROP COLUMN IF EXISTS embedding_bge; VACUUM (ANALYZE) descriptions;"
```

Optional: delete the experiment result files (`validation/results/*_sembackend_*.json`). The
`--column/--dim` flags on `embed/index_embeddings.py` and this eval script are general-purpose (reusable
for any future encoder A/B), so they are kept.

## Footprint while the experiment is live

+1024-d column (TOAST, ~4 GB) + HNSW index (~5 GB) ≈ **~9 GB** extra, all reclaimed by the teardown.
Loading BGE-M3 adds ~2.3 GB RAM to the eval process only (not the server).
