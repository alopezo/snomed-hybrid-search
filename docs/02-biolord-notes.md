# BioLORD — model notes and what happened after the paper

**Paper (JAMIA 2024):** https://academic.oup.com/jamia/article/31/9/1844/7614965
· arXiv: https://arxiv.org/abs/2311.16075 · PubMed: https://pubmed.ncbi.nlm.nih.gov/38412333/
**Author:** François Remy (FremyCompany).

## What it is
A biomedical semantic representation model that **fuses LLMs + a clinical knowledge graph**.
In the vector space it brings together the *name* of a concept and its *definition* (LLM-generated),
"grounding" the name with its meaning. It improves on BCR, STS, and NEL (named entity linking).

- **Multilingual** (the relevant part for ES→EN): up to **50+ languages**, with cross-lingual alignment
  via **LaBSE** and a multilingual dictionary **built from the regional SNOMED-CT releases**
  (EN, ES, FR, DE, NL, DA, SV). Spanish is among the 7 fine-tuned European languages.

### Language coverage — precision (only ES/EN? NO)
- **7 languages with OFFICIAL support** (fine-tuned): **English, Spanish, French, German, Dutch,
  Danish, Swedish**. It is not ES/EN only.
- **"Unofficial" support for more languages** (up to 50+ via cross-lingual distillation), but with
  **lower and non-guaranteed quality** → you must measure before relying on it.
- **ES↔EN** is one of the best-covered pairs (both fine-tuned) → consistent with the measured `sim = 0.999`.
- Why this coverage: the base model `all-mpnet-base-v2` is English; the multilingual capability comes from the alignment
  built **from the national SNOMED editions**, which is why it is strong precisely in those 7 languages
  (where SNOMED has mature editions) and weak outside them.
- **Implication for the project:** if the horizon is only ES/EN, this model is ideal as-is.
  For a 3rd European language → it would work but **needs evaluation**. For languages outside the European
  circle → consider a more uniform alternative such as **BGE-M3** (~100 languages, less "clinical").

## What happened AFTER the paper (status as of 2026-08)
- **There is no BioLORD-2024 or -2025.** BioLORD-2023 remains the current version.
- The original was retroactively renamed **BioLORD-2022**; "-2023" is the improvement
  (better training strategy + updated corpus).
- Hugging Face repos **last updated in May 2025** (maintenance, not a new generation).
- Also available in the **Microsoft Foundry / Azure AI** catalog
  (`fremycompany-biolord-2023`) → a signal of production adoption.

**Conclusion:** it is a **stable and mature** model, neither abandoned nor superseded by a successor.
Suitable to build on without fear of it becoming obsolete right away.

## Variants (choosing for our case)

| Model | Use | Note |
|---|---|---|
| **BioLORD-2023-M** | **Multilingual (our choice)** | distilled from -2023; 7 European languages incl. **Spanish** |
| BioLORD-2023   | Best monolingual EN | with *model averaging* |
| BioLORD-2023-S | Best monolingual EN | without *model averaging* |
| BioLORD-2023-C | Monolingual EN | contrastive training only |

## Deployment sheet (BioLORD-2023-M)
- **Base:** `sentence-transformers/all-mpnet-base-v2`, biomedically fine-tuned.
- **Vector dimension:** **768** → `VECTOR(768)` column in pgvector.
- **Size:** ~0.3B parameters (lightweight; runs on CPU, fast on GPU).
- **Usage:**
  ```python
  from sentence_transformers import SentenceTransformer
  model = SentenceTransformer("FremyCompany/BioLORD-2023-M")
  emb = model.encode(sentences, normalize_embeddings=True)  # normalize → cosine = dot
  ```
- **License:** contributions are MIT, but it requires licensing **UMLS and SNOMED CT**
  (free with registration) due to the origin of the training data. ✔️ compatible with SNOMED usage.

## Why it fits better than the alternatives for our case
- vs **SapBERT**: SapBERT is English-only → it does not cover Spanish queries. BioLORD-2023-M does.
- vs **BGE-M3 / multilingual e5**: these are general-domain; BioLORD is **biomedical** and its alignment
  was built **from SNOMED**, so it already knows clinical synonyms and language variants.
- vs **Triplet-BERT from the paper**: same idea (aligning synonyms via fine-tuning) but already trained,
  multilingual, and maintained → **we retrain nothing**.

## Risks / things to validate in the pilot
- The strong multilingual fine-tune covers 7 European languages; Spanish ✔️, but it is worth measuring
  with real physician queries (jargon, local abbreviations e.g. "IAM", "HTA").
- Very short texts (1–2 words) → BioLORD is designed for "clinical sentences";
  for ultra-short terms, measure and, if needed, enrich the query with gemma
  (abbreviation expansion) before embedding.
