# Paper: *Semantic Search for Large Scale Clinical Ontologies*

**Source:** https://pmc.ncbi.nlm.nih.gov/articles/PMC8861757/ (group affiliated with CSIRO / e-health, Australia)
**Why it is the reference:** it builds exactly the kind of search engine where a user's query
retrieves SNOMED CT concepts that are semantically close even when they do **not** match lexically.
It is the work closest to our objective in the EHR.

---

## 1. Problem they tackle
- Mapping free-text clinical text to large ontologies (SNOMED CT, "340,000+ concepts").
- *String matching* fails when the query and the ontology use **different vocabularies**
  (synonyms, different semantic expressions). ← our *vocabulary mismatch*.

## 2. Method / architecture

### Lexical baseline
- **Elasticsearch BM25** (IDF·TF). Removes stopwords.
- Key demonstrated limitation: "it only works well when the query uses the same vocabulary
  as the ontology".

### Semantic: **Triplet-BERT**
- BERT base = **BioBERT-Base v1.1**, **MEAN** pooling → **768-dim** vectors.
- *Triplet* architecture (anchor / positive / negative) with shared weights.
- Similarity at inference: **cosine**. During training: Euclidean distance.
- **Loss (triplet):** `max(||V_a - V_pos|| - ||V_a - V_neg|| + m, 0)`, margin `m = 0.1`.

### Training data generation (Algorithm 1) — reusable idea
Directly from the ontology hierarchy, without external NLI datasets:
- **anchor** = one label of the concept.
- **positive** = another label/synonym of the **same** concept.
- **negative** = label of a **parent or sibling** (chosen at random).
- Result: ~**4 million triplets** from SNOMED CT + HPO. 90/5/5 split.
- Training: 5 epochs, batch 32, Adam lr 2e-5, 10% warm-up. ~40 h on 1 GPU (32 GB).

### ⚠️ Fusion: there is NONE
The paper evaluates 5 systems **separately** (BM25, Word2Vec-avg, BioBERT-CLS, BioBERT-MEAN,
Triplet-BERT). **It does not use RRF or weighting.** The paper itself **recommends** a hybrid
deployment (see §6) — but the fusion is something we have to contribute ourselves. → here, RRF in Postgres.

## 3. Data and scale
- Target ontology: **SNOMED CT** (340k+ concepts).
- 5 benchmarks: `cadec2sct` (2,036 q, adverse-drug-reaction forums), `note2sct` (4,960 q, hospital discharge summaries),
  `hpo2sct` (14,149 labels), `fma2sct` (13,123), `ncit2sct` (46,185).

## 4. Metrics
- **Hits@K** (K=1,5,10): 1 if the correct concept is in the top-K.
- **nDCG@K** with a gain function based on ontological structure:
  g=3 exact match · g=2 direct parent/child · g=1 grandparent/grandchild/uncle/sibling · g=0 no relation.
- **MRR**. Significance via *paired t-test* (p ≪ 0.05).

## 5. Key results (Triplet-BERT vs baselines)

Concept normalization (Hits@1 / Hits@10):

| Dataset   | BM25        | Word2Vec    | **Triplet-BERT** |
|-----------|-------------|-------------|------------------|
| cadec2sct | 0.132/0.307 | 0.191/0.418 | **0.385/0.654**  |
| note2sct  | 0.526/0.770 | 0.605/0.829 | **0.755/0.904**  |
| hpo2sct   | 0.334/0.553 | 0.397/0.632 | **0.608/0.844**  |
| fma2sct   | 0.310/0.639 | 0.183/0.597 | **0.700/0.885**  |
| nci2sct   | 0.345/0.496 | 0.369/0.567 | **0.503/0.670**  |

Ranking (averages): nDCG@10 ≈ **0.748** (Triplet) vs 0.625 (BM25); MRR up to **0.696**.

### The finding that justifies the whole design (Figure 3): **lexical overlap**
- **High overlap (0.8–1.0):** BM25 = 0.94–0.98 Hits@10 ≈ Triplet 0.98–0.99. **They tie.**
- **Low overlap (0–0.3):** BM25 **drops to 0.0**; Triplet **maintains** performance.
- Examples:
  - "narrow retinal arterioles" → *Retinal arteries attenuated (finding)* — overlap 1/3, BM25 fails.
  - "tooth mass excess" → *Macrodontia (disorder)* — overlap **0.0**, only Triplet gets it right.

### Why "raw" BioBERT is worse than BM25
Very short texts (~1–2 tokens) → self-attention without context. E.g. pre-train:
sim(cephalodynia, cephalgia)=0.97 but sim(headache, cephalgia)=0.73. It is the **triplet
fine-tuning** that brings synonyms together. → *A generic biomedical BERT is not enough; you have to
align synonyms* (exactly what SapBERT / BioLORD already do → see note 02).

## 6. Limitations and practical recommendations from the paper
- Casual/ambiguous language degrades performance (forums): "threw up" → *Does throw* instead of *Vomiting*.
- Low-quality synonyms (NCIt with numbers/abbreviations) lower performance.
- Varies by concept type (findings > anatomy).
- **They explicitly recommend a hybrid deployment:** Triplet for low overlap, BM25 for high overlap.
- With Hits@10 = 90.4% on discharge summaries, ~1 in every 10 needs manual review → acceptable for clinical capture.
- Show the top-5 (high MRR → the correct one is usually within 1–2 clicks).

---

## What we reuse as-is (mapping to our design)

| From the paper | In our system |
|---|---|
| BM25 as the lexical channel | `tsvector`/`to_tsquery('unaccent_simple', 'w:* & w:*')` multi-prefix ignore-order, accent-insensitive (Postgres) |
| Triplet-BERT (synonym fine-tuning) | **BioLORD-2023-M** already comes synonym-aligned and is multilingual → we do not retrain |
| Algorithm 1 (triplets from the hierarchy) | Plan B: if we wanted our own fine-tuning, generate triplets from Description + Language refset + Relationship (IS-A) |
| Cosine + top-K | `embedding <=> qvec` with HNSW; show top-5/top-10 |
| nDCG gain by hierarchy | Evaluation metric for our search engine (use IS-A for g=2/g=1) |
| Hybrid recommendation (they do not implement it) | **Our contribution:** **RRF** fusion of both channels in a single SQL query |

## Evaluation ideas for our case
- Build a set of Spanish queries (physician's language) → expected SNOMED concept.
- Report Hits@1/5/10, nDCG@10 (with gain by IS-A), MRR.
- **Segment by ES→EN lexical overlap** to demonstrate where the semantic channel adds value
  (it will be almost the entire range, because the query comes in Spanish).
