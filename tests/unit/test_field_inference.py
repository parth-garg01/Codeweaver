from __future__ import annotations

import unittest

from codeweaver.backends.typescript.module import generate_module
from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.ir.version import IR_SCHEMA_VERSION, SCHEMA_HISTORY, is_compatible
from codeweaver.transforms.fields import InferFieldsPass, infer_instance_fields


def module_for(source: str):
    result = parse_source(source, "m.py")
    assert result.module is not None
    return result.module


def fields_of(source: str, bag: DiagnosticBag | None = None):
    module = infer_instance_fields(module_for(source), bag)
    return {item.name: item for item in module.classes[0].fields}


class InferenceTests(unittest.TestCase):
    def test_constructor_assignment_declares_a_field(self) -> None:
        fields = fields_of("class A:\n    def __init__(self, owner):\n        self.owner = owner\n")
        self.assertIn("owner", fields)
        self.assertFalse(fields["owner"].optional)

    def test_field_type_comes_from_the_constructor_parameter(self) -> None:
        fields = fields_of(
            "class A:\n    def __init__(self, owner: str):\n        self.owner = owner\n"
        )
        self.assertEqual(str(fields["owner"].field_type), "str")

    def test_field_type_comes_from_a_literal_value(self) -> None:
        fields = fields_of("class A:\n    def __init__(self):\n        self.count = 0\n")
        self.assertEqual(str(fields["count"].field_type), "int")

    def test_untyped_assignment_gives_an_unknown_field(self) -> None:
        fields = fields_of("class A:\n    def __init__(self, g):\n        self.value = g()\n")
        self.assertEqual(str(fields["value"].field_type), "unknown")

    def test_already_declared_field_is_not_duplicated(self) -> None:
        source = (
            "class A:\n"
            "    owner: str = ''\n"
            "    def __init__(self, owner: str):\n"
            "        self.owner = owner\n"
        )
        module = infer_instance_fields(module_for(source))
        names = [item.name for item in module.classes[0].fields]
        self.assertEqual(names, ["owner"])

    def test_assignment_in_a_later_method_is_optional_and_reported(self) -> None:
        bag = DiagnosticBag()
        fields = fields_of(
            "class A:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def close(self):\n"
            "        self.closed = True\n",
            bag,
        )
        self.assertTrue(fields["closed"].optional)
        self.assertIn("CW0720", [item.code for item in bag])

    def test_constructor_assignment_wins_over_a_later_method(self) -> None:
        fields = fields_of(
            "class A:\n"
            "    def close(self):\n"
            "        self.state = 'closed'\n"
            "    def __init__(self):\n"
            "        self.state = 0\n"
        )
        self.assertEqual(str(fields["state"].field_type), "int")
        self.assertFalse(fields["state"].optional)

    def test_augmented_assignment_also_declares_a_field(self) -> None:
        fields = fields_of("class A:\n    def __init__(self):\n        self.total += 1\n")
        self.assertIn("total", fields)

    def test_underscore_field_is_protected(self) -> None:
        fields = fields_of("class A:\n    def __init__(self):\n        self._cache = {}\n")
        self.assertEqual(fields["_cache"].visibility, "protected")

    def test_inference_source_is_recorded(self) -> None:
        fields = fields_of("class A:\n    def __init__(self):\n        self.x = 1\n")
        self.assertEqual(fields["x"].meta["inferredFrom"], "__init__")

    def test_assignment_to_another_object_is_not_a_field(self) -> None:
        fields = fields_of("class A:\n    def __init__(self, other):\n        other.x = 1\n")
        self.assertEqual(fields, {})

    def test_static_method_assignments_are_not_fields(self) -> None:
        source = "class A:\n    @staticmethod\n    def make(self):\n        self.x = 1\n"
        self.assertEqual(fields_of(source), {})

    def test_module_without_classes_is_returned_unchanged(self) -> None:
        module = module_for("def f():\n    pass\n")
        self.assertIs(infer_instance_fields(module), module)

    def test_original_module_is_not_mutated(self) -> None:
        module = module_for("class A:\n    def __init__(self):\n        self.x = 1\n")
        infer_instance_fields(module)
        self.assertEqual(module.classes[0].fields, [])

    def test_inference_is_idempotent(self) -> None:
        module = module_for("class A:\n    def __init__(self):\n        self.x = 1\n")
        once = infer_instance_fields(module)
        self.assertEqual(infer_instance_fields(once), once)


class GeneratedOutputTests(unittest.TestCase):
    def generate(self, source: str) -> str:
        return generate_module(infer_instance_fields(module_for(source))).contents

    def test_constructor_field_is_declared_without_a_question_mark(self) -> None:
        contents = self.generate(
            "class A:\n    def __init__(self, owner: str):\n        self.owner = owner\n"
        )
        self.assertIn("owner: string;", contents)
        self.assertNotIn("owner?:", contents)

    def test_late_assigned_field_is_declared_optional(self) -> None:
        contents = self.generate(
            "class A:\n"
            "    def __init__(self):\n"
            "        pass\n"
            "    def close(self):\n"
            "        self.closed = True\n"
        )
        self.assertIn("closed?: boolean;", contents)

    def test_without_inference_the_field_is_never_declared(self) -> None:
        module = module_for(
            "class A:\n    def __init__(self, owner: str):\n        self.owner = owner\n"
        )
        self.assertNotIn("owner: string;", generate_module(module).contents)


class PassTests(unittest.TestCase):
    def test_applies_when_a_method_assigns_an_attribute(self) -> None:
        module = module_for("class A:\n    def __init__(self):\n        self.x = 1\n")
        self.assertTrue(InferFieldsPass().applies_to(module))

    def test_does_not_apply_without_attribute_assignment(self) -> None:
        module = module_for("class A:\n    def f(self):\n        pass\n")
        self.assertFalse(InferFieldsPass().applies_to(module))


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_was_bumped_and_documented(self) -> None:
        self.assertEqual(IR_SCHEMA_VERSION, "1.1.0")
        self.assertIn("optional", SCHEMA_HISTORY["1.1.0"])

    def test_earlier_schema_remains_readable(self) -> None:
        self.assertTrue(is_compatible("1.0.0"))

    def test_optional_flag_survives_serialisation(self) -> None:
        from codeweaver.ir.base import node_from_dict
        from codeweaver.ir.declarations import IRField

        field = IRField("closed", optional=True)
        self.assertTrue(node_from_dict(field.to_dict()).optional)


if __name__ == "__main__":
    unittest.main()
