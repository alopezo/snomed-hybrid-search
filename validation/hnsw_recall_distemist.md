# HNSW recall diagnostic — DISTEMIST

Approximate (HNSW) vs exact (brute-force) nearest neighbours over 100 gold mentions.
recall@10 = overlap with the exact top-10; a *collapse* is recall <= 0.2 (graph
traversal stuck). `rows` = mean rows returned for a LIMIT = CANDIDATES request (200); ef_search caps it.

| ef_search | mean recall@10 | collapses (<=0.2) | mean rows (of 200) |
|--:|--:|--:|--:|
| 40 | 0.990 | 0 / 100 | 40 |
| 100 | 0.998 | 0 / 100 | 101 |
| 200  ← shipped | 0.998 | 0 / 100 | 200 |
| 400 | 0.998 | 0 / 100 | 200 |
