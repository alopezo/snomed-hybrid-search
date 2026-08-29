# Implementation plan — step by step

From the SNOMED release ETL to a hybrid-search demo app.
Marked **[MANUAL]** = a step performed by a person (install, download, licenses).
Marked **[AUTO]** = a script we can generate and run.

Release: point `SNOMED_SNAPSHOT_DIR` (in `.env`) at the `Snapshot` folder of any RF2 release, e.g.
`.../SnomedCT_InternationalRF2_PRODUCTION_<VERSION>/Snapshot`.

Local LLM server: any OpenAI-compatible chat endpoint via `GEMMA_URL` / `GEMMA_MODEL` (chat only; the
embedding model is separate). Example used during development: `gemma-4-26b-a4b-it` at `http://127.0.0.1:8080/v1`.

---

## Architecture (reminder)

```
query (any language, auto-detected)
  ├─ [gemma] translates→EN + expands abbreviations/localisms → EN terms (deduped)   (optional, cacheable)
  ├─ LEXICAL channel   : to_tsvector/to_tsquery('unaccent_simple', 'w:* & w:*')  (multi-prefix, order- & accent-independent)
  ├─ SEMANTIC channel: BioLORD-2023-M → 768d vector → pgvector kNN (cosine)
  └─ RRF FUSION (SQL) → boosts (preferred/type) → [optional cross-encoder rerank] → top-10
```
Single store: **PostgreSQL 16 + pgvector**.

## Repo structure

The repository root is `snomed-search/`; these design notes live under `docs/`. See the top-level
[README](../README.md) for the authoritative layout and the one-command `make` pipeline.

```
snomed-search/                 # repo root
  ├─ docker-compose.yml        # Postgres 16 + pgvector
  ├─ Makefile                  # one-command reproduction (make help)
  ├─ .env.example              # config template (DB, SNOMED path, model, LLM endpoint)
  ├─ sql/         01_schema.sql (auto-init) · 02_indexes.sql (GIN + HNSW)
  ├─ etl/         load_descriptions.py · smoke_test_lexical.py
  ├─ embed/       download_model.py · index_embeddings.py
  ├─ api/         search.py (hybrid + RRF + gemma, streaming) · server.py (FastAPI)
  ├─ demo/        index.html (live UI)
  └─ docs/        these design notes + Mermaid architecture diagrams
```

---

## PHASE 0 — Prerequisites (environment)

- [ ] **[MANUAL]** Confirm a valid SNOMED CT / UMLS license (required to use BioLORD). ✔️ already in place.
- [ ] **[MANUAL]** Install PostgreSQL 16 + pgvector.
  - Homebrew option:
    ```bash
    brew install postgresql@16 pgvector
    brew services start postgresql@16
    ```
  - Docker option (more isolated, recommended):
    ```bash
    docker run -d --name snomed-pg -e POSTGRES_PASSWORD=snomed \
      -p 5432:5432 -v snomed_pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
    ```
- [ ] **[MANUAL]** Create the database and user:
    ```bash
    createdb snomed_search    # or docker exec ... psql
    ```
- [ ] **[MANUAL]** Python environment (3.10+):
    ```bash
    python3 -m venv .venv && source .venv/bin/activate
    pip install -U sentence-transformers torch psycopg[binary] fastapi uvicorn python-dotenv httpx
    ```
    (On Apple Silicon Macs, `torch` ships with **MPS** support by default.)
- [ ] **[MANUAL]** Verify gemma is alive: `curl http://127.0.0.1:8080/health`.
- [ ] **[MANUAL]** Create `.env` with: `PG_DSN`, `SNOMED_SNAPSHOT_DIR`, `GEMMA_URL`, `EMBED_MODEL`.

---

## PHASE 1 — ETL: RF2 → PostgreSQL  ✅ EXECUTED (2026-08-07)

**Actual load results (International 20260601):**
- Active concepts: **381,856**
- Descriptions loaded (FSN + synonyms of active concepts): **1,024,825**
- US preferred: **763,712** (= exactly 2× concepts → 1 FSN + 1 preferred synonym each; sanity check ✔️)
- Multi-prefix order-independent lexical channel **verified** with `smoke_test_lexical.py`
  ("diab mell" → *Diabetes mellitus type 2*, "infarct myocard" → *myocardial infarction*).
- Code: [`etl/load_descriptions.py`](../etl/load_descriptions.py).

Inputs: `Terminology/sct2_Description_...`, `Refset/Language/der2_cRefset_Language_...`,
`Terminology/sct2_Concept_...`.

