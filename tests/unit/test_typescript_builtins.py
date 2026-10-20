from __future__ import annotations

import ast
import unittest

from codeweaver.backends.typescript.builtins import BUILTIN_NAMES, UNMAPPED_BUILTINS
from codeweaver.backends.typescript.context import EmitContext
from codeweaver.backends.typescript.expressions import emit_expression
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import convert_expression
from codeweaver.ir.expressions import IRCall, IRName
from codeweaver.ir.types import t_dict, t_list, t_set, t_str


class BuiltinEmitTestCase(unittest.TestCase):
    def emit(self, python_source: str) -> str:
        convert_ctx = ConversionContext(path="m.py", source_lines=[python_source])
        node = convert_expression(ast.parse(python_source, mode="eval").body, convert_ctx)
        self.ctx = EmitContext(source_path="m.py")
        return emit_expression(node, self.ctx).text

    def codes(self) -> list[str]:
        return sorted({item.code for item in self.ctx.diagnostics})


class ConversionBuiltinTests(BuiltinEmitTestCase):
    def test_print_becomes_console_log(self) -> None:
        self.assertEqual(self.emit("print(1, 2)"), "console.log(1, 2)")

    def test_print_with_no_arguments(self) -> None:
        self.assertEqual(self.emit("print()"), "console.log()")

    def test_str_float_and_bool_conversions(self) -> None:
        self.assertEqual(self.emit("str(x)"), "String(x)")
        self.assertEqual(self.emit("float(x)"), "Number(x)")
        self.assertEqual(self.emit("bool(x)"), "Boolean(x)")

    def test_int_truncates_and_is_reported(self) -> None:
        self.assertEqual(self.emit("int(x)"), "Math.trunc(Number(x))")
        self.assertIn("CW0666", self.codes())

    def test_round_difference_is_reported(self) -> None:
        self.assertEqual(self.emit("round(x)"), "Math.round(x)")
        self.assertIn("CW0666", self.codes())

    def test_abs(self) -> None:
        self.assertEqual(self.emit("abs(x)"), "Math.abs(x)")


class LengthTests(unittest.TestCase):
    def length_of(self, value_type) -> tuple[str, EmitContext]:
        ctx = EmitContext()
        call = IRCall(IRName("len"), [IRName("x", value_type=value_type)])
        return emit_expression(call, ctx).text, ctx

    def test_list_uses_length(self) -> None:
        text, ctx = self.length_of(t_list())
        self.assertEqual(text, "x.length")
        self.assertEqual(list(ctx.diagnostics), [])

    def test_string_uses_length(self) -> None:
        text, _ = self.length_of(t_str())
        self.assertEqual(text, "x.length")

    def test_set_uses_size(self) -> None:
        text, _ = self.length_of(t_set())
        self.assertEqual(text, "x.size")

    def test_dict_counts_its_keys(self) -> None:
        text, _ = self.length_of(t_dict())
        self.assertEqual(text, "Object.keys(x).length")

    def test_unknown_container_is_reported(self) -> None:
        from codeweaver.ir.types import t_unknown

        text, ctx = self.length_of(t_unknown())
        self.assertEqual(text, "x.length")
        self.assertIn("CW0660", [item.code for item in ctx.diagnostics])


class CollectionBuiltinTests(BuiltinEmitTestCase):
    def test_sum_becomes_a_reduce(self) -> None:
        self.assertIn("reduce((acc: number, item: number) => acc + item, 0)", self.emit("sum(xs)"))

    def test_sorted_copies_and_is_reported(self) -> None:
        self.assertEqual(self.emit("sorted(xs)"), "[...xs].sort()")
        self.assertIn("CW0661", self.codes())

    def test_list_and_tuple_use_array_from(self) -> None:
        self.assertEqual(self.emit("list(xs)"), "Array.from(xs)")
        self.assertEqual(self.emit("tuple(xs)"), "Array.from(xs)")

    def test_set_constructor(self) -> None:
        self.assertEqual(self.emit("set(xs)"), "new Set(xs)")

    def test_min_and_max_with_several_arguments(self) -> None:
        self.assertEqual(self.emit("max(1, 2, 3)"), "Math.max(1, 2, 3)")

    def test_min_over_a_sequence_is_spread_and_reported(self) -> None:
        self.assertEqual(self.emit("min(xs)"), "Math.min(...xs)")
        self.assertIn("CW0662", self.codes())


class IsinstanceTests(BuiltinEmitTestCase):
    def test_string_check_uses_typeof(self) -> None:
        self.assertEqual(self.emit("isinstance(x, str)"), 'typeof x === "string"')

    def test_bool_check_uses_typeof(self) -> None:
        self.assertEqual(self.emit("isinstance(x, bool)"), 'typeof x === "boolean"')

    def test_int_check_cannot_distinguish_floats_and_is_reported(self) -> None:
        self.assertEqual(self.emit("isinstance(x, int)"), 'typeof x === "number"')
        self.assertIn("CW0667", self.codes())

    def test_list_check_uses_instanceof_array(self) -> None:
        self.assertEqual(self.emit("isinstance(x, list)"), "x instanceof Array")

    def test_user_class_check_uses_instanceof(self) -> None:
        self.assertEqual(self.emit("isinstance(x, Account)"), "x instanceof Account")


class UnmappedBuiltinTests(BuiltinEmitTestCase):
    def test_enumerate_is_reported_with_a_hint(self) -> None:
        self.assertIn("unmapped builtin: enumerate()", self.emit("enumerate(xs)"))
        self.assertIn("CW0663", self.codes())

    def test_every_unmapped_builtin_has_a_hint(self) -> None:
        for name, hint in UNMAPPED_BUILTINS.items():
            self.assertTrue(hint, name)

    def test_dynamic_access_builtins_are_unmapped(self) -> None:
        for name in ("getattr", "setattr", "hasattr", "eval", "exec"):
            self.assertIn(name, UNMAPPED_BUILTINS)

    def test_builtin_names_covers_both_sets(self) -> None:
        self.assertIn("len", BUILTIN_NAMES)
        self.assertIn("enumerate", BUILTIN_NAMES)


class FallthroughTests(BuiltinEmitTestCase):
    def test_a_shadowing_call_with_the_wrong_arity_falls_back(self) -> None:
        self.assertEqual(self.emit("abs(1, 2)"), "abs(1, 2)")
        self.assertIn("CW0665", self.codes())

    def test_keyword_arguments_fall_back_to_a_plain_call(self) -> None:
        self.assertIn("round(", self.emit("round(x, ndigits=2)"))
        self.assertIn("CW0664", self.codes())

    def test_star_args_fall_back_to_a_plain_call(self) -> None:
        self.assertEqual(self.emit("max(*xs)"), "max(...xs)")

    def test_a_non_builtin_call_is_untouched(self) -> None:
        self.assertEqual(self.emit("helper(1)"), "helper(1)")

    def test_a_method_call_named_like_a_builtin_is_untouched(self) -> None:
        self.assertEqual(self.emit("obj.len(x)"), "obj.len(x)")

    def test_super_is_not_treated_as_an_unmapped_builtin(self) -> None:
        self.assertEqual(self.emit("super().method()"), "super.method()")


if __name__ == "__main__":
    unittest.main()
