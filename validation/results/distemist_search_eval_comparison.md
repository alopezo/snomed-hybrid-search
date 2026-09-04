# DISTEMIST search-only — configuration comparison

Search-only normalization on the first-100-doc sample (n = 542 scoped mentions; gemma query
normalization and cross-encoder rerank toggled). Gold resolved via historical associations and
scoped to disorder/finding (see the per-config reports for methodology).

| config | acc@1 | recall@5 | recall@10 | MRR | near-miss@10 |
|---|---|---|---|---|---|
| **pp OFF / rr ON** | 0.601 | 0.764 | 0.797 | 0.669 | +0.096 |
| **pp ON / rr OFF** | 0.535 | 0.699 | 0.747 | 0.603 | +0.124 |
| **pp ON / rr ON** | 0.541 | 0.694 | 0.747 | 0.605 | +0.124 |

*Strict exact-concept metrics; recall@5/@10 = the picker-list framing (gold on the short list the user sees). near-miss@10 = extra fraction whose top-10 holds a parent/child of the gold.*
