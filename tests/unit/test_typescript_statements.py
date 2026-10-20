from __future__ import annotations

import ast
import unittest

from codeweaver.backends.typescript.context import UNSUPPORTED_MARKER, EmitContext
from codeweaver.backends.typescript.statements import emit_statement
from codeweaver.backends.typescript.writer import CodeWriter
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.statements import convert_statements
from codeweaver.ir.expressions import IRLiteral, IRName
from codeweaver.ir.statements import IRBlock, IRForRange, IRNoOp


class StatementEmitTestCase(unittest.TestCase):
    def emit(self, python_source: str) -> str:
        convert_ctx = ConversionContext(path="m.py", source_lines=python_source.splitlines())
        statements = convert_statements(ast.parse(python_source).body, convert_ctx)
        self.ctx = EmitContext(source_path="m.py")
        writer = CodeWriter()
        for statement in statements:
            emit_statement(statement, writer, self.ctx)
        return writer.render()

    def codes(self) -> list[str]:
        return [item.code for item in self.ctx.diagnostics]


class BindingEmitTests(StatementEmitTestCase):
    def test_first_binding_becomes_let(self) -> None:
        self.assertEqual(self.emit("x = 1"), "let x = 1;\n")

    def test_rebinding_is_a_plain_assignment(self) -> None:
        self.assertEqual(self.emit("x = 1\nx = 2"), "let x = 1;\nx = 2;\n")

    def test_annotated_binding_carries_its_type(self) -> None:
        self.assertEqual(self.emit("total: int = 0"), "let total: number = 0;\n")

    def test_annotation_without_a_value(self) -> None:
        self.assertEqual(self.emit("total: int"), "let total: number;\n")

    def test_attribute_and_index_assignment(self) -> None:
        self.assertEqual(self.emit("obj.x = 1"), "obj.x = 1;\n")
        self.assertEqual(self.emit("xs[0] = 1"), "xs[0] = 1;\n")

    def test_augmented_assignment(self) -> None:
        self.assertEqual(self.emit("total += 1"), "total += 1;\n")
        self.assertEqual(self.emit("total **= 2"), "total **= 2;\n")

    def test_augmented_floor_division_is_expanded(self) -> None:
        self.assertEqual(self.emit("n //= 2"), "n = Math.floor(n / 2);\n")

    def test_uninitialised_constant_is_demoted_and_reported(self) -> None:
        from codeweaver.ir.statements import IRVarDecl

        ctx = EmitContext()
        writer = CodeWriter()
        emit_statement(IRVarDecl("X", is_const=True), writer, ctx)
        self.assertEqual(writer.render(), "let X;\n")
        self.assertIn("CW0621", [item.code for item in ctx.diagnostics])


class ControlFlowEmitTests(StatementEmitTestCase):
    def test_if(self) -> None:
        self.assertEqual(self.emit("if ok:\n    f()"), "if (ok) {\n  f();\n}\n")

    def test_if_else(self) -> None:
        self.assertEqual(
            self.emit("if ok:\n    f()\nelse:\n    g()"),
            "if (ok) {\n  f();\n} else {\n  g();\n}\n",
        )

    def test_elif_becomes_else_if_without_extra_nesting(self) -> None:
        rendered = self.emit("if a:\n    f()\nelif b:\n    g()\nelse:\n    h()")
        self.assertEqual(
            rendered,
            "if (a) {\n  f();\n} else if (b) {\n  g();\n} else {\n  h();\n}\n",
        )

    def test_long_elif_chain_stays_flat(self) -> None:
        rendered = self.emit("if a:\n    f()\nelif b:\n    g()\nelif c:\n    h()")
        self.assertEqual(rendered.count("else if"), 2)
        self.assertNotIn("    else", rendered)

    def test_while(self) -> None:
        self.assertEqual(self.emit("while ok:\n    f()"), "while (ok) {\n  f();\n}\n")

    def test_while_else_is_reported_not_dropped_silently(self) -> None:
        self.emit("while ok:\n    f()\nelse:\n    g()")
        self.assertIn("CW0623", self.codes())

    def test_for_each(self) -> None:
        self.assertEqual(
            self.emit("for item in items:\n    f(item)"),
            "for (const item of items) {\n  f(item);\n}\n",
        )

    def test_loop_control(self) -> None:
        rendered = self.emit("while ok:\n    break\n")
        self.assertIn("break;", rendered)
        self.assertIn("continue;", self.emit("while ok:\n    continue\n"))

    def test_pass_becomes_a_comment(self) -> None:
        self.assertEqual(self.emit("pass"), "// pass\n")

    def test_return(self) -> None:
        self.assertEqual(self.emit("return 1"), "return 1;\n")
        self.assertEqual(self.emit("return"), "return;\n")


