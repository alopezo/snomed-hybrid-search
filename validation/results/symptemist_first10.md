# SYMPTEMIST validation — first 10 cases

Extractor + hybrid mapping vs. the gold entity-linking SNOMED CT codes. Two metrics: **strict**
(our concept id equals the gold) and **hierarchy-aware** (our concept is the gold, an ancestor,
or a descendant of it — crediting parent/child equivalents). Recall still has a built-in ceiling:
the corpus scope includes mentions the extractor deliberately skips, and cross-hierarchy
representation choices (e.g. a *substance* vs a *disorder* concept) are not credited by either.

## Summary

- Files: **10**, elapsed 671.9s
- Gold mappable codes: **107** (in our release: **98**)

| metric | best match | top-5 |
|---|---|---|
| **strict** (recall of gold) | 0.196 | 0.308 |
| **hierarchy-aware** (of gold) | 0.364 | 0.477 |
| strict (of in-release) | 0.214 | 0.337 |
| hierarchy-aware (of in-release) | 0.398 | 0.52 |

## Per file

| file | gold | in-rel | hit best | hit top-5 | hier best | hier top-5 |
|---|---|---|---|---|---|---|
| `es-S0004-06142007000700013-1` | 12 | 12 | 2 | 3 | 6 | 8 |
| `es-S0004-06142007000700015-1` | 11 | 10 | 2 | 3 | 4 | 4 |
| `es-S0004-06142007000900011-1` | 6 | 6 | 1 | 3 | 3 | 4 |
| `es-S0004-06142007000900013-1` | 7 | 6 | 0 | 1 | 0 | 1 |
| `es-S0004-06142007000900014-1` | 17 | 16 | 4 | 5 | 7 | 8 |
| `es-S0004-06142008000300015-1` | 7 | 7 | 2 | 2 | 4 | 4 |
| `es-S0004-06142008000400010-1` | 25 | 23 | 8 | 14 | 10 | 16 |
| `es-S0004-06142008000500015-1` | 1 | 1 | 0 | 0 | 1 | 1 |
| `es-S0004-06142008000600013-1` | 6 | 5 | 0 | 0 | 1 | 2 |
| `es-S0004-06142008000600014-1` | 15 | 12 | 2 | 2 | 3 | 3 |
