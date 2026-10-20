from __future__ import annotations

import ast
import unittest

from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import convert_expression, convert_params
from codeweaver.ir.base import IRUnsupported
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRAwait,
    IRBinary,
    IRCall,
    IRCompare,
    IRConditional,
    IRIndex,
    IRLambda,
    IRLiteral,
    IRLogical,
    IRMapLiteral,
    IRName,
    IRSetLiteral,
    IRSlice,
    IRTemplateString,
    IRTupleLiteral,
    IRUnary,
)
from codeweaver.ir.operators import BinaryOp, CompareOp, LogicalOp, UnaryOp


class ExpressionConversionTestCase(unittest.TestCase):
    def convert(self, text: str):
        self.ctx = ConversionContext(path="m.py", source_lines=text.splitlines())
        return convert_expression(ast.parse(text, mode="eval").body, self.ctx)

    def warnings(self) -> list[str]:
        return [item.code for item in self.ctx.diagnostics.warnings]


class LiteralTests(ExpressionConversionTestCase):
    def test_integer_literal_keeps_value_type_and_source_text(self) -> None:
        node = self.convert("0x1f")
        self.assertIsInstance(node, IRLiteral)
        self.assertEqual(node.value, 31)
        self.assertEqual(node.source_text, "0x1f")
        self.assertEqual(str(node.value_type), "int")

    def test_string_bool_and_none_literals(self) -> None:
        self.assertEqual(str(self.convert("'a'").value_type), "str")
        self.assertEqual(str(self.convert("True").value_type), "bool")
        self.assertEqual(self.convert("None").value, None)

    def test_ellipsis_is_marked(self) -> None:
        node = self.convert("...")
        self.assertTrue(node.meta["pythonEllipsis"])

    def test_span_points_at_the_source(self) -> None:
        node = self.convert("1")
        self.assertEqual(node.span.start_line, 1)
        self.assertEqual(node.span.file, "m.py")


class OperatorTests(ExpressionConversionTestCase):
    def test_arithmetic_operators(self) -> None:
        self.assertEqual(self.convert("a + b").op, BinaryOp.ADD)
        self.assertEqual(self.convert("a // b").op, BinaryOp.FLOOR_DIV)
        self.assertEqual(self.convert("a ** b").op, BinaryOp.POW)
        self.assertEqual(self.convert("a @ b").op, BinaryOp.MAT_MUL)

    def test_bitwise_and_shift_operators(self) -> None:
        self.assertEqual(self.convert("a & b").op, BinaryOp.BIT_AND)
        self.assertEqual(self.convert("a << b").op, BinaryOp.SHIFT_LEFT)

    def test_unary_operators(self) -> None:
        self.assertEqual(self.convert("-a").op, UnaryOp.NEG)
        self.assertEqual(self.convert("not a").op, UnaryOp.NOT)
        self.assertEqual(self.convert("~a").op, UnaryOp.BIT_NOT)

    def test_not_is_typed_as_bool(self) -> None:
        self.assertEqual(str(self.convert("not a").value_type), "bool")

    def test_nested_binary_structure(self) -> None:
        node = self.convert("a + b * c")
        self.assertIsInstance(node, IRBinary)
        self.assertEqual(node.op, BinaryOp.ADD)
        self.assertEqual(node.right.op, BinaryOp.MUL)

    def test_logical_operators(self) -> None:
        node = self.convert("a and b and c")
        self.assertIsInstance(node, IRLogical)
        self.assertEqual(node.op, LogicalOp.AND)
        self.assertEqual(len(node.operands), 3)
        self.assertEqual(self.convert("a or b").op, LogicalOp.OR)