- [ ] **[AUTO]** `sql/01_schema.sql`: create extensions and table.
    ```sql
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS unaccent;
    -- Accent/case-insensitive, no-stemming config used by BOTH indexing and querying.
    CREATE TEXT SEARCH CONFIGURATION unaccent_simple (COPY = simple);
    ALTER TEXT SEARCH CONFIGURATION unaccent_simple
      ALTER MAPPING FOR asciiword, asciihword, hword_asciipart, word, hword, hword_part
      WITH unaccent, simple;
    CREATE TABLE descriptions (
      id BIGINT PRIMARY KEY, concept_id BIGINT, term TEXT, term_norm TEXT,
      type_id BIGINT, semantic_tag TEXT,
      pref_us BOOLEAN DEFAULT false, pref_gb BOOLEAN DEFAULT false,
      term_tsv tsvector, embedding vector(768)
    );
    ```
- [ ] **[AUTO]** `etl/load_descriptions.py`:
  1. Read `Concept` → set of `conceptId` with `active=1`.
  2. Read `Description` row by row (TSV, `\t`), filter `active=1`, `typeId ∈ {synonym, FSN}`,
     and active `conceptId`.
  3. Extract `semantic_tag` from the FSN (regex `\(([^)]+)\)$`).
  4. `term_norm = normalize(term)` in Python (used only as the embedding dedup key).
  5. Bulk `COPY` into the table (use `psycopg.copy` for speed; ~1M rows).
- [ ] **[AUTO]** build the lexical vector (inside the ETL) — accent/case folding via the config:
    ```sql
    UPDATE descriptions SET term_tsv = to_tsvector('unaccent_simple', term);
    ```
  - Mark `pref_us`/`pref_gb` with a join to the Language refset
    (acceptability `900000000000548007` = preferred; US refset `...509007`, GB `...508004`).
- [ ] **[AUTO]** `sql/02_indexes.sql` (lexical part):
    ```sql
    CREATE INDEX ix_desc_tsv ON descriptions USING gin (term_tsv);
    CREATE INDEX ix_desc_concept ON descriptions (concept_id);
    ```
- [ ] **Checkpoint:** the **lexical channel already works** (multi-prefix order-independent) without needing embeddings.
    ```sql
    SELECT term FROM descriptions WHERE term_tsv @@ to_tsquery('unaccent_simple','diab:* & mell:*') LIMIT 10;
    ```

---

## PHASE 2 — Embedding model + vector indexing

- [ ] **[MANUAL]** Download BioLORD-2023-M (once, ~0.5 GB, requires internet):
    ```bash
    huggingface-cli download FremyCompany/BioLORD-2023-M --local-dir models/biolord-2023-m
    ```
  (or let `sentence-transformers` download it automatically on first use via `download_model.py`.)
- [ ] **[AUTO]** `embed/index_embeddings.py`:
  1. `SELECT DISTINCT term_norm` (deduplicate ~1.4M → ~1.1M unique to save compute).
  2. `model.encode(batch, batch_size=64, normalize_embeddings=True, device="mps")`.
  3. Write vectors; propagate to all rows sharing the same `term_norm`.
  - **[MANUAL/decision]** run with gemma **unloaded** to free up GPU/RAM (overnight job, ~1–2 h).
- [ ] **[AUTO]** `sql/02_indexes.sql` (vector part, AFTER populating):
    ```sql
    CREATE INDEX ix_desc_emb ON descriptions USING hnsw (embedding vector_cosine_ops);
    ```
- [ ] **Checkpoint:** semantic kNN responds:
    ```sql
    SELECT term FROM descriptions ORDER BY embedding <=> :qvec LIMIT 10;
    ```

---

## PHASE 3 — Hybrid search core (RRF fusion)

### ⚑ Empirical finding (2026-08-07) — gemma ES→EN translation is ESSENTIAL, not optional
Tested against the real index (1M descriptions + HNSW):
- **Colloquial Spanish sent directly to the embedding = unreliable.** Examples that FAILED without translation:
  `azucar alta` → random substances; `presion alta` → **barometric** pressure (not blood pressure);
  `rinon inflamado` → *Rhinosinusitis* (confused "riñón"/kidney with "rhino-"). Only `tos con flema`
  got it right (*Productive cough*).
