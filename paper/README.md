# Paper

Manuscript for the experience report on **hybrid SNOMED CT concept retrieval** (multi-model
lexical-semantic search without an additional hand-maintained interface vocabulary).

Posted as a preprint on medRxiv:
[10.64898/2026.09.10.26362796](https://doi.org/10.64898/2026.09.10.26362796)
(latest version: <https://medrxiv.org/cgi/content/short/2026.09.10.26362796v3>).

Version history:
- v1 (2026-09-10): initial posting.
- v2 (2026-09-14): abstract simplified, Apache-2.0 license and preprint links added, citations verified.
- v3 (2026-09-22): DisTEMIST evaluated on the full corpus (985 mentions; accuracy@1 0.57, replacing the
  earlier 542-mention subset at 0.60); added contextual LLM re-ranking (Table 4; accuracy@1 0.57 to 0.61
  on DisTEMIST and 0.52 to 0.56 on the EL Challenge); switched the local LLM to Gemma 4 (reasoning off);
  re-ran all evaluations with HNSW ef_search=200; synonym-scope and LOINC-module-isolation ablations.

## Files
- `manuscript.md`: the manuscript (Markdown, pandoc citation syntax `[@key]`).
- `references.bib`: bibliography. Entries flagged `VERIFY` need a final check of pages/DOI/authors
  before journal submission.
- `figures/reference-architecture.svg`: editable source for the reference architecture diagram.
- `figures/reference-architecture.png`: high-resolution version embedded in the manuscript.

## Build (PDF / docx)

```bash
# needs pandoc and tectonic for PDF
mkdir -p output/pdf
pandoc --citeproc manuscript.md --bibliography references.bib --pdf-engine=tectonic -o output/pdf/manuscript.pdf
pandoc --citeproc manuscript.md --bibliography references.bib -o manuscript.docx
```

## Open items (before the next version / journal submission)
- [x] Citations verified against published sources (2026-09-14): rosenbloom2011tension,
      remy2024biolord, snomedllm2024scoping, zhao2023pmcpatients (titles aligned to journal versions).
- [x] v3 content finalized (full DisTEMIST corpus, contextual LLM re-ranking, Gemma 4, ef_search=200).
- [ ] For a journal submission, trim the main text toward the target venue's word limit
      (~4,000 words for JAMIA Research and Applications); consider moving some detail to a supplement.
- [ ] Update the citation and add the journal DOI once (and if) published.

## Thesis (one line)
Detailed interface terminologies carry an unsustainable maintenance burden that degrades their quality;
an LLM + hybrid semantic-search layer can *compute* the interface on demand against SNOMED CT's own
descriptions, reframing AI as support for using reference terminologies directly.
