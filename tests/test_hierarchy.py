"""Hierarchy graph tests using the salivary-imaging branch from the UI example."""

from api.hierarchy import hierarchy_for_results


def test_direct_parent_child_relations_and_spanish_labels():
    graph = hierarchy_for_results([417994007, 118768005, 441875004])

    focal = graph["relations"]["417994007"]
    assert set(focal["direct_parents"]) == {"118768005", "441875004"}
    assert graph["relations"]["118768005"]["direct_children"] == ["417994007"]
    assert graph["relations"]["441875004"]["direct_children"] == ["417994007"]

    labels = {node["id"]: node["term"] for node in graph["nodes"]}
    assert labels["417994007"] == "imagenología de glándula salival"
    assert labels["71388002"] == "procedimiento"
    assert labels["138875005"] == "concepto de SNOMED CT"
    assert {"child": "417994007", "parent": "118768005"} in graph["edges"]
    assert {"child": "417994007", "parent": "441875004"} in graph["edges"]


def test_transitive_result_relation_is_not_mislabeled_as_direct():
    graph = hierarchy_for_results([417994007, 71388002])
    assert graph["relations"]["417994007"]["direct_parents"] == []
    assert graph["relations"]["417994007"]["ancestors"] == ["71388002"]
    assert graph["relations"]["71388002"]["descendants"] == ["417994007"]
