from __future__ import annotations

import ast
import unittest

from codeweaver.backends.typescript.context import EmitContext
from codeweaver.backends.typescript.expressions import emit_expression
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import convert_expression


class ComprehensionEmitTests(unittest.TestCase):
    def emit(self, python_source: str) -> str:
        convert_ctx = ConversionContext(path="m.py", source_lines=[python_source])
        node = convert_expression(ast.parse(python_source, mode="eval").body, convert_ctx)
        self.ctx = EmitContext(source_path="m.py")
        return emit_expression(node, self.ctx).text

    def test_mapping_comprehension_becomes_map(self) -> None:
        self.assertEqual(self.emit("[x * 2 for x in xs]"), "xs.map((x) => x * 2)")

    def test_filter_only_comprehension_becomes_filter(self) -> None:
        self.assertEqual(self.emit("[x for x in xs if x > 0]"), "xs.filter((x) => x > 0)")

    def test_filter_and_map_are_chained_in_order(self) -> None:
        self.assertEqual(
            self.emit("[x * 2 for x in xs if x > 0]"),
            "xs.filter((x) => x > 0).map((x) => x * 2)",
        )

    def test_identity_comprehension_copies_the_sequence(self) -> None:
        self.assertEqual(self.emit("[x for x in xs]"), "[...xs]")

    def test_set_comprehension_builds_a_set(self) -> None:
        self.assertEqual(self.emit("{x for x in xs}"), "new Set([...xs])")

    def test_set_comprehension_with_a_mapping(self) -> None:
        self.assertEqual(self.emit("{x * 2 for x in xs}"), "new Set(xs.map((x) => x * 2))")

    def test_dict_comprehension_uses_from_entries(self) -> None:
        self.assertEqual(
            self.emit("{k: k * 2 for k in ks}"),
            "Object.fromEntries(ks.map((k) => [k, k * 2]))",
        )

    def test_dict_comprehension_with_a_filter(self) -> None:
        self.assertEqual(
            self.emit("{k: 1 for k in ks if k}"),
            "Object.fromEntries(ks.filter((k) => k).map((k) => [k, 1]))",
        )

    def test_comprehension_over_a_call_is_parenthesised_correctly(self) -> None:
        self.assertEqual(self.emit("[x for x in items()]"), "[...items()]")

    def test_comprehension_over_a_conditional_is_parenthesised(self) -> None:
        self.assertEqual(self.emit("[x for x in (a if c else b)]"), "[...(c ? a : b)]")

    def test_nested_comprehension_in_an_element(self) -> None:
        self.assertEqual(self.emit("[[y for y in x] for x in xs]"), "xs.map((x) => [...x])")

    def test_reserved_word_target_is_made_safe(self) -> None:
        self.assertEqual(self.emit("[var for var in xs]"), "[...xs]")

    def test_comprehension_produces_no_gap_markers(self) -> None:
        self.emit("[x for x in xs]")
        self.assertEqual(self.ctx.gaps, [])

    def test_comprehension_is_usable_as_a_call_argument(self) -> None:
        self.assertEqual(self.emit("collect([x for x in xs])"), "collect([...xs])")


if __name__ == "__main__":
    unittest.main()
