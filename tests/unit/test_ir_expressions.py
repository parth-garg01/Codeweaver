from __future__ import annotations

import unittest

from codeweaver.diagnostics import SourceSpan
from codeweaver.errors import IRSchemaError
from codeweaver.ir.base import (
    IRUnsupported,
    node_class,
    node_from_dict,
    registered_kinds,
    walk,
)
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRAwait,
    IRBinary,
    IRCall,
    IRCompare,
    IRConditional,
    IRIndex,
    IRKeyword,
    IRLambda,
    IRLiteral,
    IRLogical,
    IRMapEntry,
    IRMapLiteral,
    IRName,
    IRParam,
    IRSetLiteral,
    IRSlice,
    IRTemplateString,
    IRTupleLiteral,
    IRUnary,
)
from codeweaver.ir.operators import BinaryOp, CompareOp, LogicalOp, UnaryOp
from codeweaver.ir.types import TypeKind, t_int, t_list, t_str


def roundtrip(node):
    return node_from_dict(node.to_dict())


class NodeFoundationTests(unittest.TestCase):
    def test_default_value_type_is_unknown_not_any(self) -> None:
        self.assertEqual(IRLiteral(1).value_type.kind, TypeKind.UNKNOWN)

    def test_span_and_meta_are_keyword_only(self) -> None:
        node = IRName("x", span=SourceSpan("m.py", 1, 0), meta={"origin": "test"})
        self.assertEqual(node.span.start_line, 1)
        self.assertEqual(node.meta["origin"], "test")

    def test_to_dict_omits_absent_span_and_empty_meta(self) -> None:
        payload = IRName("x").to_dict()
        self.assertNotIn("span", payload)
        self.assertNotIn("meta", payload)

    def test_children_are_in_declaration_order(self) -> None:
        node = IRBinary(BinaryOp.ADD, IRName("a"), IRName("b"))
        self.assertEqual([child.name for child in node.children()], ["a", "b"])

    def test_children_include_list_members(self) -> None:
        node = IRArrayLiteral([IRName("a"), IRName("b"), IRName("c")])
        self.assertEqual(len(node.children()), 3)

    def test_walk_is_depth_first_parents_first(self) -> None:
        tree = IRBinary(BinaryOp.MUL, IRBinary(BinaryOp.ADD, IRName("a"), IRName("b")), IRName("c"))
        kinds = [node.kind for node in walk(tree)]
        self.assertEqual(kinds, ["binary", "binary", "name", "name", "name"])

    def test_every_registered_kind_resolves_to_its_class(self) -> None:
        for kind in registered_kinds():
            self.assertEqual(node_class(kind).kind, kind)

    def test_unknown_kind_is_rejected(self) -> None:
        with self.assertRaises(IRSchemaError):
            node_class("no-such-node")

    def test_payload_without_kind_is_rejected(self) -> None:
        with self.assertRaises(IRSchemaError):
            node_from_dict({"name": "x"})

    def test_unsupported_node_keeps_the_source_construct(self) -> None:
        node = IRUnsupported("Yield", "generators are not mapped", "yield x", "return a list")
        restored = roundtrip(node)
        self.assertEqual(restored.source_kind, "Yield")
        self.assertEqual(restored.hint, "return a list")


class ExpressionRoundTripTests(unittest.TestCase):
    def assert_roundtrips(self, node) -> None:
        self.assertEqual(roundtrip(node), node)

    def test_literal(self) -> None:
        self.assert_roundtrips(IRLiteral(42, source_text="0x2a", value_type=t_int()))

    def test_literal_none_and_bool(self) -> None:
        self.assert_roundtrips(IRLiteral(None))
        self.assert_roundtrips(IRLiteral(True))

    def test_name_with_resolution(self) -> None:
        self.assert_roundtrips(IRName("total", resolved_symbol="mod.total"))

    def test_binary(self) -> None:
        self.assert_roundtrips(IRBinary(BinaryOp.FLOOR_DIV, IRLiteral(7), IRLiteral(2)))

    def test_unary(self) -> None:
        self.assert_roundtrips(IRUnary(UnaryOp.NOT, IRName("flag")))

    def test_compare(self) -> None:
        self.assert_roundtrips(IRCompare(CompareOp.NOT_IN, IRName("x"), IRName("xs")))

    def test_logical(self) -> None:
        self.assert_roundtrips(IRLogical(LogicalOp.OR, [IRName("a"), IRName("b")]))

    def test_call_with_keywords_and_spreads(self) -> None:
        node = IRCall(
            IRName("fn"),
            args=[IRLiteral(1)],
            keywords=[IRKeyword("flag", IRLiteral(True))],
            star_args=[IRName("rest")],
            star_keywords=[IRName("options")],
        )
        self.assert_roundtrips(node)

    def test_attribute_and_index(self) -> None:
        self.assert_roundtrips(IRAttribute(IRName("obj"), "field"))
        self.assert_roundtrips(IRIndex(IRName("xs"), IRLiteral(0)))

    def test_slice_with_optional_bounds(self) -> None:
        self.assert_roundtrips(IRSlice(IRName("xs"), lower=IRLiteral(1)))
        self.assert_roundtrips(IRSlice(IRName("xs")))

    def test_collection_literals(self) -> None:
        self.assert_roundtrips(IRArrayLiteral([IRLiteral(1), IRLiteral(2)]))
        self.assert_roundtrips(IRTupleLiteral([IRLiteral(1), IRName("b")]))
        self.assert_roundtrips(IRSetLiteral([IRLiteral("a")]))

    def test_map_literal(self) -> None:
        node = IRMapLiteral([IRMapEntry(IRLiteral("k"), IRLiteral(1))])
        self.assert_roundtrips(node)

    def test_param_with_type_and_default(self) -> None:
        self.assert_roundtrips(IRParam("items", t_list(t_str()), IRArrayLiteral([]), "keyword"))

    def test_lambda(self) -> None:
        self.assert_roundtrips(IRLambda([IRParam("x")], IRName("x")))

    def test_conditional(self) -> None:
        self.assert_roundtrips(IRConditional(IRName("ok"), IRLiteral(1), IRLiteral(0)))

    def test_template_string(self) -> None:
        node = IRTemplateString([IRLiteral("hi "), IRName("name"), IRLiteral("!")])
        self.assert_roundtrips(node)

    def test_await(self) -> None:
        self.assert_roundtrips(IRAwait(IRCall(IRName("fetch"))))

    def test_value_type_survives_round_trip(self) -> None:
        restored = roundtrip(IRArrayLiteral([], value_type=t_list(t_int())))
        self.assertEqual(str(restored.value_type), "list[int]")

    def test_span_survives_round_trip(self) -> None:
        restored = roundtrip(IRName("x", span=SourceSpan("m.py", 4, 2, 4, 3)))
        self.assertEqual(str(restored.span), "m.py:4:3")


if __name__ == "__main__":
    unittest.main()
