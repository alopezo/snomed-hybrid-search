# Synonym-value ablation — SNOMED_EL

How much do SNOMED CT synonyms add to retrieval, isolated per channel. *desc scope*: **all** = FSN
+ every synonym; **fsn_pt** = FSN + preferred term; **fsn** = FSN only. Each block shows acc@1 /
recall@10 / MRR at the three scopes, then the two isolated contributions.

| channel / config | metric | all | fsn+PT | fsn only | Δ synonyms>PT (all−fsn_pt) | Δ PT (fsn_pt−fsn) |
|---|---|--:|--:|--:|--:|--:|
| **semantic channel (rerank off)** | acc@1 | 0.478 | 0.477 | 0.453 | +0.001 | +0.024 |
|  | recall@10 | 0.665 | 0.659 | 0.647 | +0.006 | +0.012 |
|  | MRR | 0.537 | 0.532 | 0.513 | +0.005 | +0.019 |
| **lexical channel (rerank off)** | acc@1 | 0.313 | 0.220 | 0.227 | +0.093 | -0.007 |
|  | recall@10 | 0.412 | 0.288 | 0.288 | +0.124 | +0.000 |
|  | MRR | 0.341 | 0.239 | 0.244 | +0.102 | -0.005 |
| **fused, served (rerank on)** | acc@1 | 0.484 | 0.417 | 0.409 | +0.067 | +0.008 |
|  | recall@10 | 0.690 | 0.645 | 0.637 | +0.045 | +0.008 |
|  | MRR | 0.553 | 0.494 | 0.484 | +0.059 | +0.010 |

_n = 719 mentions · gemma off · LOINC-excluded._
