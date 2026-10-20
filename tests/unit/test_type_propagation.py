from __future__ import annotations

import unittest

from codeweaver.backends.typescript.module import generate_module
from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.ir.base import walk
from codeweaver.ir.expressions import IRAttribute, IRName
from codeweaver.transforms.types import TypePropagationPass, propagate_types, scope_types


def module_for(source: str):
    result = parse_source(source, "m.py")
    assert result.module is not None
    return result.module


def name_types(module, name: str) -> list[str]:
    return [
        str(node.value_type)
        for node in walk(module)
        if isinstance(node, IRName) and node.name == name
    ]


class ScopeTests(unittest.TestCase):
    def test_parameters_contribute_their_declared_types(self) -> None:
        module = module_for("def f(a: int, b):\n    pass\n")
        scope = scope_types(module.functions[0])
        self.assertEqual(str(scope["a"]), "int")
        self.assertNotIn("b", scope)

    def test_annotated_declarations_contribute(self) -> None:
        module = module_for("def f():\n    total: int = 0\n")
        self.assertEqual(str(scope_types(module.functions[0])["total"]), "int")

    def test_literal_initialiser_type_is_used_when_unannotated(self) -> None:
        module = module_for("def f():\n    name = 'x'\n")
        self.assertEqual(str(scope_types(module.functions[0])["name"]), "str")

    def test_unknown_initialiser_contributes_nothing(self) -> None:
        module = module_for("def f(g):\n    value = g()\n")
        self.assertNotIn("value", scope_types(module.functions[0]))

    def test_conflicting_declarations_keep_the_first_and_report(self) -> None:
        module = module_for("def f():\n    x: int = 1\n    x: str = 'a'\n")
        bag = DiagnosticBag()
        scope = scope_types(module.functions[0], bag)
        self.assertEqual(str(scope["x"]), "int")
        self.assertIn("CW0710", [item.code for item in bag])


class PropagationTests(unittest.TestCase):
    def test_parameter_type_reaches_its_references(self) -> None:
        module = propagate_types(module_for("def f(xs: list[int]):\n    return xs\n"))
        self.assertIn("list[int]", name_types(module, "xs"))

    def test_local_declaration_type_reaches_later_references(self) -> None:
        module = propagate_types(module_for("def f():\n    total: int = 0\n    return total\n"))
        self.assertIn("int", name_types(module, "total"))

    def test_module_level_constant_type_reaches_references(self) -> None:
        module = propagate_types(module_for("NAME: str = 'x'\nother = NAME\n"))
        self.assertIn("str", name_types(module, "NAME"))

    def test_unrelated_names_stay_unknown(self) -> None:
        module = propagate_types(module_for("def f(a: int, b):\n    return b\n"))
        self.assertEqual(set(name_types(module, "b")), {"unknown"})

    def test_an_already_typed_reference_is_not_overwritten(self) -> None:
        module = propagate_types(module_for("def f(xs: list[int]):\n    return len(xs)\n"))
        self.assertNotIn("unknown", name_types(module, "xs"))

    def test_receiver_field_access_gains_the_field_type(self) -> None:
        source = (
            "class C:\n    items: list[int] = []\n    def count(self):\n        return self.items\n"
        )
        module = propagate_types(module_for(source))
        attributes = [
            str(node.value_type)
            for node in walk(module)
            if isinstance(node, IRAttribute) and node.name == "items"
        ]
        self.assertEqual(attributes, ["list[int]"])

    def test_field_access_on_another_object_is_untouched(self) -> None:
        source = (
            "class C:\n"
            "    items: list[int] = []\n"
            "    def count(self, other):\n"
            "        return other.items\n"
        )
        module = propagate_types(module_for(source))
        attributes = [
            str(node.value_type)
            for node in walk(module)
            if isinstance(node, IRAttribute) and node.name == "items"
        ]
        self.assertEqual(attributes, ["unknown"])

    def test_original_module_is_not_mutated(self) -> None:
        module = module_for("def f(xs: list[int]):\n    return xs\n")
        propagate_types(module)
        self.assertEqual(set(name_types(module, "xs")), {"unknown"})

    def test_propagation_is_idempotent(self) -> None:
        module = module_for("def f(xs: list[int]):\n    return xs\n")
        once = propagate_types(module)
        self.assertEqual(propagate_types(once), once)

    def test_module_with_nothing_to_do_is_returned_equal(self) -> None:
        module = module_for("x = 1\n")
        self.assertEqual(propagate_types(module), module)


class GeneratedOutputTests(unittest.TestCase):
    def generate(self, source: str) -> str:
        return generate_module(propagate_types(module_for(source))).contents

    def test_len_of_a_list_parameter_uses_length(self) -> None:
        self.assertIn("xs.length", self.generate("def f(xs: list[int]):\n    return len(xs)\n"))

    def test_len_of_a_dict_parameter_counts_keys(self) -> None:
        self.assertIn(
            "Object.keys(d).length",
            self.generate("def f(d: dict[str, int]):\n    return len(d)\n"),
        )

    def test_len_of_a_set_parameter_uses_size(self) -> None:
        self.assertIn("s.size", self.generate("def f(s: set[int]):\n    return len(s)\n"))

    def test_membership_on_a_typed_dict_uses_has_own(self) -> None:
        self.assertIn(
            "Object.hasOwn",
            self.generate("def f(d: dict[str, int], k: str):\n    return k in d\n"),
        )

    def test_without_propagation_the_backend_warns(self) -> None:
        module = module_for("def f(d: dict[str, int]):\n    return len(d)\n")
        generated = generate_module(module)
        self.assertIn("CW0660", [item.code for item in generated.diagnostics])

    def test_with_propagation_the_warning_is_gone(self) -> None:
        module = propagate_types(module_for("def f(d: dict[str, int]):\n    return len(d)\n"))
        generated = generate_module(module)
        self.assertNotIn("CW0660", [item.code for item in generated.diagnostics])


class PassTests(unittest.TestCase):
    def test_applies_to_a_module_with_content(self) -> None:
        self.assertTrue(TypePropagationPass().applies_to(module_for("def f():\n    pass\n")))

    def test_does_not_apply_to_an_empty_module(self) -> None:
        self.assertFalse(TypePropagationPass().applies_to(module_for("")))

    def test_run_returns_a_propagated_module(self) -> None:
        module = module_for("def f(xs: list[int]):\n    return xs\n")
        result = TypePropagationPass().run(module, DiagnosticBag())
        self.assertIn("list[int]", name_types(result, "xs"))


if __name__ == "__main__":
    unittest.main()
