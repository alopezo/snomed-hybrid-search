# Hybrid SNOMED CT search — architecture & flow

Reference document for the system we built. It describes how the **three components** cooperate to turn
a clinician's query (colloquial Spanish or English) into the correct SNOMED concept, even when they do
not use the exact words.

The three components:

| # | Component | Nature | Role | Technology |
|---|-----------|--------|------|------------|
| 1 | **Algorithmic (lexical)** | Deterministic | Multi-word prefix matching, order-independent | PostgreSQL `tsvector`/`to_tsquery` + GIN |
| 2 | **LLM (query understanding)** | Generative | Translates the clinician's note (source language selectable) to English and expands abbreviations/localisms | gemma via Ollama (e.g. gemma3:12b), local, OpenAI-compatible |
| 3 | **Semantic index** | Vector | Retrieves by *meaning*, not by letters | BioLORD-2023-M + pgvector HNSW (cosine) |

The **RRF fusion** combines the rankings from (1) and (3); component (2) **prepares** the input for both.

---

## 1. End-to-end flow

```mermaid
flowchart TD
    Q["🩺 Clinician query<br/>colloquial ES or EN<br/>e.g. 'azucar alta'"]

    subgraph LLM["② LLM · gemma via Ollama (query understanding)"]
        direction TB
        G["Translate ES→EN + expand abbreviations<br/>'azucar alta' → 'high blood sugar hyperglycemia'"]
        GF{"gemma answered<br/>(8s timeout)?"}
        G --> GF
    end

    Q --> G
    ST["search_text = EN translation (or raw query if gemma fails)"]
    GF -->|yes| ST
    GF -->|no / disabled| ST

    subgraph ALG["① Algorithmic · LEXICAL channel (deterministic)"]
        direction TB
        L1["Normalize: lower + unaccent"]
        L2["Tokenize → prefixes:<br/>'high:* & blood:* & sugar:*'<br/>(AND, order-independent)"]
        L3[("PostgreSQL<br/>tsvector @@ to_tsquery<br/>GIN index")]
        L4["Top-200 by ts_rank"]
        L1 --> L2 --> L3 --> L4
    end

    subgraph SEM["③ Semantic index · VECTOR channel"]
        direction TB
        S1["BioLORD-2023-M<br/>encode(search_text) → 768d vector<br/>(normalized)"]
        S2[("pgvector<br/>embedding <=> qvec<br/>HNSW index, cosine")]
        S3["Top-200 by cosine distance"]
        S1 --> S2 --> S3
    end

    ST --> L1
    ST --> S1

    subgraph FUS["Fusion + ranking"]
        direction TB
        R["RRF: score = 1/(60+rank_lex) + 1/(60+rank_vec)<br/>+ 0.01 boost if preferred term"]
        D["Dedup by concept_id<br/>(best description per concept)"]
        FSN["Attach the concept's FSN"]
        R --> D --> FSN
    end

    L4 --> R
    S3 --> R

    OUT["📋 Top-K SNOMED concepts<br/>with channel (lexical/semantic/both) and score"]
    FSN --> OUT

    classDef llm fill:#4a148c,stroke:#ce93d8,color:#fff
    classDef alg fill:#1b5e20,stroke:#a5d6a7,color:#fff
    classDef sem fill:#0d47a1,stroke:#90caf9,color:#fff
    classDef fus fill:#e65100,stroke:#ffcc80,color:#fff
    class LLM,G,GF llm
    class ALG,L1,L2,L3,L4 alg
    class SEM,S1,S2,S3 sem
    class FUS,R,D,FSN fus
```

---

## 2. Sequence with real (measured) latencies

```mermaid
sequenceDiagram
    autonumber
    participant U as Clinician
    participant API as API (FastAPI)
    participant G as gemma (LLM)
    participant B as BioLORD (embeddings)
    participant PG as PostgreSQL (lexical + vector)

    U->>API: GET /api/search?q=azucar alta
    API->>G: Translate + expand (ES→EN)
    G-->>API: "high blood sugar hyperglycemia"  (~hundreds of ms)
    Note over API: search_text = translation

    par Lexical channel
        API->>PG: to_tsquery('high:* & blood:* & sugar:*')  (GIN)
        PG-->>API: top-200 (ts_rank)
    and Semantic channel
        API->>B: encode(search_text) → 768d vector  (~10-40 ms MPS)
        B-->>API: qvec
        API->>PG: embedding <=> qvec  (HNSW)
        PG-->>API: top-200 (cosine)
    end

    API->>API: RRF + dedup + FSN
    API-->>U: Top-K concepts (~50-300 ms without gemma)
```

