# DISTEMIST search-only — configuration comparison

Search-only normalization on the first-100-doc sample (n = 542 scoped mentions; gemma query
normalization and cross-encoder rerank toggled). Gold resolved via historical associations and
scoped to disorder/finding (see the per-config reports for methodology).

| config | MRR | acc@1 | recall@5 | recall@10 | +near-miss@10 | =same-lineage@10 |
|---|---|---|---|---|---|---|
| **pp OFF / rr ON** | 0.669 | 0.601 | 0.764 | 0.797 | +0.096 | 0.893 |
| **pp ON / rr OFF** | 0.635 | 0.576 | 0.712 | 0.762 | +0.116 | 0.878 |
| **pp ON / rr ON** | 0.64 | 0.581 | 0.721 | 0.762 | +0.116 | 0.878 |

*Strict exact-concept metrics; recall@5/@10 = the picker-list framing (gold on the short list the user sees). +near-miss@10 = extra fraction whose top-10 holds a parent/child of the gold; =same-lineage@10 = recall@10 + near-miss@10 (the optimistic reading).*
