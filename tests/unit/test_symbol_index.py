from __future__ import annotations

import unittest

from codeweaver.frontends.python import parse_source
from codeweaver.graph.symbols import SymbolKind, build_symbol_index

SOURCE_A = (
    "VERSION = '1'\n"
    "_private = 2\n"
    "\n"
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "def _helper():\n"
    "    pass\n"
    "\n"
    "class Account:\n"
    "    rate: float = 0.1\n"
    "\n"
    "    def __init__(self):\n"
    "        pass\n"
    "\n"
    "    def deposit(self, amount):\n"
    "        pass\n"
)

SOURCE_B = "def add(a, b):\n    return a\n"


def index_for(*sources):
    modules = []
    for path, text in sources:
        result = parse_source(text, path)
        assert result.module is not None
        modules.append(result.module)
    return build_symbol_index(modules)


class SymbolIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index = index_for(("pkg/a.py", SOURCE_A), ("pkg/b.py", SOURCE_B))

    def test_module_symbols_are_registered(self) -> None:
        self.assertEqual(self.index.modules(), ["pkg.a", "pkg.b"])
        self.assertEqual(self.index.get("pkg.a").kind, SymbolKind.MODULE)

    def test_functions_classes_and_variables_are_indexed(self) -> None:
        kinds = {
            "pkg.a.add": SymbolKind.FUNCTION,
            "pkg.a.Account": SymbolKind.CLASS,
            "pkg.a.VERSION": SymbolKind.VARIABLE,
        }
        for symbol_id, kind in kinds.items():
            self.assertEqual(self.index.get(symbol_id).kind, kind, symbol_id)

    def test_methods_and_fields_record_their_owner(self) -> None:
        method = self.index.get("pkg.a.Account.deposit")
        self.assertEqual(method.kind, SymbolKind.METHOD)
        self.assertEqual(method.owner, "pkg.a.Account")
        field = self.index.get("pkg.a.Account.rate")
        self.assertEqual(field.kind, SymbolKind.FIELD)

    def test_members_of_a_class(self) -> None:
        members = [symbol.name for symbol in self.index.members_of("pkg.a.Account")]
        self.assertEqual(members, ["__init__", "deposit", "rate"])

    def test_underscore_names_are_not_exported(self) -> None:
        self.assertFalse(self.index.get("pkg.a._helper").exported)
        self.assertFalse(self.index.get("pkg.a._private").exported)
        self.assertTrue(self.index.get("pkg.a.add").exported)

    def test_by_name_finds_the_same_name_in_several_modules(self) -> None:
        found = sorted(symbol.id for symbol in self.index.by_name("add"))
        self.assertEqual(found, ["pkg.a.add", "pkg.b.add"])

    def test_by_name_for_an_unknown_name_is_empty(self) -> None:
        self.assertEqual(self.index.by_name("nope"), [])

    def test_in_module_returns_only_that_module(self) -> None:
        ids = {symbol.id for symbol in self.index.in_module("pkg.b")}
        self.assertEqual(ids, {"pkg.b", "pkg.b.add"})

    def test_top_level_excludes_the_module_and_class_members(self) -> None:
        names = sorted(symbol.name for symbol in self.index.top_level_of("pkg.a"))
        self.assertEqual(names, ["Account", "VERSION", "_helper", "_private", "add"])

    def test_resolve_in_module(self) -> None:
        self.assertIsNotNone(self.index.resolve_in_module("pkg.a", "add"))
        self.assertIsNone(self.index.resolve_in_module("pkg.a", "missing"))

    def test_symbols_carry_source_spans(self) -> None:
        symbol = self.index.get("pkg.a.add")
        self.assertEqual(symbol.span.file, "pkg/a.py")
        self.assertEqual(symbol.span.start_line, 4)

    def test_contains_and_len(self) -> None:
        self.assertIn("pkg.a.add", self.index)
        self.assertNotIn("pkg.a.nope", self.index)
        self.assertGreater(len(self.index), 5)

    def test_build_is_deterministic_regardless_of_input_order(self) -> None:
        reversed_index = index_for(("pkg/b.py", SOURCE_B), ("pkg/a.py", SOURCE_A))
        self.assertEqual(sorted(self.index.symbols), sorted(reversed_index.symbols))

    def test_to_dict_is_sorted(self) -> None:
        ids = [item["id"] for item in self.index.to_dict()["symbols"]]
        self.assertEqual(ids, sorted(ids))

    def test_empty_project_produces_an_empty_index(self) -> None:
        self.assertEqual(len(build_symbol_index([])), 0)


if __name__ == "__main__":
    unittest.main()
