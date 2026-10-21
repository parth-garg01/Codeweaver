"""Regression test for issue #3.

A `@property` with a matching `@x.setter` produced two IRFunction values with
the same name, which tripped the duplicate-method rule (`CW0315`). The schema
error was treated as a parse failure, so the entire module was dropped from the
generated project while other modules could still import it.

https://github.com/parth-garg01/Codeweaver/issues/3
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from codeweaver.backends.typescript.module import generate_module
from codeweaver.frontends.python import parse_source
from codeweaver.ir.declarations import ACCESSOR_GETTER, ACCESSOR_SETTER, IRClass, IRFunction
from codeweaver.ir.schema import validate_module
from codeweaver.ir.version import IR_SCHEMA_VERSION, SCHEMA_HISTORY, is_compatible
from codeweaver.pipeline import MigrationOptions, analyse_project, run_migration
from codeweaver.validation.results import CheckOutcome

HAS_TSC = shutil.which("tsc") is not None

PROPERTY_PAIR = (
    "class Box:\n"
    "    def __init__(self, value: int) -> None:\n"
    "        self._value = value\n"
    "\n"
    "    @property\n"
    "    def value(self) -> int:\n"
    "        return self._value\n"
    "\n"
    "    @value.setter\n"
    "    def value(self, new: int) -> None:\n"
    "        self._value = new\n"
    "\n"
    "\n"
    "def use(box: Box) -> int:\n"
    "    box.value = box.value + 1\n"
    "    return box.value\n"
)

GETTER_ONLY = (
    "class Box:\n"
    "    def __init__(self) -> None:\n"
    "        self._value = 0\n"
    "\n"
    "    @property\n"
    "    def value(self) -> int:\n"
    "        return self._value\n"
)


def module_for(source: str, path: str = "box.py"):
    result = parse_source(source, path)
    assert result.module is not None, [d.format() for d in result.diagnostics]
    return result.module


class FrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.klass = module_for(PROPERTY_PAIR).class_named("Box")

    def test_the_getter_is_marked(self) -> None:
        getter = self.klass.method_named("value", ACCESSOR_GETTER)
        self.assertIsNotNone(getter)
        self.assertTrue(getter.is_accessor)

    def test_the_setter_is_marked(self) -> None:
        setter = self.klass.method_named("value", ACCESSOR_SETTER)
        self.assertIsNotNone(setter)
        self.assertEqual(setter.accessor, ACCESSOR_SETTER)

    def test_both_halves_are_kept(self) -> None:
        pairs = self.klass.accessor_pairs()
        self.assertEqual(sorted(pairs["value"]), ["getter", "setter"])

    def test_an_ordinary_method_is_not_an_accessor(self) -> None:
        klass = module_for("class C:\n    def m(self) -> int:\n        return 1\n").class_named("C")
        self.assertFalse(klass.method_named("m").is_accessor)

    def test_a_getter_only_property_is_still_a_getter(self) -> None:
        klass = module_for(GETTER_ONLY).class_named("Box")
        self.assertEqual(klass.accessor_pairs()["value"].keys(), {"getter"})

    def test_the_setter_decorator_no_longer_warns_as_unmapped(self) -> None:
        result = parse_source(PROPERTY_PAIR, "box.py")
        self.assertNotIn("CW0413", [item.code for item in result.diagnostics])


class SchemaTests(unittest.TestCase):
    def test_a_property_pair_is_valid_ir(self) -> None:
        bag = validate_module(module_for(PROPERTY_PAIR))
        self.assertFalse(bag.has_errors, [item.format() for item in bag.errors])

    def test_a_genuinely_duplicated_method_is_still_rejected(self) -> None:
        klass = IRClass(
            "C",
            methods=[IRFunction("m", is_method=True), IRFunction("m", is_method=True)],
        )
        from codeweaver.ir.schema import validate_node

        self.assertIn("CW0315", [item.code for item in validate_node(klass)])

    def test_two_getters_with_one_name_are_still_rejected(self) -> None:
        klass = IRClass(
            "C",
            methods=[
                IRFunction("v", is_method=True, accessor=ACCESSOR_GETTER),
                IRFunction("v", is_method=True, accessor=ACCESSOR_GETTER),
            ],
        )
        from codeweaver.ir.schema import validate_node

        self.assertIn("CW0315", [item.code for item in validate_node(klass)])

    def test_an_accessor_clashing_with_a_plain_method_is_rejected(self) -> None:
        klass = IRClass(
            "C",
            methods=[
                IRFunction("v", is_method=True, accessor=ACCESSOR_GETTER),
                IRFunction("v", is_method=True),
            ],
        )
        from codeweaver.ir.schema import validate_node

        self.assertIn("CW0328", [item.code for item in validate_node(klass)])

    def test_a_setter_without_a_getter_warns_but_is_not_an_error(self) -> None:
        klass = IRClass("C", methods=[IRFunction("v", is_method=True, accessor=ACCESSOR_SETTER)])
        from codeweaver.ir.schema import validate_node

        bag = validate_node(klass)
        self.assertIn("CW0329", [item.code for item in bag])
        self.assertFalse(bag.has_errors)

    def test_the_schema_version_was_bumped_and_documented(self) -> None:
        self.assertEqual(IR_SCHEMA_VERSION, "1.2.0")
        self.assertIn("accessor", SCHEMA_HISTORY["1.2.0"])

    def test_earlier_schemas_remain_readable(self) -> None:
        self.assertTrue(is_compatible("1.0.0"))
        self.assertTrue(is_compatible("1.1.0"))

    def test_the_accessor_field_survives_serialisation(self) -> None:
        from codeweaver.ir.base import node_from_dict

        function = IRFunction("v", is_method=True, accessor=ACCESSOR_SETTER)
        self.assertEqual(node_from_dict(function.to_dict()).accessor, ACCESSOR_SETTER)


class CodegenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contents = generate_module(module_for(PROPERTY_PAIR)).contents

    def test_the_getter_becomes_a_get_accessor(self) -> None:
        self.assertIn("get value(): number {", self.contents)

    def test_the_setter_becomes_a_set_accessor(self) -> None:
        self.assertIn("set value(new_: number) {", self.contents)

    def test_the_setter_declares_no_return_type(self) -> None:
        # A TypeScript setter with a return type does not compile.
        self.assertNotIn("set value(new_: number): ", self.contents)

    def test_the_applied_decorator_is_not_reported_as_unapplied(self) -> None:
        self.assertNotIn("decorator not applied", self.contents)

    def test_a_setter_with_the_wrong_arity_is_reported(self) -> None:
        klass = IRClass(
            "C",
            methods=[
                IRFunction("v", is_method=True, accessor=ACCESSOR_GETTER),
                IRFunction("v", is_method=True, accessor=ACCESSOR_SETTER),
            ],
        )
        from codeweaver.ir.declarations import IRModule

        generated = generate_module(IRModule("m", "m.py", classes=[klass]))
        self.assertIn("CW0643", [item.code for item in generated.diagnostics])
        self.assertIn("set v(value: unknown)", generated.contents)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "p"
        self.out = Path(self._tmp.name) / "out"
        self.root.mkdir(parents=True)
        (self.root / "box.py").write_text(PROPERTY_PAIR, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_module_is_not_dropped(self) -> None:
        analysis = analyse_project(self.root)
        self.assertEqual(analysis.parse_failures, [])
        self.assertEqual(analysis.counts.parsed, 1)

    def test_the_module_is_generated(self) -> None:
        outcome = run_migration(self.root, self.out, MigrationOptions(run_validation=False))
        self.assertIn("box.ts", outcome.written_files)
        self.assertEqual(outcome.report.counts.failed, 0)

    @unittest.skipUnless(HAS_TSC, "tsc is not installed")
    def test_the_generated_project_type_checks(self) -> None:
        outcome = run_migration(self.root, self.out)
        check = outcome.report.validation.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.PASSED, msg=check.output)

    @unittest.skipUnless(HAS_TSC, "tsc is not installed")
    def test_the_migration_passes_rather_than_failing(self) -> None:
        outcome = run_migration(self.root, self.out)
        self.assertEqual(outcome.report.status.value, "passed")


if __name__ == "__main__":
    unittest.main()
