# Paper

Manuscript for the experience report on **hybrid SNOMED CT concept retrieval** (multi-model
lexical-semantic search without an additional hand-maintained interface vocabulary).

Posted as a preprint on medRxiv:
[10.64898/2026.09.10.26362796](https://doi.org/10.64898/2026.09.10.26362796)
(<https://medrxiv.org/cgi/content/short/2026.09.10.26362796v1>).

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
- [ ] For a journal submission, trim the main text toward the target venue's word limit
      (~4,000 words for JAMIA Research and Applications); consider moving some detail to a supplement.
- [ ] Update the citation and add the journal DOI once (and if) published.

## Thesis (one line)
Detailed interface terminologies carry an unsustainable maintenance burden that degrades their quality;
an LLM + hybrid semantic-search layer can *compute* the interface on demand against SNOMED CT's own
descriptions, reframing AI as support for using reference terminologies directly.
