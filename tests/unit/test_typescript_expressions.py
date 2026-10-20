from __future__ import annotations

import ast
import unittest

from codeweaver.backends.typescript.context import UNSUPPORTED_MARKER, EmitContext
from codeweaver.backends.typescript.expressions import emit_expression, emit_params
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import convert_expression, convert_params
from codeweaver.ir.base import IRUnsupported
from codeweaver.ir.expressions import IRLiteral, IRMapEntry, IRMapLiteral, IRName
from codeweaver.ir.types import t_dict, t_set, t_str


class EmitTestCase(unittest.TestCase):
    def emit(self, python_source: str) -> str:
        convert_ctx = ConversionContext(path="m.py", source_lines=[python_source])
        node = convert_expression(ast.parse(python_source, mode="eval").body, convert_ctx)
        self.ctx = EmitContext(source_path="m.py")
        return emit_expression(node, self.ctx).text

    def codes(self) -> list[str]:
        return [item.code for item in self.ctx.diagnostics]


class LiteralEmitTests(EmitTestCase):
    def test_numbers_keep_their_source_spelling(self) -> None:
        self.assertEqual(self.emit("0x1f"), "0x1f")
        self.assertEqual(self.emit("1_000"), "1_000")

    def test_strings_are_double_quoted(self) -> None:
        self.assertEqual(self.emit("'hi'"), '"hi"')

    def test_booleans_and_none(self) -> None:
        self.assertEqual(self.emit("True"), "true")
        self.assertEqual(self.emit("False"), "false")
        self.assertEqual(self.emit("None"), "null")

    def test_bytes_become_a_typed_array(self) -> None:
        self.assertEqual(self.emit("b'ab'"), "new Uint8Array([97, 98])")

    def test_ellipsis_becomes_undefined(self) -> None:
        self.assertEqual(self.emit("..."), "undefined")


class OperatorEmitTests(EmitTestCase):
    def test_arithmetic(self) -> None:
        self.assertEqual(self.emit("a + b"), "a + b")
        self.assertEqual(self.emit("a * b"), "a * b")

    def test_precedence_adds_no_redundant_parentheses(self) -> None:
        self.assertEqual(self.emit("a + b * c"), "a + b * c")

    def test_precedence_parenthesises_where_needed(self) -> None:
        self.assertEqual(self.emit("(a + b) * c"), "(a + b) * c")

    def test_subtraction_is_left_associative(self) -> None:
        self.assertEqual(self.emit("a - (b - c)"), "a - (b - c)")

    def test_power_is_right_associative(self) -> None:
        self.assertEqual(self.emit("a ** b ** c"), "a ** b ** c")
        self.assertEqual(self.emit("(a ** b) ** c"), "(a ** b) ** c")

    def test_floor_division_becomes_math_floor(self) -> None:
        self.assertEqual(self.emit("a // b"), "Math.floor(a / b)")

    def test_floor_division_of_a_sum_keeps_grouping(self) -> None:
        self.assertEqual(self.emit("(a + b) // c"), "Math.floor((a + b) / c)")

    def test_matrix_multiply_is_reported(self) -> None:
        self.assertEqual(self.emit("a @ b"), "undefined")
        self.assertIn("CW0603", self.codes())

    def test_unary_operators(self) -> None:
        self.assertEqual(self.emit("-a"), "-a")
        self.assertEqual(self.emit("not a"), "!a")
        self.assertEqual(self.emit("~a"), "~a")

    def test_unary_over_a_binary_is_parenthesised(self) -> None:
        self.assertEqual(self.emit("not (a and b)"), "!(a && b)")

    def test_logical_operators(self) -> None:
        self.assertEqual(self.emit("a and b"), "a && b")
        self.assertEqual(self.emit("a or b"), "a || b")

    def test_mixed_logical_precedence(self) -> None:
        self.assertEqual(self.emit("a or b and c"), "a || b && c")
        self.assertEqual(self.emit("(a or b) and c"), "(a || b) && c")


