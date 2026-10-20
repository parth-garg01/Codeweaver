from __future__ import annotations

import unittest
from dataclasses import dataclass, replace

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.declarations import IRFunction, IRModule
from codeweaver.ir.expressions import IRBinary, IRLiteral, IRName
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.statements import IRBlock, IRIf, IRReturn
from codeweaver.transforms.base import PassPipeline
from codeweaver.transforms.rewrite import count_nodes, rewrite


class RewriteTests(unittest.TestCase):
    def test_identity_rewriter_returns_the_same_object(self) -> None:
        tree = IRBinary(BinaryOp.ADD, IRName("a"), IRName("b"))
        self.assertIs(rewrite(tree, lambda node: None), tree)

    def test_replaces_a_matching_leaf(self) -> None:
        tree = IRBinary(BinaryOp.ADD, IRName("a"), IRName("b"))

        def rename(node):
            if isinstance(node, IRName) and node.name == "a":
                return IRName("renamed")
            return None

        rewritten = rewrite(tree, rename)
        self.assertEqual(rewritten.left.name, "renamed")
        self.assertEqual(rewritten.right.name, "b")

    def test_original_tree_is_not_mutated(self) -> None:
        tree = IRBinary(BinaryOp.ADD, IRName("a"), IRName("b"))
        rewrite(tree, lambda node: IRName("x") if isinstance(node, IRName) else None)
        self.assertEqual(tree.left.name, "a")

    def test_rewrites_inside_lists(self) -> None:
        block = IRBlock([IRReturn(IRName("a")), IRReturn(IRName("b"))])
        rewritten = rewrite(
            block,
            lambda node: IRName("z") if isinstance(node, IRName) else None,
        )
        self.assertEqual([item.value.name for item in rewritten.statements], ["z", "z"])

    def test_rewrites_deeply_nested_nodes(self) -> None:
        tree = IRIf(IRName("c"), IRBlock([IRReturn(IRName("deep"))]))
        rewritten = rewrite(
            tree,
            lambda node: (
                IRName("found") if isinstance(node, IRName) and node.name == "deep" else None
            ),
        )
        self.assertEqual(rewritten.then_body.statements[0].value.name, "found")

    def test_rewriter_sees_children_first(self) -> None:
        order: list[str] = []

        def record(node):
            order.append(node.kind)
            return

        rewrite(IRBinary(BinaryOp.ADD, IRName("a"), IRLiteral(1)), record)
        self.assertEqual(order, ["name", "literal", "binary"])

    def test_replacement_is_not_revisited(self) -> None:
        calls = {"count": 0}

        def grow(node):
            if isinstance(node, IRName):
                calls["count"] += 1
                return IRName(f"{node.name}_")
            return None

        rewrite(IRName("a"), grow)
        self.assertEqual(calls["count"], 1)

    def test_parent_sees_rewritten_children(self) -> None:
        def fold(node):
            if (
                isinstance(node, IRBinary)
                and isinstance(node.left, IRLiteral)
                and isinstance(node.right, IRLiteral)
            ):
                return IRLiteral(node.left.value + node.right.value)
            if isinstance(node, IRName):
                return IRLiteral(1)
            return None

        folded = rewrite(IRBinary(BinaryOp.ADD, IRName("a"), IRName("b")), fold)
        self.assertEqual(folded.value, 2)

    def test_count_nodes(self) -> None:
        tree = IRBlock([IRReturn(IRName("a")), IRReturn(IRName("b"))])
        self.assertEqual(count_nodes(tree, "name"), 2)
        self.assertEqual(count_nodes(tree, "literal"), 0)


@dataclass
class RenamePass:
    name: str = "rename-main"
    description: str = "Rename a function called main"
    targets: tuple[str, ...] = ()

    def applies_to(self, module: IRModule) -> bool:
        return module.function_named("main") is not None

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        bag.info("TEST01", "renaming main", stage="transform")
        functions = [
            replace(function, name="run") if function.name == "main" else function
            for function in module.functions
        ]
        return replace(module, functions=functions)


@dataclass
class TargetOnlyPass:
    name: str = "typescript-only"
    description: str = "Only relevant to TypeScript"
    targets: tuple[str, ...] = ("typescript",)

    def applies_to(self, module: IRModule) -> bool:  # noqa: ARG002
        return True

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:  # noqa: ARG002
        return module


class PipelineTests(unittest.TestCase):
    def module(self, *, with_main: bool = True) -> IRModule:
        functions = [IRFunction("main")] if with_main else [IRFunction("other")]
        return IRModule("m", "m.py", functions=functions)

    def test_applicable_pass_runs_and_reports(self) -> None:
        result = PassPipeline([RenamePass()]).run(self.module())
        self.assertEqual(result.applied_passes, ["rename-main"])
        self.assertIsNotNone(result.module.function_named("run"))
        self.assertEqual([item.code for item in result.diagnostics], ["TEST01"])

    def test_inapplicable_pass_is_skipped_with_a_reason(self) -> None:
        result = PassPipeline([RenamePass()]).run(self.module(with_main=False))
        self.assertEqual(result.skipped_passes, ["rename-main"])
        self.assertEqual(result.outcomes[0].skipped_reason, "nothing to rewrite")

    def test_pass_for_another_target_is_skipped(self) -> None:
        pipeline = PassPipeline([TargetOnlyPass()], target="go")
        result = pipeline.run(self.module())
        self.assertEqual(result.skipped_passes, ["typescript-only"])
        self.assertIn("targets typescript", result.outcomes[0].skipped_reason)

    def test_pass_runs_when_the_target_matches(self) -> None:
        pipeline = PassPipeline([TargetOnlyPass()], target="typescript")
        self.assertEqual(pipeline.run(self.module()).applied_passes, ["typescript-only"])

    def test_pass_runs_when_no_target_is_set(self) -> None:
        self.assertEqual(
            PassPipeline([TargetOnlyPass()]).run(self.module()).applied_passes,
            ["typescript-only"],
        )

    def test_changed_flag_reflects_whether_the_module_was_replaced(self) -> None:
        result = PassPipeline([RenamePass()]).run(self.module())
        self.assertTrue(result.outcomes[0].changed)
        result = PassPipeline([TargetOnlyPass()]).run(self.module())
        self.assertFalse(result.outcomes[0].changed)

    def test_pipeline_is_idempotent(self) -> None:
        pipeline = PassPipeline([RenamePass()])
        once = pipeline.run(self.module()).module
        twice = pipeline.run(once).module
        self.assertEqual(once, twice)

    def test_input_module_is_not_mutated(self) -> None:
        module = self.module()
        PassPipeline([RenamePass()]).run(module)
        self.assertIsNotNone(module.function_named("main"))

    def test_add_returns_the_pipeline_for_chaining(self) -> None:
        pipeline = PassPipeline().add(RenamePass()).add(TargetOnlyPass())
        self.assertEqual(pipeline.names(), ["rename-main", "typescript-only"])

    def test_empty_pipeline_returns_the_module_unchanged(self) -> None:
        module = self.module()
        self.assertIs(PassPipeline().run(module).module, module)

    def test_to_dict_is_json_friendly(self) -> None:
        import json

        json.dumps(PassPipeline([RenamePass()]).run(self.module()).to_dict())


if __name__ == "__main__":
    unittest.main()
