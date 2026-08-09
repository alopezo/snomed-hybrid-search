# SNOMED CT hybrid search — reproducible pipeline.
# Usage: `make help`. Typical fresh run: edit .env, then `make all && make serve`.

POSTGRES_USER ?= snomed
POSTGRES_DB   ?= snomed_search
PY   := .venv/bin/python
PIP  := .venv/bin/pip
PSQL := docker compose exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env: ## Create .env from the template (then edit SNOMED_SNAPSHOT_DIR)
	@test -f .env || (cp .env.example .env && \
		echo ">> Created .env — edit SNOMED_SNAPSHOT_DIR to point at your release Snapshot dir.")

install: ## Create the venv and install Python deps
	python3 -m venv .venv
	$(PIP) install -U pip
	$(PIP) install -U -r requirements.txt

db-up: .env ## Start Postgres + pgvector and wait until healthy
	docker compose up -d
	@echo "waiting for db to become healthy..."
	@until [ "$$(docker inspect -f '{{.State.Health.Status}}' snomed-search-db 2>/dev/null)" = "healthy" ]; do sleep 2; done
	@echo "db healthy."

download: .env ## Download+extract the latest International release from MLDS (needs SNOMED_USER/PASSWORD)
	$(PY) etl/syndication_downloader.py

etl: ## Load the SNOMED release into Postgres (reads SNOMED_SNAPSHOT_DIR from .env)
	$(PY) etl/load_descriptions.py

index-lexical: ## Build the lexical (GIN) index
	$(PSQL) -c "CREATE INDEX IF NOT EXISTS ix_desc_tsv ON descriptions USING gin (term_tsv); CREATE INDEX IF NOT EXISTS ix_desc_concept ON descriptions (concept_id); ANALYZE descriptions;"

embed: ## Download the embedding model and encode all terms into pgvector
	$(PY) embed/download_model.py
	$(PY) embed/index_embeddings.py

index-hnsw: ## Build the semantic (HNSW) index
	$(PSQL) -c "SET maintenance_work_mem='2GB'; CREATE INDEX IF NOT EXISTS ix_desc_emb ON descriptions USING hnsw (embedding vector_cosine_ops); ANALYZE descriptions;"

all: db-up install etl index-lexical embed index-hnsw ## Full reproduction pipeline
	@echo ">> Pipeline complete. Start the app with: make serve"

llm: ## Start a small local LLM (Ollama) for translation + rerank (see docs/llm-setup.md)
	bash scripts/serve-llm.sh

serve: ## Run the API + demo at http://127.0.0.1:8090
	$(PY) -m uvicorn api.server:app --host 127.0.0.1 --port 8090

smoke: ## Quick lexical sanity check
	$(PY) etl/smoke_test_lexical.py "diab mell"

down: ## Stop the database container (keeps data)
	docker compose stop

reset: ## Delete the DB volume and re-init (DESTROYS loaded data + embeddings)
	docker compose down -v

.PHONY: help install db-up download etl index-lexical embed index-hnsw all llm serve smoke down reset
