from __future__ import annotations

import unittest

from codeweaver.backends.typescript.module import generate_module
from codeweaver.frontends.python import parse_source
from codeweaver.transforms import default_passes, default_pipeline

SOURCE = (
    "class Account:\n"
    "    def __init__(self, owner: str, items: list[int]) -> None:\n"
    "        self.owner = owner\n"
    "        self.items = items\n"
    "\n"
    "    def total(self) -> int:\n"
    "        running = 0\n"
    "        for i in range(len(self.items)):\n"
    "            running += i\n"
    "        return running\n"
)


def module_for(source: str = SOURCE):
    result = parse_source(source, "pkg/account.py")
    assert result.module is not None
    return result.module


class PipelineOrderTests(unittest.TestCase):
    def test_pass_order_is_fixed(self) -> None:
        self.assertEqual(
            [item.name for item in default_passes()],
            ["infer-instance-fields", "lower-range-loops", "propagate-declared-types"],
        )

    def test_pipeline_carries_the_target(self) -> None:
        self.assertEqual(default_pipeline("typescript").target, "typescript")


class PipelineBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = default_pipeline("typescript").run(module_for())

    def test_every_pass_applies_to_this_module(self) -> None:
        self.assertEqual(
            self.result.applied_passes,
            ["infer-instance-fields", "lower-range-loops", "propagate-declared-types"],
        )

    def test_inferred_fields_get_their_declared_types(self) -> None:
        fields = {item.name: str(item.field_type) for item in self.result.module.classes[0].fields}
        self.assertEqual(fields, {"owner": "str", "items": "list[int]"})

    def test_pipeline_is_idempotent(self) -> None:
        once = self.result.module
        twice = default_pipeline("typescript").run(once).module
        self.assertEqual(once, twice)

    def test_generated_output_uses_every_pass(self) -> None:
        contents = generate_module(self.result.module).contents
        self.assertIn("owner: string;", contents)
        self.assertIn("items: number[];", contents)
        self.assertIn("for (let i = 0; i < this.items.length; i += 1) {", contents)

    def test_generated_output_has_no_gap_markers(self) -> None:
        generated = generate_module(self.result.module)
        self.assertEqual(generated.unsupported_count, 0)

    def test_pipeline_reports_skipped_passes_for_a_trivial_module(self) -> None:
        result = default_pipeline("typescript").run(module_for("x = 1\n"))
        self.assertEqual(
            sorted(result.skipped_passes), ["infer-instance-fields", "lower-range-loops"]
        )

    def test_pipeline_output_is_valid_ir(self) -> None:
        from codeweaver.ir.schema import validate_module

        self.assertFalse(validate_module(self.result.module).has_errors)


if __name__ == "__main__":
    unittest.main()
