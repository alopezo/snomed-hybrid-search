# SNOMED CT Entity Linking Challenge — end-to-end evaluation

This folder scores the **complete pipeline** (LLM extraction **+** hybrid mapping) on the SNOMED CT
Entity Linking Challenge (DrivenData / PhysioNet, MIMIC-IV discharge notes), using the challenge's own
**official metric**, so the result is comparable *in magnitude* to the public leaderboard.

It complements `../run_search_eval.py`, which is **search-only** (feeds the gold mention span to the
engine and measures `acc@1 / recall@k / MRR`, isolating normalization). Here nothing is given: the model
reads the raw note, decides what the mentions are, maps each one, and is scored on character coverage.

## Files

- **`official_iou.py`** — the challenge metric: character-level IoU per concept, macro-averaged over the
  union of concepts in gold and prediction. Dependency-light re-implementation of the challenge's
  `iou_per_class` (DrivenData `snomed-ct-entity-linking`, Apache-2.0; identical routine in the top-3
  winners' code). Verified to match the reference scipy implementation exactly on randomized inputs
  (`_selftest()` and a 200-trial cross-check). Run `python official_iou.py` for the self-test.
- **`run_e2e.py`** — the end-to-end runner (extraction → search → offset projection → scoring).
- **`exhaustive_extract.py`** — an ALTERNATIVE, recall-oriented extractor for this eval only (see below).
- **`results/`** — generated `snomed_el_e2e_first<N>.{json,md}` (production) and
  `snomed_el_e2e_first<N>_exhaustive.{json,md}`.

## Two extractors

`run_e2e.py --extractor {production,exhaustive}` (default `production`):

- **`production`** — the app's `/api/extract`, unchanged. Selective by design: diagnoses / findings /
  procedures / medications, with lab values and vital-sign numbers out of scope, single dense-JSON pass,
  800-char chunks (the 12B QAT model degenerates on long structured output). This is the paper's system.
- **`exhaustive`** — `exhaustive_extract.py`, built to chase the challenge's *exhaustive* gold. Two passes:
  1. **Detection** — plain text, one verbatim span per line (no JSON), so output stays short and the model
     does not degenerate → large chunks (`EXHAUSTIVE_CHUNK_CHARS`, default 2500). Casts a wide net,
     including lab/vital-sign NAMES, anatomy and abbreviations (but not numeric values).
  2. **Typing + normalization** — batched; the **full note is passed as context** so abbreviations are
     expanded by how they are used in this note (e.g. `MR` → mitral regurgitation vs MRI). Emits
     `type` (search hierarchy filter) + English `clinicalTerm`.

  Detection is **section-aware** (#1): the note is split by its section headers and detected per section,
  so the model sees the section framing and section-specific mentions are found; each detected span carries
  its section, which typify also uses to disambiguate. Set `EXHAUSTIVE_SECTIONS=0` to turn this off for A/B.

  The model is configurable and independent of production:
  `EXHAUSTIVE_MODEL` (default `gemma3:27b`), `EXHAUSTIVE_CHUNK_CHARS`, `EXHAUSTIVE_TYPIFY_BATCH`,
  `EXHAUSTIVE_SECTIONS`. Nothing in this module imports or changes `api/`. Quick standalone check:
  `echo "<note text>" | .venv/bin/python validation/snomed_el_e2e/exhaustive_extract.py`.

  > Memory note: `gemma3:27b` (~17 GB) is tight on a 32 GB machine alongside BioLORD + the reranker;
  > do not run it concurrently with a production-extractor eval.

## Layer 3 — context-aware LLM selection (#2), shared with the search UI

The `--llm-select` flag routes each entity's search top-k through `llm_select()` in `api/search.py` — the
**same** third rerank layer exposed in the interactive search UI (the "llm pick" toggle + context box). A
local LLM picks the single best candidate **by index** (grounded: it can only choose from the retrieved
list, never emits a concept id), given the query and a context string. In the eval the context is the
mention's **section + sentence** (`local_context()` in `run_e2e.py`); in the UI it is the editable context
box. It is a reranker, not a retriever — it can only reorder what search already retrieved.

## How to run

The API must be up (`make up`) and `PG_DSN` / `SNOMED_SNAPSHOT_DIR` set in `../../.env`.

```bash
cd snomed-search
.venv/bin/python validation/snomed_el_e2e/run_e2e.py --n 25          # subset (minutes)
.venv/bin/python -u validation/snomed_el_e2e/run_e2e.py --n 272      # full corpus (slow, LLM-bound)

# recall-oriented exhaustive extractor (#1 section context is on by default):
.venv/bin/python -u validation/snomed_el_e2e/run_e2e.py --n 5 --extractor exhaustive
# add the context-aware LLM selection layer (#2) over each entity's search top-k:
.venv/bin/python -u validation/snomed_el_e2e/run_e2e.py --n 5 --extractor exhaustive --llm-select
# A/B the section context (#1): re-run with it off
EXHAUSTIVE_SECTIONS=0 .venv/bin/python -u validation/snomed_el_e2e/run_e2e.py --n 5 --extractor exhaustive
```

Use `python -u` (unbuffered) to watch per-note progress live. Flags: `--gemma`, `--rerank`, `--k`
(defaults `false` / `true` / `5` — the manuscript's interactive serving configuration for this corpus),
`--extractor {production,exhaustive}`, `--llm-select`. Output stems get `_exhaustive` / `_llmsel` suffixes
so runs never collide. `--llm-select` and `--extractor exhaustive` (section context) attack the exact-id
precision bottleneck rather than recall.

## Metric

For every concept present in the gold or the prediction, over all notes:

```
IoU(concept) = |chars where gold == concept AND pred == concept|
             / |chars where gold == concept  OR pred == concept|
```

then the unweighted mean over those concepts. A concept we predict that is not in the gold enters the
average with IoU 0, so **over-prediction is penalized** as much as missed concepts.

## Interpreting the number

Public leaderboard (challenge **test** split, fine-tuned NER): 1st 0.4452/0.4202, 2nd 0.4447/0.4194,
3rd 0.4065/0.3777 (public/private).

Our figure is **not head-to-head** with those:

1. **Split.** The leaderboard test gold is private; we score the released **train** annotations.
2. **Zero-shot.** No training or fine-tuning on the corpus, unlike every leaderboard entry.
3. **Scope.** The extractor deliberately omits some annotated mention types (lab values, normal exam
   findings) — a structural recall ceiling the IoU metric penalizes.
4. **Single concept.** One best concept per mention; no post-coordination.
5. **Offsets.** The extractor returns verbatim spans without offsets and the server deduplicates spans
   across chunks, so offsets are reconstructed by marking all non-overlapping literal occurrences of each
   span (longer spans win contested characters). Spans not found verbatim are dropped and counted.

Read the end-to-end IoU as an order-of-magnitude reference against the leaderboard, and expect it to sit
**below** the search-only `acc@1` (0.52): the end-to-end path adds extraction error and false positives on
top of the normalization step that the search-only harness isolates.