class ComparisonEmitTests(EmitTestCase):
    def test_equality_uses_strict_operators(self) -> None:
        self.assertEqual(self.emit("a == b"), "a === b")
        self.assertEqual(self.emit("a != b"), "a !== b")

    def test_identity_maps_onto_strict_equality(self) -> None:
        self.assertEqual(self.emit("a is b"), "a === b")
        self.assertEqual(self.emit("a is not b"), "a !== b")

    def test_relational_operators(self) -> None:
        self.assertEqual(self.emit("a < b"), "a < b")
        self.assertEqual(self.emit("a >= b"), "a >= b")

    def test_chained_comparison_becomes_a_conjunction(self) -> None:
        self.assertEqual(self.emit("0 <= i < n"), "0 <= i && i < n")

    def test_membership_on_an_unknown_container_warns(self) -> None:
        self.assertEqual(self.emit("x in xs"), "xs.includes(x)")
        self.assertIn("CW0604", self.codes())

    def test_membership_not_in_is_negated(self) -> None:
        self.assertEqual(self.emit("x not in xs"), "!xs.includes(x)")

    def test_membership_on_a_list_literal_does_not_warn(self) -> None:
        self.assertEqual(self.emit("x in [1, 2]"), "[1, 2].includes(x)")
        self.assertNotIn("CW0604", self.codes())

    def test_membership_on_a_dict_uses_has_own(self) -> None:
        ctx = EmitContext()
        node = emit_expression(
            _compare_in(IRName("k"), IRMapLiteral([], value_type=t_dict(t_str(), t_str()))),
            ctx,
        )
        self.assertIn("Object.hasOwn", node.text)

    def test_membership_on_a_set_uses_has(self) -> None:
        from codeweaver.ir.expressions import IRSetLiteral

        ctx = EmitContext()
        node = emit_expression(
            _compare_in(IRName("k"), IRSetLiteral([], value_type=t_set(t_str()))), ctx
        )
        self.assertIn(".has(", node.text)


def _compare_in(left, right):
    from codeweaver.ir.expressions import IRCompare
    from codeweaver.ir.operators import CompareOp

    return IRCompare(CompareOp.IN, left, right)


class CallEmitTests(EmitTestCase):
    def test_simple_call(self) -> None:
        self.assertEqual(self.emit("fn(1, 2)"), "fn(1, 2)")

    def test_method_call(self) -> None:
        self.assertEqual(self.emit("obj.method(1)"), "obj.method(1)")

    def test_keyword_arguments_become_an_options_object(self) -> None:
        self.assertEqual(self.emit("fn(a=1, b=2)"), "fn({ a: 1, b: 2 })")
        self.assertIn("CW0605", self.codes())

    def test_star_args_become_a_spread(self) -> None:
        self.assertEqual(self.emit("fn(*xs)"), "fn(...xs)")

    def test_double_star_kwargs_spread_is_reported(self) -> None:
        self.assertEqual(self.emit("fn(**kw)"), "fn(...kw)")
        self.assertIn("CW0606", self.codes())

    def test_call_on_a_conditional_is_parenthesised(self) -> None:
        self.assertEqual(self.emit("(a if c else b)()"), "(c ? a : b)()")


class AccessEmitTests(EmitTestCase):
    def test_attribute_chain(self) -> None:
        self.assertEqual(self.emit("a.b.c"), "a.b.c")

    def test_index(self) -> None:
        self.assertEqual(self.emit("xs[0]"), "xs[0]")

    def test_slice_with_both_bounds(self) -> None:
        self.assertEqual(self.emit("xs[1:5]"), "xs.slice(1, 5)")

    def test_slice_with_only_a_start(self) -> None:
        self.assertEqual(self.emit("xs[2:]"), "xs.slice(2)")

    def test_slice_with_only_an_end(self) -> None:
        self.assertEqual(self.emit("xs[:3]"), "xs.slice(0, 3)")

    def test_stepped_slice_is_reported(self) -> None:
        self.emit("xs[::2]")
        self.assertIn("CW0607", self.codes())


class CollectionEmitTests(EmitTestCase):
    def test_list_and_tuple_become_arrays(self) -> None:
        self.assertEqual(self.emit("[1, 2]"), "[1, 2]")
        self.assertEqual(self.emit("(1, 2)"), "[1, 2]")

    def test_set_becomes_a_set_constructor(self) -> None:
        self.assertEqual(self.emit("{1, 2}"), "new Set([1, 2])")

    def test_dict_becomes_an_object_literal(self) -> None:
        self.assertEqual(self.emit("{'a': 1}"), "{ a: 1 }")

    def test_empty_dict(self) -> None:
        self.assertEqual(self.emit("{}"), "{}")

    def test_invalid_key_is_quoted(self) -> None:
        self.assertEqual(self.emit("{'a-b': 1}"), '{ "a-b": 1 }')

    def test_computed_key_uses_brackets(self) -> None:
        ctx = EmitContext()
        node = IRMapLiteral([IRMapEntry(IRName("k"), IRLiteral(1))])
        self.assertEqual(emit_expression(node, ctx).text, "{ [k]: 1 }")

    def test_nested_collections(self) -> None:
        self.assertEqual(self.emit("{'a': [1, {'b': 2}]}"), "{ a: [1, { b: 2 }] }")


