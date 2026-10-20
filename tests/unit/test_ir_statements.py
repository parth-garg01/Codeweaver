from __future__ import annotations

import unittest

from codeweaver.ir.base import node_from_dict, walk
from codeweaver.ir.expressions import IRAttribute, IRIndex, IRLiteral, IRName
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.statements import (
    IRAssert,
    IRAssign,
    IRAugAssign,
    IRBlock,
    IRBreak,
    IRCatch,
    IRComment,
    IRContinue,
    IRDelete,
    IRExpressionStatement,
    IRForEach,
    IRForRange,
    IRIf,
    IRNoOp,
    IRReturn,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)
from codeweaver.ir.types import t_int


def roundtrip(node):
    return node_from_dict(node.to_dict())


class BlockTests(unittest.TestCase):
    def test_empty_block(self) -> None:
        block = IRBlock()
        self.assertTrue(block.is_empty)
        self.assertEqual(len(block), 0)

    def test_block_reports_length_and_children(self) -> None:
        block = IRBlock([IRNoOp(), IRBreak()])
        self.assertEqual(len(block), 2)
        self.assertFalse(block.is_empty)
        self.assertEqual(len(block.children()), 2)


class StatementRoundTripTests(unittest.TestCase):
    def assert_roundtrips(self, node) -> None:
        self.assertEqual(roundtrip(node), node)

    def test_expression_statement(self) -> None:
        self.assert_roundtrips(IRExpressionStatement(IRLiteral(1)))

    def test_var_decl_with_type_and_const_flag(self) -> None:
        self.assert_roundtrips(IRVarDecl("total", t_int(), IRLiteral(0), is_const=True))

    def test_var_decl_without_initialiser(self) -> None:
        self.assert_roundtrips(IRVarDecl("later"))

    def test_assign_to_name_attribute_and_index(self) -> None:
        self.assert_roundtrips(IRAssign(IRName("x"), IRLiteral(1)))
        self.assert_roundtrips(IRAssign(IRAttribute(IRName("self"), "x"), IRLiteral(1)))
        self.assert_roundtrips(IRAssign(IRIndex(IRName("xs"), IRLiteral(0)), IRLiteral(1)))

    def test_aug_assign(self) -> None:
        self.assert_roundtrips(IRAugAssign(BinaryOp.ADD, IRName("total"), IRLiteral(1)))

    def test_return_with_and_without_value(self) -> None:
        self.assert_roundtrips(IRReturn(IRName("x")))
        self.assert_roundtrips(IRReturn())

    def test_if_with_else(self) -> None:
        node = IRIf(IRName("ok"), IRBlock([IRReturn(IRLiteral(1))]), IRBlock([IRReturn()]))
        self.assert_roundtrips(node)

    def test_if_without_else(self) -> None:
        self.assert_roundtrips(IRIf(IRName("ok"), IRBlock([IRNoOp()])))

    def test_while_with_else_body_is_preserved(self) -> None:
        node = IRWhile(IRName("ok"), IRBlock([IRBreak()]), IRBlock([IRContinue()]))
        restored = roundtrip(node)
        self.assertIsNotNone(restored.else_body)
        self.assertEqual(restored, node)

    def test_for_each(self) -> None:
        node = IRForEach(IRName("item"), IRName("items"), IRBlock([IRNoOp()]))
        self.assert_roundtrips(node)

    def test_for_range(self) -> None:
        node = IRForRange("i", IRLiteral(0), IRLiteral(10), IRLiteral(2), IRBlock([IRNoOp()]))
        self.assert_roundtrips(node)

    def test_loop_control_and_noop(self) -> None:
        self.assert_roundtrips(IRBreak())
        self.assert_roundtrips(IRContinue())
        self.assert_roundtrips(IRNoOp())

    def test_throw_with_cause(self) -> None:
        self.assert_roundtrips(IRThrow(IRName("err"), IRName("original")))

    def test_bare_throw(self) -> None:
        self.assert_roundtrips(IRThrow())

    def test_try_with_handlers_else_and_finally(self) -> None:
        node = IRTry(
            body=IRBlock([IRNoOp()]),
            handlers=[
                IRCatch([IRName("ValueError"), IRName("TypeError")], "exc", IRBlock([IRNoOp()])),
            ],
            else_body=IRBlock([IRNoOp()]),
            finally_body=IRBlock([IRNoOp()]),
        )
        restored = roundtrip(node)
        self.assertEqual(restored, node)
        self.assertEqual(len(restored.handlers[0].exception_types), 2)

    def test_bare_except_has_no_exception_types(self) -> None:
        node = IRTry(body=IRBlock([IRNoOp()]), handlers=[IRCatch(body=IRBlock([IRNoOp()]))])
        self.assert_roundtrips(node)

    def test_delete_and_assert(self) -> None:
        self.assert_roundtrips(IRDelete([IRName("x"), IRName("y")]))
        self.assert_roundtrips(IRAssert(IRName("ok"), IRLiteral("must be ok")))

    def test_comment_marks_generated_text(self) -> None:
        restored = roundtrip(IRComment("unsupported: yield", generated=True))
        self.assertTrue(restored.generated)


class StatementTraversalTests(unittest.TestCase):
    def test_walk_reaches_nested_bodies(self) -> None:
        tree = IRIf(
            IRName("ok"),
            IRBlock([IRReturn(IRLiteral(1))]),
            IRBlock([IRTry(body=IRBlock([IRNoOp()]))]),
        )
        kinds = [node.kind for node in walk(tree)]
        self.assertIn("return", kinds)
        self.assertIn("try", kinds)
        self.assertIn("noop", kinds)

    def test_absent_optional_bodies_are_not_children(self) -> None:
        node = IRIf(IRName("ok"), IRBlock())
        self.assertEqual([child.kind for child in node.children()], ["name", "block"])


if __name__ == "__main__":
    unittest.main()
