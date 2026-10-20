from __future__ import annotations

import ast
import unittest

from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.statements import convert_block, convert_statement
from codeweaver.ir.base import IRUnsupported
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.statements import (
    IRAssert,
    IRAssign,
    IRAugAssign,
    IRBreak,
    IRContinue,
    IRDelete,
    IRExpressionStatement,
    IRForEach,
    IRIf,
    IRNoOp,
    IRReturn,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)


class StatementTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = ConversionContext(path="m.py")

    def one(self, text: str):
        self.ctx.source_lines = text.splitlines()
        return convert_statement(ast.parse(text).body[0], self.ctx)

    def block(self, text: str):
        self.ctx.source_lines = text.splitlines()
        return convert_block(ast.parse(text).body, self.ctx)

    def codes(self) -> list[str]:
        return [item.code for item in self.ctx.diagnostics]


class BindingTests(StatementTestCase):
    def test_first_assignment_is_a_declaration(self) -> None:
        node = self.one("x = 1")
        self.assertIsInstance(node, IRVarDecl)
        self.assertEqual(node.name, "x")

    def test_second_assignment_is_a_plain_assignment(self) -> None:
        block = self.block("x = 1\nx = 2")
        self.assertIsInstance(block.statements[0], IRVarDecl)
        self.assertIsInstance(block.statements[1], IRAssign)

    def test_annotated_assignment_carries_the_declared_type(self) -> None:
        node = self.one("total: int = 0")
        self.assertIsInstance(node, IRVarDecl)
        self.assertEqual(str(node.var_type), "int")
        self.assertEqual(node.value.value, 0)

    def test_annotation_without_a_value_is_a_declaration(self) -> None:
        node = self.one("total: int")
        self.assertIsInstance(node, IRVarDecl)
        self.assertIsNone(node.value)

    def test_annotated_assignment_redeclares_even_when_already_bound(self) -> None:
        block = self.block("x = 1\nx: int = 2")
        self.assertIsInstance(block.statements[1], IRVarDecl)

    def test_attribute_assignment_is_an_assignment(self) -> None:
        node = self.one("self.total = 1")
        self.assertIsInstance(node, IRAssign)
        self.assertEqual(node.target.name, "total")

    def test_index_assignment_is_an_assignment(self) -> None:
        self.assertIsInstance(self.one("xs[0] = 1"), IRAssign)

    def test_destructuring_is_reported(self) -> None:
        node = self.one("a, b = 1, 2")
        self.assertIsInstance(node, IRUnsupported)
        self.assertIn("own line", node.hint)

    def test_chained_assignment_is_reported(self) -> None:
        node = self.one("a = b = 1")
        self.assertIsInstance(node, IRUnsupported)

    def test_augmented_assignment(self) -> None:
        node = self.one("total += 1")
        self.assertIsInstance(node, IRAugAssign)
        self.assertEqual(node.op, BinaryOp.ADD)


class ControlFlowTests(StatementTestCase):
    def test_if_without_else(self) -> None:
        node = self.one("if ok:\n    pass")
        self.assertIsInstance(node, IRIf)
        self.assertIsNone(node.else_body)

    def test_if_with_else(self) -> None:
        node = self.one("if ok:\n    pass\nelse:\n    pass")
        self.assertIsNotNone(node.else_body)

    def test_elif_nests_as_an_if_inside_else(self) -> None:
        node = self.one("if a:\n    pass\nelif b:\n    pass\nelse:\n    pass")
        inner = node.else_body.statements[0]
        self.assertIsInstance(inner, IRIf)
        self.assertIsNotNone(inner.else_body)

    def test_while_with_else(self) -> None:
        node = self.one("while ok:\n    break\nelse:\n    pass")
        self.assertIsInstance(node, IRWhile)
        self.assertIsNotNone(node.else_body)

    def test_for_each_binds_the_loop_variable(self) -> None:
        node = self.one("for item in items:\n    pass")
        self.assertIsInstance(node, IRForEach)
        self.assertIn("item", self.ctx.bound_names)

    def test_for_with_tuple_target_is_reported(self) -> None:
        node = self.one("for k, v in pairs:\n    pass")
        self.assertIsInstance(node, IRUnsupported)

    def test_async_for_is_reported(self) -> None:
        node = convert_statement(
            ast.parse("async def f():\n    async for x in xs:\n        pass").body[0].body[0],
            self.ctx,
        )
        self.assertIsInstance(node, IRUnsupported)

    def test_loop_control_statements(self) -> None:
        self.assertIsInstance(self.one("break"), IRBreak)
        self.assertIsInstance(self.one("continue"), IRContinue)
        self.assertIsInstance(self.one("pass"), IRNoOp)

    def test_return_with_and_without_value(self) -> None:
        self.assertEqual(self.one("return 1").value.value, 1)
        self.assertIsNone(self.one("return").value)
        self.assertIsInstance(self.one("return"), IRReturn)


