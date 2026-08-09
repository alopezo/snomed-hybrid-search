# Semantic Search Design — Searching SNOMED CT descriptions

Design of a hybrid search engine (lexical *multi-prefix ignore-order* + semantic via embeddings)
so that a clinician in the EHR can find SNOMED terms even when they do not use the exact words.

**Goal:** solve the *vocabulary mismatch* — so that "infarto" / "ataque al corazón"
retrieve *Myocardial infarction* even though they share no lexical overlap.

## Decisions made
- **Single store:** PostgreSQL 16 + `pgvector` (lexical with `tsvector`/GIN, vector with HNSW, RRF fusion in SQL).
- **Language:** multilingual queries; the clinician's source language is selectable (ES/EN/FR/DE/PT/IT/NL/DA) and
  drives the translation prompt. SNOMED terms are in English.
- **Embedding model:** **BioLORD-2023-M** (biomedical + multilingual, 768 dims). See [02-biolord-notes.md](02-biolord-notes.md).
- **Role of the local LLM (gemma via Ollama, e.g. gemma3:12b):** does NOT vectorize (no `/v1/embeddings`). It is used for
  query translation/expansion and optional re-ranking. See [llm-setup.md](llm-setup.md).
- **Scale:** ~1.02M active descriptions loaded from International 20260601.

## Notes index
- [01-paper-triplet-bert-semantic-search.md](01-paper-triplet-bert-semantic-search.md) — in-depth analysis of the reference paper (*Semantic Search for Large Scale Clinical Ontologies*, CSIRO).
- [02-biolord-notes.md](02-biolord-notes.md) — BioLORD-2023, variants, and what happened after the paper.
- [03-implementation-plan.md](03-implementation-plan.md) — step-by-step plan: ETL → model → search → demo → evaluation (includes manual steps).
- [04-architecture-flow.md](04-architecture-flow.md) — detailed flow with Mermaid diagrams: how the three components (algorithmic, LLM, semantic index) cooperate.

## Sources
- Base paper: https://pmc.ncbi.nlm.nih.gov/articles/PMC8861757/
- BioLORD-2023 (JAMIA 2024): https://academic.oup.com/jamia/article/31/9/1844/7614965
- BioLORD-2023-M (Hugging Face): https://huggingface.co/FremyCompany/BioLORD-2023-M