> **Latency note:** the lexical+vector core answers in ~50–300 ms. gemma adds the largest cost
> (hundreds of ms to seconds); therefore it is cached per query and its failure is **non-blocking**
> (if it does not respond within 8 s, the raw query is used). The first query after startup loads the
> model (~12 s), mitigated with a *warmup* when the server starts.

---

## 3. Component details

### ① Algorithmic — lexical channel (`to_tsquery` multi-prefix, order-independent)
- **What it does:** deterministic matching by **prefixes of all words**, in **any order**.
  `"diab mell"` → `to_tsquery('simple', 'diab:* & mell:*')` → finds *Diabetes mellitus*.
- **How:** column `term_tsv = to_tsvector('simple', term_norm)` + **GIN** index. The `&` requires that
  **all** words appear (high precision); the `:*` allows prefixes (tolerates half-typed words).
- **Strength:** exact, fast, explainable; ideal when the clinician uses the correct vocabulary (or English).
- **Weakness:** blind to meaning. `"azucar alta"` shares no letters with *Hyperglycemia* → 0 results.
  That is why it needs component ② to translate first.
- **Background:** BM25-style lexical retrieval is the standard baseline in [1]; SNOMED's own description
  search uses this same word-prefix-any-order strategy.

### ② LLM — gemma (query understanding / normalization)
- **What it does:** translates the clinician's note from the **selected source language** to English and
  expands abbreviations and localisms into a **standard English clinical phrase**. `"IAM"` (ES) →
  *acute myocardial infarction*; `"EPOC reagudizada"` (ES) → *acute exacerbation of COPD*. The source
  language is passed in explicitly (a UI dropdown), which disambiguates acronyms (e.g. "EPOC" in ES = COPD).
- **Why it is essential** (empirical finding): BioLORD embeds clinical phrases well, but **poorly**
  embeds short Spanish slang. Without translation, `"presion alta"` fell onto *barometric pressure*;
  translated, it correctly hits *Hypertensive disorder*. gemma feeds **BOTH** channels (not only lexical).
- **How:** local Chat Completions call, `temperature=0`, prompt asking for English terms only.
  Best-effort: on failure/timeout the system degrades to the raw query (works for EN input).
- **Does not:** generate embeddings (gemma exposes no `/v1/embeddings`); that is component ③.
- **Background:** the vocabulary-mismatch problem this addresses is well documented for EHR search [4].

### ③ Semantic index — BioLORD + pgvector (retrieval by meaning)
- **What it does:** represents each description as a **768d vector**; retrieves by **cosine proximity**,
  capturing synonyms and paraphrases even when they share no letters.
- **How:** BioLORD-2023-M (biomedical + multilingual, EN/ES among 7 languages) encodes `search_text`;
  pgvector searches neighbors with an **HNSW** index. 1,024,825 descriptions vectorized.
