from __future__ import annotations

import unittest

from codeweaver.backends.typescript.module import generate_module
from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.transforms import default_pipeline
from codeweaver.transforms.fields import assigned_attribute_types, infer_instance_fields
from codeweaver.transforms.inheritance import (
    collect_inherited_fields,
    resolve_base_names,
)
from codeweaver.transforms.types import propagate_types

HIERARCHY = (
    "class Base:\n"
    "    rate: float = 0.02\n"
    "\n"
    "    def __init__(self, owner: str) -> None:\n"
    "        self.owner = owner\n"
    "        self.balance = 0\n"
    "\n"
    "\n"
    "class Middle(Base):\n"
    "    pass\n"
    "\n"
    "\n"
    "class Leaf(Middle):\n"
    "    def accrue(self) -> int:\n"
    "        earned: int = int(self.balance * self.rate)\n"
    "        self.balance += earned\n"
    "        return earned\n"
)


def modules_for(sources: dict[str, str]):
    modules = []
    for path, text in sources.items():
        result = parse_source(text, path)
        assert result.module is not None, path
        modules.append(result.module)
    return modules


class BaseNameTests(unittest.TestCase):
    def test_bare_base_names_are_resolved(self) -> None:
        klass = modules_for({"a.py": "class C(A, B):\n    pass\n"})[0].classes[0]
        self.assertEqual(resolve_base_names(klass), ["A", "B"])

    def test_dotted_base_is_not_a_bare_name(self) -> None:
        klass = modules_for({"a.py": "class C(mod.A):\n    pass\n"})[0].classes[0]
        self.assertEqual(resolve_base_names(klass), [])

    def test_class_without_bases(self) -> None:
        klass = modules_for({"a.py": "class C:\n    pass\n"})[0].classes[0]
        self.assertEqual(resolve_base_names(klass), [])


class AssignedAttributeTests(unittest.TestCase):
    def test_constructor_assignments_are_reported_before_inference_runs(self) -> None:
        klass = modules_for({"a.py": HIERARCHY})[0].classes[0]
        found = assigned_attribute_types(klass)
        self.assertEqual(sorted(found), ["balance", "owner"])
        self.assertEqual(str(found["owner"]), "str")

    def test_a_class_that_assigns_nothing_reports_nothing(self) -> None:
        klass = modules_for({"a.py": "class C:\n    pass\n"})[0].classes[0]
        self.assertEqual(assigned_attribute_types(klass), {})


class InheritedFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inherited = collect_inherited_fields(modules_for({"a.py": HIERARCHY}))

    def test_a_root_class_inherits_nothing(self) -> None:
        self.assertEqual(self.inherited["Base"], {})

    def test_declared_fields_are_inherited(self) -> None:
        self.assertEqual(str(self.inherited["Middle"]["rate"]), "float")

    def test_assigned_fields_are_inherited_too(self) -> None:
        self.assertEqual(sorted(self.inherited["Middle"]), ["balance", "owner", "rate"])

    def test_inheritance_is_transitive(self) -> None:
        self.assertEqual(sorted(self.inherited["Leaf"]), ["balance", "owner", "rate"])
        self.assertEqual(str(self.inherited["Leaf"]["owner"]), "str")

    def test_a_class_does_not_inherit_its_own_fields(self) -> None:
        inherited = collect_inherited_fields(modules_for({"a.py": "class C:\n    x: int = 0\n"}))
        self.assertEqual(inherited["C"], {})

    def test_an_unknown_base_class_is_ignored(self) -> None:
        inherited = collect_inherited_fields(
            modules_for({"a.py": "class C(External):\n    pass\n"})
        )
        self.assertEqual(inherited["C"], {})

    def test_bases_across_modules_are_resolved_by_name(self) -> None:
        inherited = collect_inherited_fields(
            modules_for(
                {
                    "base.py": "class Base:\n    rate: float = 0.0\n",
                    "leaf.py": "from base import Base\n\n\nclass Leaf(Base):\n    pass\n",
                }
            )
        )
        self.assertEqual(sorted(inherited["Leaf"]), ["rate"])

    def test_a_hierarchy_cycle_does_not_hang(self) -> None:
        inherited = collect_inherited_fields(
            modules_for({"a.py": "class A(B):\n    pass\n\n\nclass B(A):\n    pass\n"})
        )
        self.assertEqual(inherited["A"], {})


class SubclassFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = modules_for({"a.py": HIERARCHY})[0]
        self.inherited = collect_inherited_fields([self.module])

    def test_subclass_does_not_redeclare_an_inherited_field(self) -> None:
        inferred = infer_instance_fields(self.module, DiagnosticBag(), self.inherited)
        leaf = inferred.class_named("Leaf")
        self.assertEqual([item.name for item in leaf.fields], [])

    def test_without_the_mapping_the_subclass_does_redeclare_it(self) -> None:
        inferred = infer_instance_fields(self.module, DiagnosticBag())
        leaf = inferred.class_named("Leaf")
        self.assertEqual([item.name for item in leaf.fields], ["balance"])

    def test_base_class_still_declares_its_own_fields(self) -> None:
        inferred = infer_instance_fields(self.module, DiagnosticBag(), self.inherited)
        base = inferred.class_named("Base")
        self.assertEqual(sorted(item.name for item in base.fields), ["balance", "owner", "rate"])

    def test_inherited_field_types_reach_receiver_references(self) -> None:
        propagated = propagate_types(self.module, DiagnosticBag(), self.inherited)
        leaf = propagated.class_named("Leaf")
        contents = generate_module(
            propagated.__class__(name="m", path="m.py", classes=[leaf], source_language="python")
        ).contents
        self.assertNotIn("Object is of type", contents)
        self.assertIn("this.balance", contents)


class GeneratedOutputTests(unittest.TestCase):
    def test_pipeline_output_declares_each_field_once(self) -> None:
        module = modules_for({"a.py": HIERARCHY})[0]
        inherited = collect_inherited_fields([module])
        result = default_pipeline("typescript", inherited_fields=inherited).run(module)
        contents = generate_module(result.module).contents
        self.assertEqual(contents.count("balance"), contents.count("this.balance") + 1)
        self.assertIn("class Leaf extends Middle {", contents)
        self.assertNotIn("balance?: unknown;", contents)


if __name__ == "__main__":
    unittest.main()
