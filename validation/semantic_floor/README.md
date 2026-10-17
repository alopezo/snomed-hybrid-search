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
- A token must also be **rare as a word of its own**: the descriptions containing it as a whole word,
  divided by those containing a longer word it starts (`ndoc` in `lexicon`), must stay below
  `TRUNC_MAX_WORD_RATIO = 0.1`. Prefixes sit at ≤ 0.05 (`fam` 0.000, `ca` 0.001, `myo` 0.010, `cid`
  0.048); whole words at ≥ 0.40, function words included (`on` 0.40, `the` 0.41, `water` 3.0, `knee`
  10.6, `tumor` 50.9). Counting distinct longer words instead does not separate them (`knee` 12,
  `fam` 25). Without this check every word of `water on the knee` looked truncated.
- The gate fires only if **every** token looks truncated. One whole word leaves the query untouched.
- **Two floors by query shape.** A lone prefix-like token gets `SEM_MIN_SIM` (0.80): it may be a Spanish
  acronym that BioLORD maps correctly from the semantic channel alone (`CID`, `LAM`, `LES` on
  DisTEMIST; a 1.0 floor lost all three). Two or more prefix-like tokens get `SEM_MIN_SIM_MULTI`
  (0.95): that is clinician shorthand, and XLM-R reads fragments such as `▁fam ▁bre ▁ca` as short
  Romance-language words, so BioLORD lands on foreign loanwords at high similarity (`fam bre ca` →
  *Cafard* 0.86, *Cacique* 0.83; `fx hip` → *Coxa profunda* 0.88). Real phrases reach their answer at
  0.89–1.00, so 0.95 still lets near-exact matches through if the detector misfires; paraphrases
  (`water on the knee` 0.89) rely on the word-ratio check instead.
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

The word-ratio check and the two-floor split (`*_ratiofloor.*`, rerank on) leave DisTEMIST unchanged:
acc@1 0.575, recall@10 0.798, MRR 0.654, and no mention changes rank against the same configuration
without them. Their benefit is pinned by `test_truncation_detector` and
`test_multi_prefix_drops_foreign_word_noise`.

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