- **Strength:** solves the *vocabulary mismatch* (the project's goal).
- **Weakness:** with ambiguous/colloquial input it loses precision → relies on ② to disambiguate.
- **Background:** the model is BioLORD-2023 [2]; the synonym-alignment idea behind it traces to
  SapBERT [3]; the fine-tuned Triplet-BERT in [1] demonstrated the same principle on SNOMED.

### Fusion — RRF (Reciprocal Rank Fusion)
- Combines the two rankings without calibrating disparate score scales:
  `score(doc) = 1/(60 + rank_lexical) + 1/(60 + rank_semantic) + 0.01·[is_preferred]`.
- A concept appearing in **both** channels rises to the top → `both` badge (highest confidence).
- Then **dedup by concept** (keep the best description) and attach the **FSN** for display.
- **Background:** RRF is the method of Cormack, Clarke & Büttcher [5]; hybrid lexical+semantic retrieval
  is the dominant pattern in the clinical-ontology search literature [1].

---

## 4. How the components cover each other's weaknesses

```mermaid
flowchart LR
    subgraph problem["Problematic query"]
        P1["'azucar alta'<br/>(slang, no shared letters<br/>with the English term)"]
    end
    P1 -->|"② translates"| T["'high blood sugar<br/>hyperglycemia'"]
    T -->|"① prefixes"| Lx["lexical: may miss if it<br/>ANDs too many words"]
    T -->|"③ vector"| Vx["semantic: 'high blood sugar'<br/>≈ Hyperglycemia ✓"]
    Lx --> RRF["RRF picks the best<br/>of each channel"]
    Vx --> RRF
    RRF --> OK["✓ Hyperglycemia (disorder)"]

    classDef llm fill:#4a148c,stroke:#ce93d8,color:#fff
    classDef alg fill:#1b5e20,stroke:#a5d6a7,color:#fff
    classDef sem fill:#0d47a1,stroke:#90caf9,color:#fff
    class T llm
    class Lx alg
    class Vx sem
```

**Synergy summary:**
- ① provides exact **precision** (and explainability) when the vocabulary matches.
- ② provides **understanding** of the clinician's language (idiom, slang, abbreviations).
- ③ provides **retrieval by meaning** (the heart of the *vocabulary mismatch*).
- **RRF** means it is enough for **one** channel to be right for the correct concept to rise.

---

## 5. Code traceability

| Flow element | Where |
|---|---|
| `search()` orchestration | [`api/search.py`](../api/search.py) |
| ② `gemma_expand()` | `api/search.py` |
| ① `to_prefix_query()` + `RRF_SQL` (CTE `lex`) | `api/search.py` |
| ③ `embed_query()` (BioLORD) + `RRF_SQL` (CTE `vec`) | `api/search.py` |
| RRF fusion + dedup + FSN | `RRF_SQL` (CTEs `fused`/`scored`/`best`) |
| API + demo | [`api/server.py`](../api/server.py), [`demo/index.html`](../demo/index.html) |

---

## 6. References (papers)

1. **Semantic Search for Large Scale Clinical Ontologies** — Chang, Mai, et al. (CSIRO). The closest prior
   work: hybrid lexical (BM25) + semantic (Triplet-BERT over BioBERT) search over SNOMED CT; source of the
   *low lexical-overlap* finding, the Hits@K/nDCG/MRR evaluation, and the synonym-from-hierarchy training
   idea. https://pmc.ncbi.nlm.nih.gov/articles/PMC8861757/
   (Detailed notes: [01-paper-triplet-bert-semantic-search.md](01-paper-triplet-bert-semantic-search.md).)
2. **BioLORD-2023: Semantic Textual Representations Fusing LLM and Clinical Knowledge Graph Insights** —
   Remy, Demuynck, et al., *JAMIA* 31(9):1844, 2024. The embedding model we use (component ③); multilingual
   variant covers ES/EN. https://academic.oup.com/jamia/article/31/9/1844/7614965 · arXiv:2311.16075
   (Notes: [02-biolord-notes.md](02-biolord-notes.md).)
3. **Self-Alignment Pretraining for Biomedical Entity Representations (SapBERT)** — Liu, Shareghi, et al.,
   NAACL 2021. Synonym-aligned biomedical embeddings; the entity-linking + reranking pattern.
   https://aclanthology.org/2021.naacl-main.334/
4. **Designing and Testing a Search Engine for Clinical Notes in EHRs** — NCBI Bookshelf NBK613172.
   Frames the *vocabulary mismatch* that component ② addresses. https://www.ncbi.nlm.nih.gov/books/NBK613172/
5. **Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods** — Cormack, Clarke &
   Büttcher, *SIGIR* 2009. The RRF fusion method. https://dl.acm.org/doi/10.1145/1571941.1572114
6. **The Probabilistic Relevance Framework: BM25 and Beyond** — Robertson & Zaragoza, 2009. The lexical
   ranking baseline referenced by component ①. https://dl.acm.org/doi/10.1561/1500000019