class TemplateEmitTests(EmitTestCase):
    def test_fstring_becomes_a_template_literal(self) -> None:
        self.assertEqual(self.emit("f'hi {name}!'"), "`hi ${name}!`")

    def test_backticks_and_dollar_braces_are_escaped(self) -> None:
        self.assertEqual(self.emit("f'a`b'"), "`a\\`b`")

    def test_literal_dollar_brace_is_escaped(self) -> None:
        from codeweaver.ir.expressions import IRTemplateString

        node = IRTemplateString([IRLiteral("cost ${x}")])
        self.assertEqual(emit_expression(node, EmitContext()).text, "`cost \\${x}`")

    def test_expression_inside_a_template(self) -> None:
        self.assertEqual(self.emit("f'{a + b}'"), "`${a + b}`")


class MiscEmitTests(EmitTestCase):
    def test_lambda_becomes_an_arrow_function(self) -> None:
        self.assertEqual(self.emit("lambda x: x + 1"), "(x: unknown) => x + 1")

    def test_conditional_expression(self) -> None:
        self.assertEqual(self.emit("a if c else b"), "c ? a : b")

    def test_nested_conditional_is_right_associative(self) -> None:
        self.assertEqual(self.emit("a if c else (b if d else e)"), "c ? a : d ? b : e")

    def test_await(self) -> None:
        self.assertEqual(self.emit("await fetch()"), "await fetch()")

    def test_reserved_word_names_are_made_safe(self) -> None:
        self.assertEqual(self.emit("function"), "function_")


class UnsupportedEmitTests(unittest.TestCase):
    def test_unsupported_node_becomes_a_marker_and_is_recorded(self) -> None:
        ctx = EmitContext()
        node = IRUnsupported("ListComp", "comprehensions are not lowered")
        text = emit_expression(node, ctx).text
        self.assertIn(UNSUPPORTED_MARKER, text)
        self.assertIn("ListComp", text)
        self.assertEqual(len(ctx.gaps), 1)
        self.assertEqual([item.code for item in ctx.diagnostics], ["CW0600"])

    def test_comment_terminator_in_a_reason_cannot_escape(self) -> None:
        ctx = EmitContext()
        text = emit_expression(IRUnsupported("X", "ends with */ here"), ctx).text
        self.assertNotIn("*/ here", text)

    def test_absent_expression_becomes_undefined(self) -> None:
        self.assertEqual(emit_expression(None, EmitContext()).text, "undefined")


class ParameterEmitTests(unittest.TestCase):
    def params(self, signature: str) -> tuple[str, EmitContext]:
        convert_ctx = ConversionContext(path="m.py")
        tree = ast.parse(f"def f({signature}): pass")
        params = convert_params(tree.body[0].args, convert_ctx)
        ctx = EmitContext()
        return emit_params(params, ctx), ctx

    def test_typed_parameters(self) -> None:
        rendered, _ = self.params("a: int, b: str")
        self.assertEqual(rendered, "a: number, b: string")

    def test_untyped_parameter_is_unknown(self) -> None:
        rendered, _ = self.params("a")
        self.assertEqual(rendered, "a: unknown")

    def test_default_values(self) -> None:
        rendered, _ = self.params("a: int = 1")
        self.assertEqual(rendered, "a: number = 1")

    def test_var_positional_becomes_a_rest_parameter(self) -> None:
        rendered, _ = self.params("*args")
        self.assertEqual(rendered, "...args: unknown[]")

    def test_var_keyword_becomes_an_options_object_and_warns(self) -> None:
        rendered, ctx = self.params("**kwargs")
        self.assertEqual(rendered, "kwargs: Record<string, unknown> = {}")
        self.assertIn("CW0608", [item.code for item in ctx.diagnostics])

    def test_optional_parameter_type(self) -> None:
        rendered, _ = self.params("a: int | None = None")
        self.assertEqual(rendered, "a: number | null = null")

    def test_empty_parameter_list(self) -> None:
        rendered, _ = self.params("")
        self.assertEqual(rendered, "")


if __name__ == "__main__":
    unittest.main()