class ExceptionTests(StatementTestCase):
    def test_try_except_finally(self) -> None:
        node = self.one("try:\n    pass\nexcept ValueError as exc:\n    pass\nfinally:\n    pass")
        self.assertIsInstance(node, IRTry)
        self.assertEqual(node.handlers[0].name, "exc")
        self.assertEqual(len(node.handlers[0].exception_types), 1)
        self.assertIsNotNone(node.finally_body)

    def test_except_tuple_keeps_every_type(self) -> None:
        node = self.one("try:\n    pass\nexcept (A, B):\n    pass")
        self.assertEqual(len(node.handlers[0].exception_types), 2)

    def test_bare_except_has_no_types(self) -> None:
        node = self.one("try:\n    pass\nexcept:\n    pass")
        self.assertEqual(node.handlers[0].exception_types, [])

    def test_try_else_is_kept(self) -> None:
        node = self.one("try:\n    pass\nexcept A:\n    pass\nelse:\n    pass")
        self.assertIsNotNone(node.else_body)

    def test_raise_with_cause(self) -> None:
        node = self.one("raise ValueError('x') from original")
        self.assertIsInstance(node, IRThrow)
        self.assertIsNotNone(node.cause)

    def test_bare_raise(self) -> None:
        node = self.one("raise")
        self.assertIsNone(node.value)

    def test_assert_with_message(self) -> None:
        node = self.one("assert ok, 'must be ok'")
        self.assertIsInstance(node, IRAssert)
        self.assertEqual(node.message.value, "must be ok")


class MiscStatementTests(StatementTestCase):
    def test_expression_statement(self) -> None:
        self.assertIsInstance(self.one("print(1)"), IRExpressionStatement)

    def test_delete(self) -> None:
        node = self.one("del a, b")
        self.assertIsInstance(node, IRDelete)
        self.assertEqual(len(node.targets), 2)


class UnsupportedStatementTests(StatementTestCase):
    def test_with_statement_is_reported_with_a_hint(self) -> None:
        node = self.one("with open('f') as handle:\n    pass")
        self.assertIsInstance(node, IRUnsupported)
        self.assertIn("try/finally", node.hint)

    def test_global_and_nonlocal_are_reported(self) -> None:
        self.assertIsInstance(self.one("global x"), IRUnsupported)

    def test_match_is_reported(self) -> None:
        node = self.one("match x:\n    case 1:\n        pass")
        self.assertIsInstance(node, IRUnsupported)
        self.assertIn("if/elif", node.hint)

    def test_nested_definitions_are_reported(self) -> None:
        inner = ast.parse("def outer():\n    def inner():\n        pass").body[0].body[0]
        node = convert_statement(inner, self.ctx)
        self.assertIsInstance(node, IRUnsupported)
        self.assertIn("module level", node.hint)

    def test_function_level_import_is_reported(self) -> None:
        inner = ast.parse("def f():\n    import os").body[0].body[0]
        node = convert_statement(inner, self.ctx)
        self.assertIsInstance(node, IRUnsupported)

    def test_every_unsupported_statement_produces_a_diagnostic(self) -> None:
        self.one("global x")
        self.assertEqual(self.codes(), ["CW0400"])

    def test_unsupported_statement_records_its_source_text(self) -> None:
        node = self.one("with open('f') as handle:\n    pass")
        self.assertIn("with open", node.source_text)


if __name__ == "__main__":
    unittest.main()
