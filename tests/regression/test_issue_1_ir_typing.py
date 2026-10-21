"""Regression test for issue #1.

CI was red because mypy reported 91 errors, and the errors had reached `main`
because `scripts/sanity-check` reported the type gate as *skipped* when mypy was
not installed.

Two things are guarded here:

* the IR slot aliases exist and include `IRUnsupported`, so the annotations
  agree with what `ir/schema.py` validates;
* `rewrite()` is type-preserving, so a pass cannot widen a typed field.

The 91 errors themselves are guarded by running mypy, which is the `mypy` gate
in the sanity suite rather than a unit test — a test that shelled out to mypy
would duplicate the gate and be slow. What this file adds is the structural
reasons those errors existed, so they cannot come back silently.

https://github.com/parth-garg01/Codeweaver/issues/1
"""

from __future__ import annotations

import re
import typing
import unittest
from pathlib import Path

from codeweaver.ir import ExpressionSlot, StatementSlot
from codeweaver.ir.base import IRUnsupported
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule
from codeweaver.ir.expressions import IRBinary, IRExpression, IRLiteral, IRName
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.statements import IRBlock, IRReturn, IRStatement
from codeweaver.transforms.rewrite import rewrite

ROOT = Path(__file__).resolve().parents[2]


class SlotAliasTests(unittest.TestCase):
    def test_the_expression_slot_admits_a_gap_node(self) -> None:
        members = typing.get_args(ExpressionSlot)
        self.assertIn(IRExpression, members)
        self.assertIn(IRUnsupported, members)

    def test_the_statement_slot_admits_a_gap_node(self) -> None:
        members = typing.get_args(StatementSlot)
        self.assertIn(IRStatement, members)
        self.assertIn(IRUnsupported, members)

    def test_the_validator_accepts_what_the_aliases_allow(self) -> None:
        # The annotations and ir/schema.py must agree: a gap node in a
        # statement slot is valid IR, not an error.
        from codeweaver.ir.schema import validate_module

        module = IRModule(
            "m",
            "m.py",
            functions=[IRFunction("f", body=IRBlock([IRUnsupported("With", "context managers")]))],
        )
        bag = validate_module(module)
        self.assertFalse(bag.has_errors)
        self.assertIn("CW0300", [item.code for item in bag])

    def test_a_gap_node_survives_in_an_expression_slot(self) -> None:
        node = IRBinary(BinaryOp.ADD, IRLiteral(1), IRUnsupported("Yield", "generators"))
        from codeweaver.ir.base import node_from_dict

        self.assertEqual(node_from_dict(node.to_dict()), node)


class RewriteTypePreservationTests(unittest.TestCase):
    def test_rewriting_a_block_returns_a_block(self) -> None:
        block = IRBlock([IRReturn(IRName("a"))])
        self.assertIsInstance(rewrite(block, lambda node: None), IRBlock)

    def test_rewriting_a_module_returns_a_module(self) -> None:
        module = IRModule("m", "m.py", classes=[IRClass("C")])
        self.assertIsInstance(rewrite(module, lambda node: None), IRModule)

    def test_rewriting_a_function_returns_a_function(self) -> None:
        function = IRFunction("f", body=IRBlock([IRReturn()]))
        self.assertIsInstance(rewrite(function, lambda node: None), IRFunction)

    def test_a_replacement_is_still_applied(self) -> None:
        block = IRBlock([IRReturn(IRName("a"))])
        rewritten = rewrite(
            block,
            lambda node: IRName("b") if isinstance(node, IRName) else None,
        )
        self.assertEqual(rewritten.statements[0].value.name, "b")

    def test_rewrite_is_annotated_as_type_preserving(self) -> None:
        # The signature is the point: `rewrite(block) -> IRBlock`, not IRNode.
        hints = typing.get_type_hints(rewrite)
        self.assertIs(hints["node"], hints["return"])


class HandlerTableTests(unittest.TestCase):
    """Dispatch tables must be tables of callables, not of `object`."""

    MODULES = (
        "src/codeweaver/frontends/python/expressions.py",
        "src/codeweaver/frontends/python/statements.py",
        "src/codeweaver/backends/typescript/expressions.py",
        "src/codeweaver/backends/typescript/statements.py",
    )

    def test_no_handler_table_is_typed_as_object(self) -> None:
        for relative in self.MODULES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("], object] = {", text, relative)

    def test_every_handler_table_is_typed_as_a_callable(self) -> None:
        for relative in self.MODULES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertRegex(text, r"_HANDLERS: dict\[[^\]]+\], _Handler\] = \{", relative)


class SanityGateTests(unittest.TestCase):
    """The type gate must not be able to go quiet in CI again."""

    def setUp(self) -> None:
        self.script = (ROOT / "scripts" / "sanity-check").read_text(encoding="utf-8")

    def test_a_missing_mypy_fails_in_ci(self) -> None:
        self.assertIn('elif [ -n "${CI:-}" ]; then', self.script)
        self.assertIn("required in CI but not installed", self.script)

    def test_a_missing_mypy_still_only_skips_locally(self) -> None:
        self.assertIn("mypy not installed (pip install -e '.[dev]')", self.script)

    def test_the_ci_workflow_installs_the_dev_extra(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"pip install -e '\.\[dev\]'")

    def test_mypy_is_declared_in_the_dev_extra(self) -> None:
        import tomllib

        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dev = data["project"]["optional-dependencies"]["dev"]
        self.assertTrue(any(re.match(r"^mypy", item) for item in dev), dev)


if __name__ == "__main__":
    unittest.main()