class ComparisonTests(ExpressionConversionTestCase):
    def test_simple_comparison(self) -> None:
        node = self.convert("a == b")
        self.assertIsInstance(node, IRCompare)
        self.assertEqual(node.op, CompareOp.EQ)

    def test_membership_and_identity(self) -> None:
        self.assertEqual(self.convert("a in b").op, CompareOp.IN)
        self.assertEqual(self.convert("a not in b").op, CompareOp.NOT_IN)
        self.assertEqual(self.convert("a is not b").op, CompareOp.IS_NOT)

    def test_chained_comparison_expands_to_a_conjunction(self) -> None:
        node = self.convert("a < b < c")
        self.assertIsInstance(node, IRLogical)
        self.assertEqual(node.op, LogicalOp.AND)
        self.assertEqual([part.op for part in node.operands], [CompareOp.LT, CompareOp.LT])

    def test_chain_over_a_name_does_not_warn(self) -> None:
        self.convert("0 <= i < n")
        self.assertEqual(self.warnings(), [])

    def test_chain_over_a_call_warns_about_double_evaluation(self) -> None:
        self.convert("0 < f() < 10")
        self.assertIn("CW0401", self.warnings())


class CallTests(ExpressionConversionTestCase):
    def test_positional_arguments(self) -> None:
        node = self.convert("fn(1, 2)")
        self.assertIsInstance(node, IRCall)
        self.assertEqual(len(node.args), 2)

    def test_keyword_arguments(self) -> None:
        node = self.convert("fn(a=1)")
        self.assertEqual(node.keywords[0].name, "a")

    def test_star_args_and_star_kwargs_are_recorded_separately(self) -> None:
        node = self.convert("fn(*xs, **kw)")
        self.assertEqual(len(node.star_args), 1)
        self.assertEqual(len(node.star_keywords), 1)
        self.assertEqual(node.args, [])

    def test_method_call_on_an_attribute(self) -> None:
        node = self.convert("obj.method(1)")
        self.assertIsInstance(node.callee, IRAttribute)
        self.assertEqual(node.callee.name, "method")


class AccessTests(ExpressionConversionTestCase):
    def test_attribute_access(self) -> None:
        node = self.convert("a.b.c")
        self.assertIsInstance(node, IRAttribute)
        self.assertEqual(node.name, "c")
        self.assertEqual(node.target.name, "b")

    def test_index_access(self) -> None:
        node = self.convert("xs[0]")
        self.assertIsInstance(node, IRIndex)
        self.assertEqual(node.index.value, 0)

    def test_full_slice(self) -> None:
        node = self.convert("xs[1:5:2]")
        self.assertIsInstance(node, IRSlice)
        self.assertEqual(node.lower.value, 1)
        self.assertEqual(node.upper.value, 5)
        self.assertEqual(node.step.value, 2)

    def test_partial_slice_leaves_bounds_absent(self) -> None:
        node = self.convert("xs[:3]")
        self.assertIsNone(node.lower)
        self.assertEqual(node.upper.value, 3)


class CollectionLiteralTests(ExpressionConversionTestCase):
    def test_list_literal(self) -> None:
        node = self.convert("[1, 2, 3]")
        self.assertIsInstance(node, IRArrayLiteral)
        self.assertEqual(len(node.elements), 3)

    def test_tuple_and_set_literals(self) -> None:
        self.assertIsInstance(self.convert("(1, 2)"), IRTupleLiteral)
        self.assertIsInstance(self.convert("{1, 2}"), IRSetLiteral)

    def test_dict_literal(self) -> None:
        node = self.convert("{'a': 1, 'b': 2}")
        self.assertIsInstance(node, IRMapLiteral)
        self.assertEqual(len(node.entries), 2)
        self.assertEqual(node.entries[0].key.value, "a")

    def test_dict_spread_is_reported(self) -> None:
        node = self.convert("{**other}")
        self.assertIsInstance(node.entries[0].key, IRUnsupported)
        self.assertIn("CW0400", self.warnings())

    def test_list_spread_is_reported(self) -> None:
        node = self.convert("[*xs, 1]")
        self.assertIsInstance(node.elements[0], IRUnsupported)