class RangeLoopEmitTests(unittest.TestCase):
    def render(self, node) -> str:
        writer = CodeWriter()
        emit_statement(node, writer, EmitContext())
        return writer.render()

    def test_counted_loop_with_unit_step(self) -> None:
        node = IRForRange("i", IRLiteral(0), IRName("n"), None, IRBlock([IRNoOp()]))
        self.assertEqual(
            self.render(node),
            "for (let i = 0; i < n; i += 1) {\n  // pass\n}\n",
        )

    def test_counted_loop_with_a_variable_step_handles_direction(self) -> None:
        node = IRForRange("i", IRLiteral(0), IRName("n"), IRName("s"), IRBlock([IRNoOp()]))
        rendered = self.render(node)
        self.assertIn("(s > 0 ? i < n : i > n)", rendered)
        self.assertIn("i += s", rendered)


class ExceptionEmitTests(StatementEmitTestCase):
    def test_single_typed_handler(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept ValueError as exc:\n    g(exc)")
        self.assertIn("try {", rendered)
        self.assertIn("} catch (error) {", rendered)
        self.assertIn("if (error instanceof ValueError) {", rendered)
        self.assertIn("const exc = error;", rendered)
        self.assertIn("throw error;", rendered)

    def test_several_handlers_become_an_instanceof_chain(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept A:\n    g()\nexcept B:\n    h()")
        self.assertIn("if (error instanceof A) {", rendered)
        self.assertIn("} else if (error instanceof B) {", rendered)

    def test_handler_with_a_tuple_of_types(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept (A, B):\n    g()")
        self.assertIn("error instanceof A || error instanceof B", rendered)

    def test_bare_except_catches_everything_without_rethrowing(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept:\n    g()")
        self.assertNotIn("throw error;", rendered)
        self.assertIn("CW0627", self.codes())

    def test_typed_handler_then_bare_except(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept A:\n    g()\nexcept:\n    h()")
        self.assertIn("} else {", rendered)
        self.assertNotIn("throw error;", rendered)

    def test_finally(self) -> None:
        rendered = self.emit("try:\n    f()\nfinally:\n    g()")
        self.assertIn("} finally {", rendered)

    def test_try_else_is_inlined_and_reported(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept A:\n    g()\nelse:\n    h()")
        self.assertIn("// try/else body", rendered)
        self.assertIn("CW0626", self.codes())

    def test_raise(self) -> None:
        self.assertEqual(self.emit("raise ValueError('x')"), 'throw ValueError("x");\n')

    def test_raise_from_attaches_a_cause(self) -> None:
        rendered = self.emit("raise A() from b")
        self.assertIn("Object.assign(A(), { cause: b })", rendered)
        self.assertIn("CW0625", self.codes())

    def test_bare_raise_is_reported(self) -> None:
        self.assertEqual(self.emit("raise"), "throw error;\n")
        self.assertIn("CW0624", self.codes())

    def test_assert_becomes_a_guarded_throw(self) -> None:
        rendered = self.emit("assert ok, 'bad'")
        self.assertEqual(rendered, 'if (!ok) {\n  throw new Error("bad");\n}\n')

    def test_assert_without_a_message_has_a_default(self) -> None:
        self.assertIn("assertion failed", self.emit("assert ok"))

    def test_assert_of_a_comparison_is_parenthesised(self) -> None:
        self.assertIn("if (!(a === b))", self.emit("assert a == b"))


class DeleteEmitTests(StatementEmitTestCase):
    def test_deleting_a_property_uses_delete(self) -> None:
        self.assertEqual(self.emit("del obj.x"), "delete obj.x;\n")

    def test_deleting_an_index_uses_delete(self) -> None:
        self.assertEqual(self.emit("del xs[0]"), "delete xs[0];\n")

    def test_deleting_a_binding_is_reported(self) -> None:
        rendered = self.emit("del x")
        self.assertIn("// unsupported: del x", rendered)
        self.assertIn("CW0628", self.codes())


class CommentAndGapEmitTests(unittest.TestCase):
    def test_comment_becomes_a_line_comment(self) -> None:
        from codeweaver.ir.statements import IRComment

        writer = CodeWriter()
        emit_statement(IRComment("note\nmore"), writer, EmitContext())
        self.assertEqual(writer.render(), "// note\n// more\n")

    def test_unsupported_statement_becomes_a_marker_line(self) -> None:
        from codeweaver.ir.base import IRUnsupported

        ctx = EmitContext()
        writer = CodeWriter()
        emit_statement(IRUnsupported("With", "context managers"), writer, ctx)
        self.assertIn(UNSUPPORTED_MARKER, writer.render())
        self.assertEqual(len(ctx.gaps), 1)


class MappingTests(StatementEmitTestCase):
    def test_output_lines_map_back_to_source_lines(self) -> None:
        source = "x = 1\nif x:\n    y = 2\n"
        convert_ctx = ConversionContext(path="m.py", source_lines=source.splitlines())
        statements = convert_statements(ast.parse(source).body, convert_ctx)
        ctx = EmitContext(source_path="m.py")
        writer = CodeWriter()
        for statement in statements:
            emit_statement(statement, writer, ctx)
        mapped = {entry.output_line: entry.source_line for entry in writer.mappings}
        self.assertEqual(mapped[1], 1)
        self.assertEqual(mapped[2], 2)
        self.assertEqual(mapped[3], 3)


if __name__ == "__main__":
    unittest.main()
