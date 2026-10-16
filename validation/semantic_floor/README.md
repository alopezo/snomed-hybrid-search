# Semantic similarity floor for truncated queries

## Problem

RRF fuses the two channels by **rank only**: the semantic #1 scores `1/(60+1)` exactly like the lexical
#1, however weak its cosine similarity. For prefix abbreviations a clinician types (`diab mel`,
`inf myo`), BioLORD has nothing to embed and returns unrelated neighbours at cosine ~0.65–0.75. Under
the Clinical finding filter, `diab mel` (pre-process off) returned *Durlachers corn*, *Soft corn* and
*Fleckmilz* interleaved with the diabetes hits. *Durlachers corn* tied with *Diabetes mellitus* at
0.01739. The cross-encoder rerank scored the corns ~0.001 (vs 0.89 for Diabetes mellitus) but, blended at
`RERANK_WEIGHT = 0.4`, could not push them out.

## Fix (served)

`SEM_MIN_SIM` (default 0.80) drops **semantic-only** candidates below that cosine similarity, but
**only when the query looks truncated**:

- A token looks truncated when at least `TRUNC_MIN_COMPLETIONS = 10` longer words in the index
  vocabulary start with it (`diab` 15, `mel` 203, `inf` 205, `myo` 139). Spanish words and real
  acronyms have few completions (`gripe` 1, `gato` 2, `mav` 3). The vocabulary is the `lexicon` table
  (`ts_stat` over `term_tsv`, ~120k words), built by `make index-lexical`.
- The gate fires only if **every** token looks truncated. One whole word leaves the query untouched.
- Below the floor, a semantic neighbour still **corroborates** a lexical hit. That weak agreement is
  what lifts `inf myo` → *Myocardial infarction* to #1, so turning the channel off entirely broke
  `lexical__inf_myo` and `lexical__order_independent`.

## Rejected: an unconditional floor

An earlier version of the fix applied the floor to every query. On DisTEMIST it lost 47 recall@10 hits
and won none (recall@10 0.798 → 0.750, hier@10 0.894 → 0.829). With pre-process off, untranslated
Spanish mentions reach the right concept at the **same** similarity as the abbreviation noise. Examples:
`gripe` → *Influenza* 0.68, `Enfermedad por Arañazo de Gato` → *Cat scratch disease* 0.75,
`anemización` → *Anemia* 0.59. The lost hits ranged 0.59–0.80 (median 0.76). No fixed threshold
separates the two cases, so the decision has to come from the query, not the score. That run
(`*_floor080.*`) came from the intermediate code and is kept as evidence. `run_ablation.sh` reproduces
only the two configurations the current code supports.

## Result (DisTEMIST, 985 mentions, pre-process off)

The gated floor is neutral on DisTEMIST: every metric matches floor-off, recall@5 is +0.001, and two
mentions move within the top 10 (`CID` 6 → 4, `LAM` 2 → 3). It fires on 12 of 985 mentions, mostly
Spanish acronyms (`LES`, `TAB`, `CID`, `FAE`…), plus one whole word with many completions (`tumor`).
Firing on a whole word is harmless, because whole words embed well and their neighbours clear the
floor.

DisTEMIST gold spans are complete, clean disease terms, so the corpus barely exercises truncated
input. It shows the gate **does no harm**, not how much it helps. The benefit is pinned by
`tests/test_search.py::test_semantic_floor_drops_noise`. The same limitation is noted in
`results/distemist_channel_ablation.md`.

Full table and per-mention diffs: [summary_distemist.md](summary_distemist.md).

## Reproduce

```bash
bash validation/semantic_floor/run_ablation.sh distemist 750
.venv/bin/python validation/semantic_floor/summarize.py --corpus distemist
```
