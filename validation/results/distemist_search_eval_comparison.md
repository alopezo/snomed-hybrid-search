# DISTEMIST search-only — configuration comparison

Search-only normalization on the first-100-doc sample (n = 542 scoped mentions; gemma query
normalization and cross-encoder rerank toggled). Gold resolved via historical associations and
scoped to disorder/finding (see the per-config reports for methodology).

| config | acc@1 | recall@5 | recall@10 | hier@1 | hier@5 | hier@10 | MRR |
|---|---|---|---|---|---|---|---|
| **pp OFF / rr ON** | 0.601 | 0.764 | 0.797 | 0.747 | 0.873 | 0.893 | 0.669 |
| **pp ON / rr OFF** | 0.535 | 0.699 | 0.747 | 0.742 | 0.843 | 0.871 | 0.603 |
| **pp ON / rr ON** | 0.541 | 0.694 | 0.747 | 0.742 | 0.839 | 0.871 | 0.605 |

*strict = exact code; hier = exact/ancestor/descendant. Higher is better.*
