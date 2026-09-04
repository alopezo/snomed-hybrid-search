# Paper

Working manuscript for the experience report on **hybrid SNOMED CT concept retrieval** (multi-model
lexical-semantic search without an additional hand-maintained interface vocabulary). Target venue: **JAMIA**
(Research and Applications) or a Perspective.

## Files
- `manuscript.md`: the manuscript (Markdown, pandoc citation syntax `[@key]`).
- `references.bib`: bibliography. Entries flagged `VERIFY` need a final check of pages/DOI/authors
  before submission.
- `figures/reference-architecture.svg`: editable source for the reference architecture diagram.
- `figures/reference-architecture.png`: high-resolution version embedded in the manuscript.

## Build (PDF / docx)

```bash
# needs pandoc and tectonic for PDF
mkdir -p output/pdf
pandoc --citeproc manuscript.md --bibliography references.bib --pdf-engine=tectonic -o output/pdf/manuscript.pdf
pandoc --citeproc manuscript.md --bibliography references.bib -o manuscript.docx
```

## Status / TODO
- [ ] Finalize the SympTEMIST numbers (values in `[brackets]`) once the full evaluation is run;
      add a results table. Current pilot: `../validation/results/symptemist_first10.md`.
- [ ] Add a hierarchy-aware metric (credit ancestor/descendant/synonym matches); the strict
      exact-code recall is a lower bound.
- [ ] Verify `VERIFY`-flagged citations (rosenbloom2011tension pages/doi; remy2024biolord end page;
      snomedllm2024scoping authors/volume; zhao2023pmcpatients article no.).
- [ ] State the exact SNOMED CT release/version used and the public repository URL.
- [ ] Confirm author list and affiliations; funding / competing interests.
- [ ] Consider expanding evaluation to DisTEMIST (diseases) and MedProcNER (procedures) for
      finding/disease/procedure coverage, plus a clinician-adjudicated subset.

## Thesis (one line)
Detailed interface terminologies carry an unsustainable maintenance burden that degrades their quality;
an LLM + hybrid semantic-search layer can *compute* the interface on demand against SNOMED CT's own
descriptions, reframing AI as support for using reference terminologies directly.
