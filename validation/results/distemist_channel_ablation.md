# DISTEMIST — retrieval-channel ablation

Search-only linking on the first-100-doc sample (n = 542 scoped mentions). All configs use the
served best setting — **no query pre-processing, re-ranking on** — and vary only the retrieval
channel. *both* is the RRF fusion of the two channels. Strict exact-concept metrics;
*+near@10* is the near-miss increment (top-10 holds a parent/child); *=lin@10* = recall@10 +
near-miss (same-lineage, optimistic).

| channel | MRR | acc@1 | recall@5 | recall@10 | +near@10 | =lin@10 |
|---|---|---|---|---|---|---|
| **lexical only** | 0.071 | 0.07 | 0.072 | 0.072 | +0.018 | 0.09 |
| **semantic only** | 0.675 | 0.607 | 0.773 | 0.804 | +0.091 | 0.895 |
| **both (fusion)** | 0.669 | 0.601 | 0.764 | 0.797 | +0.096 | 0.893 |

*DisTEMIST gold spans are complete, clean disease terms, so this corpus does not exercise the lexical channel's order-independent multi-prefix matching (partial tokens, clinician abbreviations); it therefore understates the lexical channel's value for interactive typed input.*