- **The same intent in clinical English = clear hits** (which is what gemma produces when translating):
  `heart attack` → **Myocardial infarction** [both]; `high blood sugar` → **Hyperglycemia**;
  `kidney inflammation` → **Nephritis**; `high blood pressure` → **Hypertensive disorder** [both].
- Cause: BioLORD is trained on *clinical sentences* and concept names, not on short lay slang
  or ambiguous queries. Translating into a clinical phrase disambiguates and grounds the vector.
- **Decision:** gemma (or any clinical ES→EN translator) becomes a **component of the critical path**
  for Spanish queries, not an extra. The semantic channel is sufficient on its own only for input
  that is already clinical/English. Measured query latency: ~50–300 ms (without gemma); the 1st call ~12 s
  (model load, mitigated with warmup on the server).
- The best results come out **[both]**: when lexical and semantic agree, RRF pushes them to #1.


- [ ] **[AUTO]** `api/search.py`:
  1. (optional) **Expansion with gemma**: short prompt → translates ES→EN + expands abbreviations.
     Cache per query. If gemma does not respond in time → proceed without expansion (non-blocking).
  2. Embed the query with BioLORD (query-time, ~10–40 ms).
  3. Run the RRF query (`sql/search_rrf.sql`) with a `to_tsquery` built from the words
     (`w1:* & w2:* ...`) + the query vector.
  4. Apply boosts (`pref_us`, FSN) and return top-10 grouped by `concept_id`.
- [ ] **[AUTO]** `sql/search_rrf.sql` (base already defined in the README/design): CTE `lex` + CTE `vec` +
      `1/(60+rank)` + boosts.
- [ ] **[optional] [AUTO]** cross-encoder rerank of the fused candidates (`BAAI/bge-reranker-v2-m3`,
  deterministic neural relevance — replaced the earlier LLM-as-reranker, which was inconsistent).
  Feed it the gemma expansion (the clinical phrase), not the raw lay query.

---

## PHASE 4 — Search demo app

- [ ] **[AUTO]** `api/server.py` (FastAPI): endpoint `GET /search?q=...&lang=auto`.
    ```bash
    uvicorn api.server:app --reload --port 8090
    ```
- [ ] **[AUTO]** `demo/index.html`: text box + live results (fetch to `/search`),
      showing the term, FSN, conceptId, semantic tag, and which channel it came from (lexical/semantic/both).
- [ ] **[MANUAL]** Open `http://127.0.0.1:8090` and try clinician queries:
      "ataque al corazon", "IAM", "azucar alta", "tos con flema".

---

## PHASE 5 — Evaluation (recommended, optional for the MVP)

- [ ] **[MANUAL]** Build a set of ~50–100 ES queries → expected conceptId (with a clinician).
- [ ] **[AUTO]** Script that computes **Hits@1/5/10, nDCG@10, MRR** (same methodology as the paper,
      credit via IS-A). Segment by lexical overlap to see where the semantic channel wins.
- [ ] **[AUTO]** Compare 3 configurations: lexical-only · semantic-only · hybrid RRF.

---

## Effort estimate

| Phase | Work | Approx. time |
|---|---|---|
| 0 Prerequisites | install Postgres/pgvector, venv, model | 1–2 h (mostly [MANUAL]) |
| 1 ETL | scripts + loading 1.4M rows | half a day dev + ~10–20 min of loading |
| 2 Embeddings | indexing 1.1M unique on M2 Pro (MPS) | ~1–2 h of compute (one time) |
| 3 Search | RRF + gemma integration | half a day dev |
| 4 Demo | API + HTML | 2–4 h dev |
| 5 Evaluation | set + metrics | 1 day (depends on the clinician) |

## Fast path to a demonstrable MVP
1. Phase 0 + Phase 1 → **lexical search working today** (multi-prefix order-independent).
2. Phase 2 + Phase 3 (without gemma) → **lexical+semantic hybrid**.
3. Phase 4 → **visible demo**.
4. gemma (expansion) + optional cross-encoder rerank, and Phase 5 → refinement.

## Consolidated MANUAL steps
1. Confirm SNOMED/UMLS license. ✔️
2. Install and start PostgreSQL 16 + pgvector (Homebrew or Docker).
3. Create the `snomed_search` DB.
4. Create the Python venv + `pip install`.
5. Fill in `.env` (DSN, paths, endpoints).
6. Download the BioLORD-2023-M model (~0.5 GB).
7. (Indexing) preferably with gemma unloaded; bring it back up afterward.
8. Open the demo and try real queries.
9. (Eval) build the query set with a clinician.
```
