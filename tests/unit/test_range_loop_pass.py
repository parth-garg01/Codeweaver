from __future__ import annotations

import unittest

from codeweaver.backends.typescript.module import generate_module
from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.ir.base import walk
from codeweaver.ir.statements import IRForEach, IRForRange
from codeweaver.transforms.range_loops import RangeLoopPass, lower_range_loops


def module_for(source: str):
    result = parse_source(source, "m.py")
    assert result.module is not None
    return result.module


def lowered(source: str):
    return lower_range_loops(module_for(source))


def loops(module) -> list[str]:
    return [node.kind for node in walk(module) if isinstance(node, IRForEach | IRForRange)]


class LoweringTests(unittest.TestCase):
    def test_single_argument_range_starts_at_zero(self) -> None:
        module = lowered("def f(n):\n    for i in range(n):\n        g(i)\n")
        node = next(item for item in walk(module) if isinstance(item, IRForRange))
        self.assertEqual(node.variable, "i")
        self.assertEqual(node.start.value, 0)
        self.assertEqual(node.stop.name, "n")
        self.assertIsNone(node.step)

    def test_two_argument_range(self) -> None:
        module = lowered("def f():\n    for i in range(2, 10):\n        g(i)\n")
        node = next(item for item in walk(module) if isinstance(item, IRForRange))
        self.assertEqual(node.start.value, 2)
        self.assertEqual(node.stop.value, 10)

    def test_three_argument_range_keeps_the_step(self) -> None:
        module = lowered("def f():\n    for i in range(0, 10, 2):\n        g(i)\n")
        node = next(item for item in walk(module) if isinstance(item, IRForRange))
        self.assertEqual(node.step.value, 2)

    def test_loop_body_is_preserved(self) -> None:
        module = lowered("def f(n):\n    for i in range(n):\n        total += i\n")
        node = next(item for item in walk(module) if isinstance(item, IRForRange))
        self.assertEqual(len(node.body.statements), 1)

    def test_lowering_is_recorded_in_metadata(self) -> None:
        module = lowered("def f(n):\n    for i in range(n):\n        pass\n")
        node = next(item for item in walk(module) if isinstance(item, IRForRange))
        self.assertEqual(node.meta["loweredFrom"], "range")

    def test_module_level_loop_is_lowered(self) -> None:
        module = lowered("for i in range(3):\n    print(i)\n")
        self.assertEqual(loops(module), ["for_range"])

    def test_loop_inside_a_method_is_lowered(self) -> None:
        module = lowered(
            "class C:\n    def f(self, n):\n        for i in range(n):\n            pass\n"
        )
        self.assertEqual(loops(module), ["for_range"])

    def test_nested_range_loops_are_both_lowered(self) -> None:
        module = lowered(
            "def f(n):\n    for i in range(n):\n        for j in range(n):\n            pass\n"
        )
        self.assertEqual(loops(module), ["for_range", "for_range"])


class NonLoweringTests(unittest.TestCase):
    def test_iteration_over_a_list_is_untouched(self) -> None:
        self.assertEqual(
            loops(lowered("def f(xs):\n    for x in xs:\n        pass\n")), ["for_each"]
        )

    def test_range_with_no_arguments_is_untouched(self) -> None:
        self.assertEqual(
            loops(lowered("def f():\n    for i in range():\n        pass\n")), ["for_each"]
        )

    def test_range_with_four_arguments_is_untouched(self) -> None:
        module = lowered("def f():\n    for i in range(1, 2, 3, 4):\n        pass\n")
        self.assertEqual(loops(module), ["for_each"])

    def test_range_with_keywords_is_untouched(self) -> None:
        module = lowered("def f():\n    for i in range(stop=3):\n        pass\n")
        self.assertEqual(loops(module), ["for_each"])

    def test_range_with_a_spread_is_untouched(self) -> None:
        module = lowered("def f(a):\n    for i in range(*a):\n        pass\n")
        self.assertEqual(loops(module), ["for_each"])

    def test_a_differently_named_call_is_untouched(self) -> None:
        module = lowered("def f(n):\n    for i in irange(n):\n        pass\n")
        self.assertEqual(loops(module), ["for_each"])

    def test_for_else_keeps_its_shape_so_the_else_is_still_reported(self) -> None:
        module = lowered(
            "def f(n):\n    for i in range(n):\n        pass\n    else:\n        g()\n"
        )
        self.assertEqual(loops(module), ["for_each"])

    def test_original_module_is_not_mutated(self) -> None:
        module = module_for("def f(n):\n    for i in range(n):\n        pass\n")
        lower_range_loops(module)
        self.assertEqual(loops(module), ["for_each"])


class PassTests(unittest.TestCase):
    def test_applies_to_a_module_with_a_range_loop(self) -> None:
        pass_ = RangeLoopPass()
        self.assertTrue(pass_.applies_to(module_for("for i in range(3):\n    pass\n")))

    def test_does_not_apply_without_a_range_loop(self) -> None:
        pass_ = RangeLoopPass()
        self.assertFalse(pass_.applies_to(module_for("for x in xs:\n    pass\n")))

    def test_run_reports_how_many_loops_were_lowered(self) -> None:
        bag = DiagnosticBag()
        RangeLoopPass().run(module_for("for i in range(3):\n    pass\n"), bag)
        self.assertEqual([item.code for item in bag], ["CW0700"])

    def test_pass_is_idempotent(self) -> None:
        module = module_for("def f(n):\n    for i in range(n):\n        pass\n")
        once = lower_range_loops(module)
        self.assertEqual(lower_range_loops(once), once)


class GeneratedOutputTests(unittest.TestCase):
    def test_lowered_loop_generates_an_index_loop(self) -> None:
        module = lowered("def f(n):\n    for i in range(n):\n        g(i)\n")
        contents = generate_module(module).contents
        self.assertIn("for (let i = 0; i < n; i += 1) {", contents)

    def test_unlowered_loop_generates_for_of(self) -> None:
        module = module_for("def f(xs):\n    for x in xs:\n        g(x)\n")
        self.assertIn("for (const x of xs) {", generate_module(module).contents)


if __name__ == "__main__":
    unittest.main()
