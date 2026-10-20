from __future__ import annotations

import unittest

from codeweaver.frontends.python import parse_source
from codeweaver.graph import EdgeKind, analyse_impact, build_dependency_graph, search_nodes

MODELS = "class Account:\n    def deposit(self, amount):\n        pass\n"
REPO = "from pkg.models import Account\n\ndef load() -> Account:\n    return Account()\n"
SERVICE = "from pkg.repo import load\n\ndef run():\n    load()\n"


def build_graph():
    modules = []
    for path, text in (
        ("pkg/models.py", MODELS),
        ("pkg/repo.py", REPO),
        ("pkg/service.py", SERVICE),
    ):
        result = parse_source(text, path)
        assert result.module is not None
        modules.append(result.module)
    return build_dependency_graph(modules)


class ImpactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = build_graph()

    def test_unknown_origin_is_reported_as_not_found(self) -> None:
        report = analyse_impact(self.graph, "pkg.nope")
        self.assertFalse(report.found)
        self.assertEqual(report.dependents, {})

    def test_direct_dependents_are_at_distance_one(self) -> None:
        report = analyse_impact(self.graph, "pkg.models.Account")
        self.assertIn("pkg.repo.load", report.direct_dependents)
        self.assertEqual(report.dependents["pkg.repo.load"], 1)

    def test_transitive_dependents_record_their_distance(self) -> None:
        report = analyse_impact(self.graph, "pkg.models.Account")
        self.assertGreater(report.dependents["pkg.service.run"], 1)

    def test_affected_modules_are_listed(self) -> None:
        report = analyse_impact(self.graph, "pkg.models.Account")
        self.assertIn("pkg.repo", report.affected_modules)
        self.assertIn("pkg.service", report.affected_modules)

    def test_dependencies_point_the_other_way(self) -> None:
        report = analyse_impact(self.graph, "pkg.service.run")
        self.assertIn("pkg.repo.load", report.dependencies)
        self.assertNotIn("pkg.repo.load", report.dependents)

    def test_a_leaf_has_no_dependents(self) -> None:
        report = analyse_impact(self.graph, "pkg.service.run")
        self.assertEqual(report.dependents, {})

    def test_max_depth_bounds_the_search(self) -> None:
        report = analyse_impact(self.graph, "pkg.models.Account", max_depth=1)
        self.assertEqual(sorted(report.dependents.values()), [1] * len(report.dependents))

    def test_edge_kind_filter_restricts_traversal(self) -> None:
        report = analyse_impact(self.graph, "pkg.models.Account", kinds=(EdgeKind.INHERIT,))
        self.assertEqual(report.dependents, {})

    def test_unresolved_edges_are_not_traversed(self) -> None:
        modules = []
        for path, text in (("a.py", "import requests\n"),):
            result = parse_source(text, path)
            modules.append(result.module)
        graph = build_dependency_graph(modules)
        report = analyse_impact(graph, "external:requests")
        self.assertEqual(report.dependents, {})

    def test_to_dict_is_sorted_and_json_friendly(self) -> None:
        import json

        payload = analyse_impact(self.graph, "pkg.models.Account").to_dict()
        self.assertEqual(list(payload["dependents"]), sorted(payload["dependents"]))
        json.dumps(payload)


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = build_graph()

    def test_search_matches_node_ids(self) -> None:
        found = [item["id"] for item in search_nodes(self.graph, "Account")]
        self.assertIn("pkg.models.Account", found)

    def test_search_is_case_insensitive(self) -> None:
        self.assertTrue(search_nodes(self.graph, "account"))

    def test_shorter_matches_come_first(self) -> None:
        found = [item["id"] for item in search_nodes(self.graph, "pkg")]
        self.assertEqual(found, sorted(found, key=lambda item: (len(item), item)))

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(search_nodes(self.graph, "   "), [])

    def test_limit_is_respected(self) -> None:
        self.assertLessEqual(len(search_nodes(self.graph, "pkg", limit=2)), 2)

    def test_no_match_returns_an_empty_list(self) -> None:
        self.assertEqual(search_nodes(self.graph, "zzzz"), [])


if __name__ == "__main__":
    unittest.main()
