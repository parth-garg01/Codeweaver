from __future__ import annotations

import unittest

from codeweaver.backends.typescript.module import ModuleLayout, generate_module
from codeweaver.diagnostics import DiagnosticBag
from codeweaver.frontends.python import parse_source
from codeweaver.graph import build_symbol_index
from codeweaver.ir.base import CONSTRUCTS_CLASS, walk
from codeweaver.ir.expressions import IRCall
from codeweaver.transforms.construction import (
    ClassConstructionPass,
    class_names_visible_in,
    mark_class_construction,
)


def modules_for(sources: dict[str, str]):
    modules = []
    for path, text in sources.items():
        result = parse_source(text, path)
        assert result.module is not None, path
        modules.append(result.module)
    return modules


def marked_calls(module) -> set[str]:
    return {
        node.callee.name
        for node in walk(module)
        if isinstance(node, IRCall) and node.meta.get(CONSTRUCTS_CLASS)
    }


class VisibilityTests(unittest.TestCase):
    def test_locally_declared_classes_are_visible(self) -> None:
        modules = modules_for({"a.py": "class Account:\n    pass\n"})
        symbols = build_symbol_index(modules)
        self.assertIn("Account", class_names_visible_in(modules[0], symbols))

    def test_imported_class_is_visible(self) -> None:
        modules = modules_for(
            {
                "models.py": "class Account:\n    pass\n",
                "app.py": "from models import Account\n",
            }
        )
        symbols = build_symbol_index(modules)
        self.assertIn("Account", class_names_visible_in(modules[1], symbols))

    def test_imported_alias_is_visible_under_its_local_name(self) -> None:
        modules = modules_for(
            {
                "models.py": "class Account:\n    pass\n",
                "app.py": "from models import Account as Acct\n",
            }
        )
        symbols = build_symbol_index(modules)
        visible = class_names_visible_in(modules[1], symbols)
        self.assertIn("Acct", visible)

    def test_imported_function_is_not_a_class(self) -> None:
        modules = modules_for(
            {
                "util.py": "def helper():\n    pass\n",
                "app.py": "from util import helper\n",
            }
        )
        symbols = build_symbol_index(modules)
        self.assertNotIn("helper", class_names_visible_in(modules[1], symbols))

    def test_class_from_an_unimported_module_is_not_visible(self) -> None:
        modules = modules_for({"models.py": "class Account:\n    pass\n", "app.py": "x = 1\n"})
        symbols = build_symbol_index(modules)
        self.assertNotIn("Account", class_names_visible_in(modules[1], symbols))


class MarkingTests(unittest.TestCase):
    def test_constructor_call_is_marked(self) -> None:
        modules = modules_for(
            {"a.py": "class Account:\n    pass\n\ndef make():\n    return Account()\n"}
        )
        marked = mark_class_construction(modules[0], frozenset({"Account"}))
        self.assertEqual(marked_calls(marked), {"Account"})

    def test_function_call_is_not_marked(self) -> None:
        modules = modules_for({"a.py": "def helper():\n    pass\n\nhelper()\n"})
        marked = mark_class_construction(modules[0], frozenset({"Account"}))
        self.assertEqual(marked_calls(marked), set())

    def test_attribute_call_is_not_marked(self) -> None:
        modules = modules_for({"a.py": "models.Account()\n"})
        marked = mark_class_construction(modules[0], frozenset({"Account"}))
        self.assertEqual(marked_calls(marked), set())

    def test_calls_inside_methods_are_marked(self) -> None:
        source = (
            "class Account:\n"
            "    pass\n"
            "\n"
            "class Factory:\n"
            "    def make(self):\n"
            "        return Account()\n"
        )
        marked = mark_class_construction(modules_for({"a.py": source})[0], frozenset({"Account"}))
        self.assertEqual(marked_calls(marked), {"Account"})

    def test_empty_class_set_leaves_the_module_untouched(self) -> None:
        module = modules_for({"a.py": "Account()\n"})[0]
        self.assertIs(mark_class_construction(module, frozenset()), module)

    def test_marking_is_idempotent(self) -> None:
        module = modules_for({"a.py": "Account()\n"})[0]
        once = mark_class_construction(module, frozenset({"Account"}))
        self.assertEqual(mark_class_construction(once, frozenset({"Account"})), once)

    def test_original_module_is_not_mutated(self) -> None:
        module = modules_for({"a.py": "Account()\n"})[0]
        mark_class_construction(module, frozenset({"Account"}))
        self.assertEqual(marked_calls(module), set())


class PassTests(unittest.TestCase):
    def build(self, sources: dict[str, str], index: int = 0):
        modules = modules_for(sources)
        symbols = build_symbol_index(modules)
        return modules[index], ClassConstructionPass.for_module(modules[index], symbols)

    def test_applies_when_a_class_is_called(self) -> None:
        module, pass_ = self.build({"a.py": "class A:\n    pass\n\nA()\n"})
        self.assertTrue(pass_.applies_to(module))

    def test_does_not_apply_when_no_class_is_called(self) -> None:
        module, pass_ = self.build({"a.py": "class A:\n    pass\n\nhelper()\n"})
        self.assertFalse(pass_.applies_to(module))

    def test_does_not_apply_without_any_classes(self) -> None:
        module, pass_ = self.build({"a.py": "helper()\n"})
        self.assertFalse(pass_.applies_to(module))

    def test_run_marks_the_calls(self) -> None:
        module, pass_ = self.build({"a.py": "class A:\n    pass\n\nA()\n"})
        self.assertEqual(marked_calls(pass_.run(module, DiagnosticBag())), {"A"})


class GeneratedOutputTests(unittest.TestCase):
    def generate(self, sources: dict[str, str], index: int) -> str:
        modules = modules_for(sources)
        symbols = build_symbol_index(modules)
        marked = [
            ClassConstructionPass.for_module(module, symbols).run(module, DiagnosticBag())
            for module in modules
        ]
        layout = ModuleLayout.for_modules(marked)
        return generate_module(marked[index], layout).contents

    def test_local_class_construction_uses_new(self) -> None:
        contents = self.generate(
            {"a.py": "class Account:\n    pass\n\ndef make():\n    return Account()\n"}, 0
        )
        self.assertIn("return new Account();", contents)

    def test_imported_class_construction_uses_new(self) -> None:
        contents = self.generate(
            {
                "models.py": "class Account:\n    pass\n",
                "app.py": "from models import Account\n\ndef make():\n    return Account()\n",
            },
            1,
        )
        self.assertIn("return new Account();", contents)

    def test_function_call_does_not_gain_new(self) -> None:
        contents = self.generate(
            {"a.py": "def helper():\n    pass\n\ndef go():\n    return helper()\n"}, 0
        )
        self.assertIn("return helper();", contents)
        self.assertNotIn("new helper", contents)

    def test_constructor_arguments_are_preserved(self) -> None:
        contents = self.generate(
            {
                "a.py": "class Account:\n"
                "    def __init__(self, owner: str):\n"
                "        self.owner = owner\n"
                "\n"
                "def make():\n"
                "    return Account('a')\n"
            },
            0,
        )
        self.assertIn('return new Account("a");', contents)


if __name__ == "__main__":
    unittest.main()
