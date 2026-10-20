from __future__ import annotations

import unittest

from codeweaver.graph.analysis import (
    find_cycles,
    strongly_connected_components,
    topological_groups,
)


def adjacency(**edges: str) -> dict[str, set[str]]:
    """Build an adjacency map from ``a="b c"`` keyword pairs."""
    graph: dict[str, set[str]] = {}
    for source, targets in edges.items():
        graph.setdefault(source, set())
        for target in targets.split():
            graph[source].add(target)
            graph.setdefault(target, set())
    return graph


class SccTests(unittest.TestCase):
    def test_empty_graph(self) -> None:
        self.assertEqual(strongly_connected_components({}), [])

    def test_single_node(self) -> None:
        self.assertEqual(strongly_connected_components({"a": set()}), [("a",)])

    def test_chain_is_in_dependency_order(self) -> None:
        found = strongly_connected_components(adjacency(a="b", b="c"))
        self.assertEqual(found, [("c",), ("b",), ("a",)])

    def test_two_node_cycle_is_one_component(self) -> None:
        found = strongly_connected_components(adjacency(a="b", b="a"))
        self.assertEqual(found, [("a", "b")])

    def test_three_node_cycle(self) -> None:
        found = strongly_connected_components(adjacency(a="b", b="c", c="a"))
        self.assertEqual(found, [("a", "b", "c")])

    def test_cycle_with_a_dependency_outside_it(self) -> None:
        found = strongly_connected_components(adjacency(a="b", b="a c", e="a"))
        self.assertIn(("c",), found)
        self.assertTrue(any(component == ("a", "b") for component in found))
        self.assertLess(found.index(("c",)), found.index(("a", "b")))

    def test_disconnected_components_are_all_returned(self) -> None:
        found = strongly_connected_components(adjacency(a="b", c="d"))
        self.assertEqual(len(found), 4)

    def test_self_loop_is_its_own_component(self) -> None:
        self.assertEqual(strongly_connected_components(adjacency(a="a")), [("a",)])

    def test_edges_to_unknown_nodes_are_ignored(self) -> None:
        found = strongly_connected_components({"a": {"missing"}})
        self.assertEqual(found, [("a",)])

    def test_result_is_deterministic(self) -> None:
        graph = adjacency(a="b c", b="d", c="d", d="a", e="a")
        self.assertEqual(strongly_connected_components(graph), strongly_connected_components(graph))

    def test_deep_chain_does_not_exhaust_recursion(self) -> None:
        depth = 5000
        graph = {f"n{i}": {f"n{i + 1}"} for i in range(depth)}
        graph[f"n{depth}"] = set()
        found = strongly_connected_components(graph)
        self.assertEqual(len(found), depth + 1)
        self.assertEqual(found[0], (f"n{depth}",))


class TopologicalGroupTests(unittest.TestCase):
    def test_chain_produces_single_member_groups_in_order(self) -> None:
        groups = topological_groups(adjacency(a="b", b="c"))
        self.assertEqual([group.members for group in groups], [("c",), ("b",), ("a",)])
        self.assertEqual([group.index for group in groups], [0, 1, 2])

    def test_group_dependencies_point_backwards(self) -> None:
        groups = topological_groups(adjacency(a="b", b="c"))
        by_member = {group.members[0]: group for group in groups}
        self.assertEqual(by_member["a"].depends_on, (by_member["b"].index,))
        self.assertEqual(by_member["c"].depends_on, ())

    def test_every_dependency_index_is_lower(self) -> None:
        groups = topological_groups(adjacency(a="b c", b="d", c="d", e="a"))
        for group in groups:
            for dependency in group.depends_on:
                self.assertLess(dependency, group.index)

    def test_cycle_group_is_marked(self) -> None:
        groups = topological_groups(adjacency(a="b", b="a"))
        self.assertTrue(groups[0].is_cycle)
        self.assertFalse(topological_groups(adjacency(a="b"))[0].is_cycle)

    def test_cycle_group_keeps_its_external_dependency(self) -> None:
        groups = topological_groups(adjacency(a="b c", b="a"))
        cycle = next(group for group in groups if group.is_cycle)
        self.assertEqual(len(cycle.depends_on), 1)

    def test_to_dict(self) -> None:
        payload = topological_groups(adjacency(a="b", b="a"))[0].to_dict()
        self.assertEqual(payload["members"], ["a", "b"])
        self.assertTrue(payload["isCycle"])

    def test_empty_graph_has_no_groups(self) -> None:
        self.assertEqual(topological_groups({}), [])


class CycleTests(unittest.TestCase):
    def test_acyclic_graph_has_no_cycles(self) -> None:
        self.assertEqual(find_cycles(adjacency(a="b", b="c")), [])

    def test_multi_node_cycle_is_found(self) -> None:
        self.assertEqual(find_cycles(adjacency(a="b", b="a")), [("a", "b")])

    def test_self_loop_is_reported(self) -> None:
        self.assertEqual(find_cycles(adjacency(a="a")), [("a",)])

    def test_several_cycles_are_all_reported(self) -> None:
        found = find_cycles(adjacency(a="b", b="a", c="d", d="c"))
        self.assertEqual(sorted(found), [("a", "b"), ("c", "d")])


if __name__ == "__main__":
    unittest.main()