class StringInterpolationTests(ExpressionConversionTestCase):
    def test_fstring_becomes_a_template(self) -> None:
        node = self.convert("f'hi {name}!'")
        self.assertIsInstance(node, IRTemplateString)
        kinds = [part.kind for part in node.parts]
        self.assertEqual(kinds, ["literal", "name", "literal"])

    def test_fstring_with_only_text(self) -> None:
        node = self.convert("f'plain'")
        self.assertEqual(len(node.parts), 1)

    def test_fstring_format_spec_is_reported(self) -> None:
        node = self.convert("f'{value:.2f}'")
        self.assertIsInstance(node.parts[0], IRUnsupported)
        self.assertIn("CW0400", self.warnings())

    def test_fstring_conversion_is_reported(self) -> None:
        node = self.convert("f'{value!r}'")
        self.assertIsInstance(node.parts[0], IRUnsupported)


class MiscExpressionTests(ExpressionConversionTestCase):
    def test_lambda(self) -> None:
        node = self.convert("lambda x, y=1: x + y")
        self.assertIsInstance(node, IRLambda)
        self.assertEqual([param.name for param in node.params], ["x", "y"])
        self.assertIsInstance(node.body, IRBinary)

    def test_conditional_expression(self) -> None:
        node = self.convert("a if cond else b")
        self.assertIsInstance(node, IRConditional)
        self.assertIsInstance(node.test, IRName)

    def test_await(self) -> None:
        self.assertIsInstance(self.convert("await fetch()"), IRAwait)

    def test_unary_on_literal(self) -> None:
        self.assertIsInstance(self.convert("-1"), IRUnary)


class UnsupportedExpressionTests(ExpressionConversionTestCase):
    def test_comprehensions_are_reported_with_a_hint(self) -> None:
        for text in (
            "[x for x in xs]",
            "{x for x in xs}",
            "{k: v for k, v in xs}",
            "(x for x in xs)",
        ):
            node = self.convert(text)
            self.assertIsInstance(node, IRUnsupported)
            self.assertIn("explicit loop", node.hint)

    def test_walrus_is_reported(self) -> None:
        node = convert_expression(
            ast.parse("(n := 10)", mode="eval").body,
            ConversionContext(path="m.py"),
        )
        self.assertIsInstance(node, IRUnsupported)
        self.assertEqual(node.source_kind, "NamedExpr")

    def test_unsupported_node_keeps_the_original_source_text(self) -> None:
        node = self.convert("[x for x in xs]")
        self.assertEqual(node.source_text, "[x for x in xs]")

    def test_every_unsupported_node_produces_a_diagnostic(self) -> None:
        self.convert("[x for x in xs]")
        self.assertEqual(self.warnings(), ["CW0400"])


class ParameterConversionTests(unittest.TestCase):
    def convert(self, signature: str):
        ctx = ConversionContext(path="m.py")
        tree = ast.parse(f"def f({signature}): pass")
        return convert_params(tree.body[0].args, ctx), ctx

    def test_plain_parameters(self) -> None:
        params, _ = self.convert("a, b")
        self.assertEqual([p.style for p in params], ["positional", "positional"])

    def test_defaults_attach_to_the_right_parameters(self) -> None:
        params, _ = self.convert("a, b=1, c=2")
        self.assertIsNone(params[0].default)
        self.assertEqual(params[1].default.value, 1)
        self.assertEqual(params[2].default.value, 2)

    def test_annotations_are_converted(self) -> None:
        params, _ = self.convert("a: int, b: list[str]")
        self.assertEqual(str(params[0].param_type), "int")
        self.assertEqual(str(params[1].param_type), "list[str]")

    def test_positional_only_parameters_are_included(self) -> None:
        params, _ = self.convert("a, /, b")
        self.assertEqual([p.name for p in params], ["a", "b"])

    def test_var_positional_and_keyword_only_and_var_keyword(self) -> None:
        params, _ = self.convert("a, *args, kw=1, **kwargs")
        self.assertEqual(
            [(p.name, p.style) for p in params],
            [
                ("a", "positional"),
                ("args", "var_positional"),
                ("kw", "keyword"),
                ("kwargs", "var_keyword"),
            ],
        )

    def test_keyword_only_without_default(self) -> None:
        params, _ = self.convert("*, kw")
        self.assertEqual(params[0].style, "keyword")
        self.assertIsNone(params[0].default)

    def test_empty_signature(self) -> None:
        params, _ = self.convert("")
        self.assertEqual(params, [])


if __name__ == "__main__":
    unittest.main()
