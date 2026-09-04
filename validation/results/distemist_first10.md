# DISTEMIST validation — first 10 cases

Extractor + hybrid mapping vs. the gold entity-linking SNOMED CT codes. Two metrics: **strict**
(our concept id equals the gold) and **hierarchy-aware** (our concept is the gold, an ancestor,
or a descendant of it — crediting parent/child equivalents). Recall still has a built-in ceiling:
the corpus scope includes mentions the extractor deliberately skips, and cross-hierarchy
representation choices (e.g. a *substance* vs a *disorder* concept) are not credited by either.

## Summary

- Files: **10**, elapsed 850.8s
- Gold mappable codes: **54** (in our release: **51**)

| metric | best match | top-5 |
|---|---|---|
| **strict** (recall of gold) | 0.426 | 0.556 |
| **hierarchy-aware** (of gold) | 0.648 | 0.704 |
| strict (of in-release) | 0.451 | 0.588 |
| hierarchy-aware (of in-release) | 0.686 | 0.745 |

## Per file

| file | gold | in-rel | hit best | hit top-5 | hier best | hier top-5 |
|---|---|---|---|---|---|---|
| `S0004-06142005000700014-1` | 6 | 6 | 5 | 5 | 5 | 6 |
| `S0004-06142006000200001-1` | 5 | 5 | 3 | 4 | 5 | 5 |
| `S0004-06142006000200014-1` | 5 | 5 | 2 | 3 | 3 | 3 |
| `S0004-06142006000500002-4` | 4 | 4 | 1 | 4 | 3 | 4 |
| `S0004-06142006000600012-1` | 14 | 14 | 6 | 8 | 11 | 12 |
| `S0004-06142006000900006-1` | 3 | 3 | 0 | 0 | 0 | 0 |
| `S0004-06142006000900008-1` | 8 | 8 | 3 | 3 | 5 | 5 |
| `S0004-06142007000300013-1` | 5 | 3 | 1 | 1 | 1 | 1 |
| `S0004-06142007000500011-1` | 3 | 2 | 2 | 2 | 2 | 2 |
| `S0004-06142007000500017-1` | 1 | 1 | 0 | 0 | 0 | 0 |
