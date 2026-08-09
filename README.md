# SNOMED CT hybrid search

A local, reproducible search engine over SNOMED CT descriptions that finds the right concept even
when the clinician doesn't use the exact words. It combines three components:

| # | Component | Role | Tech |
|---|-----------|------|------|
| ① | **Lexical (algorithmic)** | multi-word prefix match, order-independent | PostgreSQL `to_tsquery` + GIN |
| ② | **LLM (query understanding)** | translate the clinician's note to English (source language selectable), expand abbreviations/localisms, optional rerank | any OpenAI-compatible chat endpoint (e.g. gemma via Ollama) |
| ③ | **Semantic index** | retrieve by meaning (solves *vocabulary mismatch*) | BioLORD-2023-M + pgvector (HNSW) |

Results from ① and ③ are fused with **Reciprocal Rank Fusion (RRF)**. Full design, evaluation notes,
and Mermaid diagrams are in [`docs/`](docs/) — start with [docs/04-architecture-flow.md](docs/04-architecture-flow.md).

---

## Requirements

- **Docker** (runs Postgres 16 + pgvector; nothing else needs a container)
- **Python 3.10+**
- A **SNOMED CT RF2 release** (the `Snapshot` folder) — you provide this; it is **not** included
- A valid **SNOMED CT / UMLS license** (see [Licensing](#licensing))
- *Optional:* an **OpenAI-compatible chat LLM** endpoint for query translation and rerank
  (any server exposing `/v1/chat/completions`; the system degrades gracefully without it).
  Easiest setup: `make llm` (Ollama + a small gemma). See [docs/llm-setup.md](docs/llm-setup.md).

Embedding device: set `EMBED_DEVICE` in `.env` to `mps` (Apple Silicon), `cuda` (NVIDIA GPU), or `cpu`.

---

## Quickstart

```bash
git clone <your-fork-url> && cd snomed-search
cp .env.example .env          # then edit SNOMED_SNAPSHOT_DIR (and EMBED_DEVICE / GEMMA_* if needed)
make all                      # db + deps + ETL + embeddings + indexes
make serve                    # open http://127.0.0.1:8090
```

`make all` is the full pipeline. The long step is embedding (~1M terms; minutes on GPU/MPS, longer on
CPU) and it downloads the BioLORD model (~0.5 GB) once. Run `make help` to see every target.

Prefer to go step by step? Each stage is its own target:

```bash
make db-up            # start Postgres + pgvector (auto-creates .env, waits for healthy)
make install          # venv + pip install
make etl              # RF2 -> descriptions table (+ term_norm, semantic_tag, preferred flags)
make index-lexical    # GIN index  -> lexical channel usable now
make embed            # download model + encode terms into pgvector
make index-hnsw       # HNSW index -> semantic channel usable
make serve            # API + demo
```

---

## Reproduce with a NEW SNOMED release

This is the whole point of the packaging. The ETL locates the release files **by name pattern**
(`sct2_Concept_Snapshot_*`, `sct2_Description_Snapshot-en_*`, `der2_cRefset_LanguageSnapshot-en_*`)
recursively under `SNOMED_SNAPSHOT_DIR`, so a new **International** release needs **only a path change**:

```bash
# 1. Point at the new release's Snapshot directory
$EDITOR .env          # set SNOMED_SNAPSHOT_DIR=/path/to/SnomedCT_..._<NEW_VERSION>/Snapshot

# 2. Rebuild from clean
make reset            # wipe the old DB volume (optional but recommended)
make db-up etl index-lexical embed index-hnsw
make serve
```

Notes:
- **Other editions** (e.g. a national extension) may name the Description/Language files differently
  or use another language code; adjust the glob patterns in [`etl/load_descriptions.py`](etl/load_descriptions.py).
- Only **active** descriptions of **active** concepts are loaded (FSN + synonyms).

---

## The LLM (②) is optional and swappable

Easiest local setup (Ollama + a small, fast gemma):
```bash
make llm            # installs nothing you didn't ask for; starts Ollama and pulls the model
```
Full options (Ollama / MLX / llama.cpp) and model choices: [docs/llm-setup.md](docs/llm-setup.md).

`GEMMA_URL` / `GEMMA_MODEL` in `.env` point at any OpenAI-compatible chat endpoint. It is used for two
things: **translating/expanding** the query to English and the optional **rerank** step.

- **Without it**, the lexical + semantic hybrid still works: English queries match directly, and
  BioLORD embeds queries cross-lingually. You lose reliable handling of short Spanish lay phrases
  and the rerank. Calls are best-effort with an 8 s timeout and never block the core search.
- **To use a different model**, just change `GEMMA_MODEL` and `GEMMA_URL`.

---

## Repository layout

```
snomed-search/
├─ docker-compose.yml     # Postgres 16 + pgvector (shm_size set for HNSW parallel builds)
├─ Makefile               # one-command reproduction (make help)
├─ .env.example           # configuration template (copy to .env)
├─ requirements.txt
├─ sql/
│  ├─ 01_schema.sql        # extensions + descriptions table (auto-run on first DB start)
│  └─ 02_indexes.sql       # GIN + HNSW index definitions (reference)
├─ etl/
│  ├─ load_descriptions.py    # RF2 -> table + tsvector + preferred-term flags
│  └─ smoke_test_lexical.py   # lexical-channel sanity check
├─ embed/
│  ├─ download_model.py       # download + validate BioLORD (checks dim + ES→EN similarity)
│  └─ index_embeddings.py     # encode unique terms -> pgvector (dedup + staging + join)
├─ api/
│  ├─ search.py               # hybrid search: gemma -> lexical + semantic -> RRF -> (rerank); streaming
│  └─ server.py               # FastAPI: /api/search, /api/search_stream, static demo
├─ demo/
│  └─ index.html              # live search UI (language select, stages, timings, rerank toggle, animated reorder)
├─ scripts/
│  └─ serve-llm.sh            # one-command local LLM (Ollama) for translation + rerank
└─ docs/                      # design, paper references, Mermaid diagrams, docs/llm-setup.md
```

---

## Execution architecture: native vs Docker

**Only the database runs in Docker.** All Python (ETL, embeddings, API) runs **natively**, which lets
BioLORD use the host GPU/MPS and reach the LLM and the release files directly.

| Component | Where | Talks to the LLM |
|---|---|---|
| Postgres + pgvector | Docker | never |
| Python (ETL, embed, API) | native venv | `GEMMA_URL` directly |

The only cross-boundary call is native Python → Postgres, via the published port (`127.0.0.1:5432`).
If you later containerize the Python app, a container can't see the host's `127.0.0.1` — use
`GEMMA_URL=http://host.docker.internal:8080/v1` and add `extra_hosts: ["host.docker.internal:host-gateway"]`.

---

## Operations

| Action | Command |
|---|---|
| Stop DB (keep data) | `make down` |
| Start DB again | `docker compose start` |
| DB logs | `docker compose logs -f db` |
| psql console | `docker compose exec db psql -U snomed -d snomed_search` |
| Full reset (wipe data) | `make reset` |

The `pgdata` Docker volume persists data across restarts. `sql/01_schema.sql` only runs when the
volume is empty; after a schema change, `make reset` re-initializes.

---

## Licensing

- **Code:** MIT (suggested — set the license you want before publishing).
- **SNOMED CT content is NOT included in this repo** and must not be committed. Use of SNOMED CT
  requires a license from **SNOMED International** (free for Members and, in many countries, for
  research/development via the **UMLS Metathesaurus**). Download your own RF2 release and point
  `SNOMED_SNAPSHOT_DIR` at it.
- **BioLORD-2023-M** derives from UMLS/SNOMED; using it requires appropriate UMLS/SNOMED licensing.
- `.gitignore` already excludes `.env`, `.venv/`, `models/`, and Python caches. **Never commit** the
  SNOMED release, the `pgdata` volume, or generated embeddings.
