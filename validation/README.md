# Validation

Evaluates the entity extractor + hybrid mapping against a gold-standard corpus of Spanish clinical
case reports normalized to SNOMED CT.

## SympTEMIST (BioCreative VIII)

1,000+ Spanish clinical case reports with symptom/sign/finding mentions normalized to SNOMED CT.

### Attribution (CC BY 4.0)

The SympTEMIST corpus is used here under the **Creative Commons Attribution 4.0 International
(CC BY 4.0)** license — <https://creativecommons.org/licenses/by/4.0/>.

- **Title:** SympTEMIST corpus (BioCreative VIII)
- **Authors:** Salvador Lima-López, Eulàlia Farré-Maduell, Luis Gascó, Anastasios Nentidis, Anastasia
  Krithara, Georgios Katsimpras, Georgios Paliouras, Martin Krallinger, et al. — Barcelona Supercomputing Center.
- **Source:** Zenodo, DOI [10.5281/zenodo.8223653](https://doi.org/10.5281/zenodo.8223653)
  (record <https://zenodo.org/records/8413866>).
- **Reference:** Lima-López et al., *Overview of SympTEMIST at BioCreative VIII: corpus, guidelines and
  evaluation of systems for the detection and normalization of symptoms, signs and findings from text*,
  Proceedings of the BioCreative VIII Challenge, 2023.
- **Changes:** none to the corpus files. This repo does **not** redistribute the corpus (it is
  git-ignored); it only runs our own extraction/mapping over the texts and stores derived evaluation
  metrics. Download the corpus yourself from the source above.

The corpus is **not committed** (`validation/symptemist/` is git-ignored, ~50 MB). Download it:

```bash
mkdir -p validation/symptemist && cd validation/symptemist
curl -sL -o symptemist.zip "https://zenodo.org/records/8413866/files/symptemist-train_all_subtasks+gazetteer+multilingual+test_all_subtasks+bg_231006.zip?download=1"
unzip -q symptemist.zip -d extracted
```

Relevant files inside `symptemist_train/`:
- `subtask1-ner/txt/*.txt` — the clinical case notes (input).
- `subtask2-linking/symptemist_tsv_train_subtask2.tsv` — gold: `filename, label, span, text, code`
  where `code` is the SNOMED CT id (or `NO_CODE` / composite).

## DisTEMIST (BioASQ 2022)

1,000 Spanish clinical case reports (750 train / 250 test) with **disease** mentions normalized to
SNOMED CT.

### Attribution (CC BY 4.0)

Used under **CC BY 4.0** — <https://creativecommons.org/licenses/by/4.0/>.

- **Title:** DisTEMIST corpus (BioASQ/CLEF 2022)
- **Authors:** Antonio Miranda-Escalada, Luis Gascó, Salvador Lima-López, Eulàlia Farré-Maduell,
  Anastasios Nentidis, Anastasia Krithara, Georgios Katsimpras, Georgios Paliouras, Martin Krallinger,
  et al. — Barcelona Supercomputing Center.
- **Source:** Zenodo, DOI [10.5281/zenodo.6532684](https://doi.org/10.5281/zenodo.6532684).
- **Reference:** Miranda-Escalada et al., *Overview of DisTEMIST at BioASQ: Automatic detection and
  normalization of diseases from clinical texts*, CLEF 2022 Working Notes.
- **Changes:** none to the corpus files; not redistributed here (git-ignored).

The corpus is **not committed** (`validation/distemist/`, git-ignored, ~14 MB). Download it:

```bash
mkdir -p validation/distemist && cd validation/distemist
curl -sL -o distemist.zip "https://zenodo.org/records/6532684/files/distemist.zip?download=1"
unzip -q distemist.zip -d extracted
```

Relevant files inside `distemist/training/`:
- `text_files/*.txt` — the clinical case notes (input).
- `subtrack2_linking/distemist_subtrack2_training1_linking.tsv` — gold:
  `filename, mark, label, off0, off1, span, code, semantic_rel` (`code` = SNOMED CT id).

## Run

```bash
make up                                             # API must be running
.venv/bin/python validation/run_corpus.py --corpus symptemist --n 10
.venv/bin/python validation/run_corpus.py --corpus distemist  --n 10
```

Outputs `results/<corpus>_first<N>.json` (full detail) and `.md` (summary). Column layouts per corpus
are in `CORPORA` at the top of `run_corpus.py` (add MedProcNER etc. there).

## What it measures

For the first N cases: extract entities → map each via `/api/search` (gemma off, type-filtered with a
fallback) → compare the mapped concept ids against the gold codes (set-based recall, best-match and
top-5). It also reports how many gold codes exist in the loaded SNOMED release, separating retrieval
misses from release/version gaps.

**Caveat (recall ceiling):** SympTEMIST's "symptom" scope is broader than this extractor — it annotates
lab-value statements and normal findings that the extractor deliberately skips — so recall is a floor,
not a full accuracy figure. Top teams reach ~0.61 accuracy on the harder SymptomNorm subtask.
