# Synonym-value ablation — DISTEMIST

How much do SNOMED CT synonyms add to retrieval, isolated per channel. *desc scope*: **all** = FSN
+ every synonym; **fsn_pt** = FSN + preferred term; **fsn** = FSN only. Each block shows acc@1 /
recall@10 / MRR at the three scopes, then the two isolated contributions.

| channel / config | metric | all | fsn+PT | fsn only | Δ synonyms>PT (all−fsn_pt) | Δ PT (fsn_pt−fsn) |
|---|---|--:|--:|--:|--:|--:|
| **semantic channel (rerank off)** | acc@1 | 0.592 | 0.577 | 0.534 | +0.015 | +0.043 |
|  | recall@10 | 0.801 | 0.775 | 0.754 | +0.026 | +0.021 |
|  | MRR | 0.667 | 0.645 | 0.609 | +0.022 | +0.036 |
| **lexical channel (rerank off)** | acc@1 | 0.055 | 0.041 | 0.038 | +0.014 | +0.003 |
|  | recall@10 | 0.060 | 0.044 | 0.041 | +0.016 | +0.003 |
|  | MRR | 0.057 | 0.042 | 0.039 | +0.015 | +0.003 |
| **fused, served (rerank on)** | acc@1 | 0.571 | 0.546 | 0.520 | +0.025 | +0.026 |
|  | recall@10 | 0.796 | 0.766 | 0.746 | +0.030 | +0.020 |
|  | MRR | 0.650 | 0.624 | 0.600 | +0.026 | +0.024 |

_n = 985 mentions · gemma off · LOINC-excluded._
