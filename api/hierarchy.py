"""SNOMED CT IS-A hierarchy subgraphs for interactive search results."""
from __future__ import annotations

import psycopg

from api.search import DISPLAY_LANGUAGE_MODULE, PG_DSN

SNOMED_ROOT = 138875005


def _labels(cur, concept_ids: list[int]) -> dict[int, tuple[str, str]]:
    if not concept_ids:
        return {}
    cur.execute(
        """
        WITH requested(id) AS (SELECT unnest(%s::bigint[]))
        SELECT requested.id,
               COALESCE(es.term, fsn.term, requested.id::text) AS term,
               COALESCE(es.semantic_tag, fsn.semantic_tag, '') AS semantic_tag
        FROM requested
        LEFT JOIN LATERAL (
            SELECT term, semantic_tag FROM descriptions
            WHERE concept_id = requested.id AND module_id = %s
            ORDER BY (type_id = 900000000000013009 AND pref_us) DESC,
                     (type_id = 900000000000013009) DESC, pref_us DESC, id
            LIMIT 1
        ) es ON true
        LEFT JOIN LATERAL (
            SELECT term, semantic_tag FROM descriptions
            WHERE concept_id = requested.id AND type_id = 900000000000003001
            ORDER BY (module_id = %s) DESC, id LIMIT 1
        ) fsn ON true
        """,
        (concept_ids, DISPLAY_LANGUAGE_MODULE, DISPLAY_LANGUAGE_MODULE),
    )
    return {int(cid): (term, tag or "") for cid, term, tag in cur.fetchall()}


def hierarchy_for_results(concept_ids: list[int], max_depth: int = 8,
                          max_nodes: int = 120) -> dict:
    """Return result-to-result relationships plus a bounded direct-parent ancestor graph."""
    ids = list(dict.fromkeys(int(x) for x in concept_ids))
    if not ids:
        return {"nodes": [], "edges": [], "relations": {}, "truncated": False}

    rank = {cid: i + 1 for i, cid in enumerate(ids)}
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT concept_id, ancestors FROM concept_ancestors WHERE concept_id = ANY(%s)",
            (ids,),
        )
        ancestor_sets = {int(cid): {int(x) for x in ancestors} for cid, ancestors in cur.fetchall()}

        cur.execute(
            "SELECT child_id, parent_id FROM concept_parents "
            "WHERE child_id = ANY(%s) AND parent_id = ANY(%s)",
            (ids, ids),
        )
        direct_pairs = {(int(c), int(p)) for c, p in cur.fetchall()}

        cur.execute(
            """
            WITH RECURSIVE walk(seed_id, child_id, parent_id, depth, path) AS (
                SELECT p.child_id, p.child_id, p.parent_id, 1,
                       ARRAY[p.child_id, p.parent_id]::bigint[]
                FROM concept_parents p
                WHERE p.child_id = ANY(%s)
              UNION ALL
                SELECT w.seed_id, p.child_id, p.parent_id, w.depth + 1,
                       w.path || p.parent_id
                FROM walk w
                JOIN concept_parents p ON p.child_id = w.parent_id
                WHERE w.depth < %s AND w.parent_id <> %s
                  AND NOT p.parent_id = ANY(w.path)
            )
            SELECT child_id, parent_id, min(depth) AS depth
            FROM walk
            GROUP BY child_id, parent_id
            """,
            (ids, max_depth, SNOMED_ROOT),
        )
        raw_edges = [(int(c), int(p), int(d)) for c, p, d in cur.fetchall()]

        distances: dict[int, int] = {cid: 0 for cid in ids}
        for child, parent, depth in raw_edges:
            distances[child] = min(distances.get(child, depth - 1), depth - 1)
            distances[parent] = min(distances.get(parent, depth), depth)
        all_nodes = set(distances)
        truncated = len(all_nodes) > max_nodes
        if truncated:
            ordered = sorted(all_nodes, key=lambda cid: (
                0 if cid in rank else 1 if cid == SNOMED_ROOT else 2,
                distances.get(cid, max_depth + 1), cid,
            ))
            keep = set(ordered[:max_nodes]) | set(ids)
        else:
            keep = all_nodes
        edges = [(c, p) for c, p, _ in raw_edges if c in keep and p in keep]
        node_ids = sorted({x for edge in edges for x in edge} | set(ids))
        labels = _labels(cur, node_ids)

    result_set = set(ids)
    relations = {}
    for cid in ids:
        direct_parents = sorted(p for c, p in direct_pairs if c == cid)
        direct_children = sorted(c for c, p in direct_pairs if p == cid)
        transitive_parents = sorted(
            other for other in result_set - {cid}
            if other in ancestor_sets.get(cid, set()) and other not in direct_parents
        )
        transitive_children = sorted(
            other for other in result_set - {cid}
            if cid in ancestor_sets.get(other, set()) and other not in direct_children
        )
        relations[str(cid)] = {
            "direct_parents": [str(x) for x in direct_parents],
            "direct_children": [str(x) for x in direct_children],
            "ancestors": [str(x) for x in transitive_parents],
            "descendants": [str(x) for x in transitive_children],
        }

    nodes = []
    for cid in node_ids:
        term, tag = labels.get(cid, (str(cid), ""))
        nodes.append({
            "id": str(cid), "term": term, "semantic_tag": tag,
            "is_result": cid in rank, "result_rank": rank.get(cid),
            "depth": distances.get(cid, max_depth + 1),
        })
    return {
        "nodes": nodes,
        "edges": [{"child": str(c), "parent": str(p)} for c, p in sorted(set(edges))],
        "relations": relations,
        "truncated": truncated,
    }
