# Paper workspace conventions

## Sources of truth

- Edit `manuscript.md` and `references.bib` during normal manuscript work.
- Treat generated PDF and DOCX files as snapshots, not as editable sources.

## Editorial style

- Avoid typographic en and em dashes in manuscript prose. Use ordinary punctuation for parenthetical clauses and ASCII hyphens in compound terms.

## Build policy

- Do not rebuild the PDF or DOCX after routine text or bibliography edits.
- Build artifacts only when the user explicitly asks, or when a current artifact is needed for sharing, submission, or layout review.
- A generated artifact may intentionally lag behind the Markdown source.

## PDF build

```bash
mkdir -p output/pdf
pandoc --citeproc manuscript.md --bibliography references.bib --pdf-engine=tectonic -o output/pdf/manuscript.pdf
```

- After a requested PDF build, render its pages and visually check for missing glyphs, clipped or overlapping text, broken references, and layout problems.
- Keep temporary rendered pages outside `output/pdf/` and remove them after review.

## DOCX build

```bash
pandoc --citeproc manuscript.md --bibliography references.bib -o manuscript.docx
```
