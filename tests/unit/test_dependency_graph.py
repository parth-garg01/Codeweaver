from __future__ import annotations

import unittest

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.graph.dependency import (
    EdgeKind,
    NodeKind,
    build_dependency_graph,
    resolve_relative_import,
)

MODELS = (
    "class Base:\n"
    "    pass\n"
    "\n"
    "class Account(Base):\n"
    "    rate: float = 0.0\n"
    "\n"
    "    def deposit(self, amount: int) -> None:\n"
    "        pass\n"
)

SERVICE = (
    "import os\n"
    "from pkg.models import Account, Missing\n"
    "\n"
    "def open_account(owner: str) -> Account:\n"
    "    return Account()\n"
    "\n"
    "def audit():\n"
    "    open_account('x')\n"
)


def graph_for(*sources, bag=None):
    modules = []
    for path, text in sources:
        result = parse_source(text, path)
        assert result.module is not None, path
        modules.append(result.module)
    return build_dependency_graph(modules, diagnostics=bag)


class RelativeImportTests(unittest.TestCase):
    def test_level_zero_is_absolute(self) -> None:
        self.assertEqual(resolve_relative_import("a.b.c", "x.y", 0), "x.y")

    def test_one_dot_resolves_within_the_package(self) -> None:
        self.assertEqual(resolve_relative_import("a.b.c", "d", 1), "a.b.d")

    def test_two_dots_go_up_one_package(self) -> None:
        self.assertEqual(resolve_relative_import("a.b.c", "d", 2), "a.d")

    def test_bare_relative_import_names_the_package(self) -> None:
        self.assertEqual(resolve_relative_import("a.b.c", "", 1), "a.b")

    def test_too_many_dots_do_not_crash(self) -> None:
        self.assertEqual(resolve_relative_import("a", "d", 5), "d")


class GraphStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bag = DiagnosticBag()
        self.graph = graph_for(("pkg/models.py", MODELS), ("pkg/service.py", SERVICE), bag=self.bag)

    def test_modules_and_symbols_become_nodes(self) -> None:
        self.assertEqual(
            [node.id for node in self.graph.module_nodes()], ["pkg.models", "pkg.service"]
        )
        self.assertEqual(self.graph.nodes["pkg.models.Account"].kind, NodeKind.SYMBOL)
        self.assertEqual(self.graph.nodes["pkg.models.Account"].symbol_kind, "class")

    def test_resolved_module_import_edge(self) -> None:
        targets = self.graph.successors("pkg.service", (EdgeKind.IMPORT,))
        self.assertIn("pkg.models", targets)
        self.assertIn("pkg.models.Account", targets)

    def test_external_import_becomes_an_external_node_and_unresolved_edge(self) -> None:
        self.assertEqual([node.label for node in self.graph.external_nodes()], ["os"])
        unresolved = self.graph.unresolved_edges()
        self.assertEqual([edge.target for edge in unresolved], ["external:os"])
        self.assertFalse(unresolved[0].resolved)

    def test_external_import_is_reported(self) -> None:
        self.assertIn("CW0500", [item.code for item in self.bag])

    def test_importing_a_name_the_module_does_not_declare_warns(self) -> None:
        self.assertIn("CW0501", [item.code for item in self.bag])

    def test_inheritance_edge(self) -> None:
        edges = self.graph.edges_of(EdgeKind.INHERIT)
        self.assertEqual(
            [(edge.source, edge.target) for edge in edges],
            [("pkg.models.Account", "pkg.models.Base")],
        )

    def test_call_edge_between_project_functions(self) -> None:
        calls = {(edge.source, edge.target) for edge in self.graph.edges_of(EdgeKind.CALL)}
        self.assertIn(("pkg.service.audit", "pkg.service.open_account"), calls)

    def test_call_edge_to_an_imported_class(self) -> None:
        calls = {(edge.source, edge.target) for edge in self.graph.edges_of(EdgeKind.CALL)}
        self.assertIn(("pkg.service.open_account", "pkg.models.Account"), calls)

    def test_type_edge_from_a_return_annotation(self) -> None:
        types = {(edge.source, edge.target) for edge in self.graph.edges_of(EdgeKind.TYPE)}
        self.assertIn(("pkg.service.open_account", "pkg.models.Account"), types)

    def test_edges_carry_spans(self) -> None:
        edge = self.graph.edges_of(EdgeKind.INHERIT)[0]
        self.assertEqual(edge.span.file, "pkg/models.py")

    def test_predecessors(self) -> None:
        self.assertIn("pkg.service.audit", self.graph.predecessors("pkg.service.open_account"))

    def test_duplicate_edges_are_collapsed(self) -> None:
        graph = graph_for(("a.py", "def f():\n    g()\n    g()\n\ndef g():\n    pass\n"))
        calls = graph.edges_of(EdgeKind.CALL)
        self.assertEqual(len(calls), 1)

    def test_module_adjacency_projects_symbol_edges(self) -> None:
        adjacency = self.graph.module_adjacency()
        self.assertEqual(adjacency["pkg.service"], {"pkg.models"})
        self.assertEqual(adjacency["pkg.models"], set())

    def test_module_of_resolves_both_node_kinds(self) -> None:
        self.assertEqual(self.graph.module_of("pkg.models"), "pkg.models")
        self.assertEqual(self.graph.module_of("pkg.models.Account"), "pkg.models")
        self.assertEqual(self.graph.module_of("nope"), "")

    def test_to_dict_reports_counts(self) -> None:
        payload = self.graph.to_dict()
        self.assertEqual(payload["counts"]["nodes"], len(self.graph.nodes))
        self.assertEqual(payload["counts"]["unresolved"], 1)
        self.assertEqual(payload["counts"]["external"], 1)


class RelativeImportGraphTests(unittest.TestCase):
    def test_relative_import_resolves_to_a_sibling_module(self) -> None:
        graph = graph_for(
            ("pkg/__init__.py", ""),
            ("pkg/a.py", "def helper():\n    pass\n"),
            ("pkg/b.py", "from .a import helper\n\ndef use():\n    helper()\n"),
        )
        self.assertIn("pkg.a", graph.successors("pkg.b", (EdgeKind.IMPORT,)))
        calls = {(edge.source, edge.target) for edge in graph.edges_of(EdgeKind.CALL)}
        self.assertIn(("pkg.b.use", "pkg.a.helper"), calls)

    def test_unknown_base_class_is_an_unresolved_inheritance_edge(self) -> None:
        graph = graph_for(("a.py", "class C(Unknown):\n    pass\n"))
        edge = graph.edges_of(EdgeKind.INHERIT)[0]
        self.assertFalse(edge.resolved)
        self.assertEqual(edge.target, "external:Unknown")

    def test_empty_project_has_an_empty_graph(self) -> None:
        graph = build_dependency_graph([])
        self.assertEqual(graph.nodes, {})
        self.assertEqual(graph.edges, [])


if __name__ == "__main__":
    unittest.main()
