from __future__ import annotations

import unittest

from codeweaver.frontends.python import PythonFrontend, module_name_for_path, parse_source
from codeweaver.ir.schema import validate_module
from codeweaver.ir.statements import IRVarDecl


def parse(source: str, path: str = "pkg/mod.py"):
    return parse_source(source, path)


class ModuleNameTests(unittest.TestCase):
    def test_plain_module(self) -> None:
        self.assertEqual(module_name_for_path("pkg/mod.py"), "pkg.mod")

    def test_package_init_drops_the_filename(self) -> None:
        self.assertEqual(module_name_for_path("pkg/__init__.py"), "pkg")

    def test_top_level_module(self) -> None:
        self.assertEqual(module_name_for_path("main.py"), "main")

    def test_nested_package(self) -> None:
        self.assertEqual(module_name_for_path("a/b/c.py"), "a.b.c")

    def test_empty_path(self) -> None:
        self.assertEqual(module_name_for_path(""), "")


class SyntaxErrorTests(unittest.TestCase):
    def test_syntax_error_is_reported_with_a_location(self) -> None:
        result = parse("def broken(:\n    pass\n")
        self.assertFalse(result.ok)
        self.assertIsNone(result.module)
        diagnostic = result.diagnostics.errors[0]
        self.assertEqual(diagnostic.code, "CW0410")
        self.assertEqual(diagnostic.span.file, "pkg/mod.py")
        self.assertEqual(diagnostic.span.start_line, 1)

    def test_null_byte_source_is_reported(self) -> None:
        result = parse("x = 1\x00\n")
        self.assertFalse(result.ok)
        self.assertTrue(result.diagnostics.has_errors)

    def test_valid_empty_module_parses(self) -> None:
        result = parse("")
        self.assertTrue(result.ok)
        self.assertEqual(result.module.name, "pkg.mod")


class ModuleStructureTests(unittest.TestCase):
    def test_docstring_is_captured_and_removed_from_the_body(self) -> None:
        module = parse('"""Module docs."""\nx = 1\n').module
        self.assertEqual(module.docstring, "Module docs.")
        self.assertEqual(len(module.body), 1)

    def test_module_records_path_and_language(self) -> None:
        module = parse("x = 1\n").module
        self.assertEqual(module.path, "pkg/mod.py")
        self.assertEqual(module.source_language, "python")

    def test_uppercase_module_variables_are_const(self) -> None:
        module = parse("VERSION = '1'\ncounter = 0\n").module
        declarations = [s for s in module.body if isinstance(s, IRVarDecl)]
        self.assertTrue(declarations[0].is_const)
        self.assertFalse(declarations[1].is_const)

    def test_generated_ir_validates(self) -> None:
        module = parse("def f(a: int) -> int:\n    return a\n").module
        self.assertFalse(validate_module(module).has_errors)


class ImportTests(unittest.TestCase):
    def test_plain_import(self) -> None:
        module = parse("import os\n").module
        self.assertEqual(module.imports[0].module, "os")
        self.assertTrue(module.imports[0].imports_whole_module)

    def test_import_with_alias(self) -> None:
        module = parse("import numpy as np\n").module
        self.assertEqual(module.imports[0].module_alias, "np")

    def test_multiple_names_in_one_import_become_separate_imports(self) -> None:
        module = parse("import os, sys\n").module
        self.assertEqual([imp.module for imp in module.imports], ["os", "sys"])

    def test_from_import(self) -> None:
        module = parse("from pkg.mod import thing as other\n").module
        found = module.imports[0]
        self.assertEqual(found.module, "pkg.mod")
        self.assertEqual(found.names[0].name, "thing")
        self.assertEqual(found.names[0].alias, "other")

    def test_relative_import_records_its_level(self) -> None:
        module = parse("from ..sibling import thing\n").module
        found = module.imports[0]
        self.assertTrue(found.is_relative)
        self.assertEqual(found.level, 2)
        self.assertEqual(found.module, "sibling")

    def test_bare_relative_import(self) -> None:
        module = parse("from . import sibling\n").module
        found = module.imports[0]
        self.assertTrue(found.is_relative)
        self.assertEqual(found.module, "")


class FunctionTests(unittest.TestCase):
    def test_signature_docstring_and_return_type(self) -> None:
        module = parse(
            'def add(a: int, b: int = 1) -> int:\n    """Add."""\n    return a + b\n'
        ).module
        function = module.function_named("add")
        self.assertEqual(function.docstring, "Add.")
        self.assertEqual(str(function.return_type), "int")
        self.assertEqual(function.arity, 2)
        self.assertEqual(len(function.body.statements), 1)

    def test_async_function_is_marked(self) -> None:
        module = parse("async def fetch():\n    pass\n").module
        self.assertTrue(module.function_named("fetch").is_async)

    def test_private_name_sets_visibility(self) -> None:
        module = parse("def _helper():\n    pass\n").module
        self.assertEqual(module.function_named("_helper").visibility, "protected")

    def test_unrecognised_decorator_is_recorded_and_warned_about(self) -> None:
        result = parse("@lru_cache\ndef f():\n    pass\n")
        function = result.module.function_named("f")
        self.assertEqual(len(function.decorators), 1)
        self.assertIn("CW0413", [d.code for d in result.diagnostics])

    def test_known_decorator_does_not_warn(self) -> None:
        result = parse("@property\ndef f(self):\n    pass\n")
        self.assertEqual([d.code for d in result.diagnostics], [])


class ClassTests(unittest.TestCase):
    SOURCE = (
        "class Account(Base):\n"
        '    """An account."""\n'
        "    kind = 'savings'\n"
        "    balance: int = 0\n"
        "\n"
        "    def __init__(self, owner: str) -> None:\n"
        "        self.owner = owner\n"
        "\n"
        "    @staticmethod\n"
        "    def make() -> 'Account':\n"
        "        return Account('x')\n"
        "\n"
        "    def _audit(self):\n"
        "        pass\n"
    )

    def setUp(self) -> None:
        self.result = parse(self.SOURCE)
        self.klass = self.result.module.class_named("Account")

    def test_docstring_and_bases(self) -> None:
        self.assertEqual(self.klass.docstring, "An account.")
        self.assertEqual(self.klass.bases[0].name, "Base")

    def test_class_and_annotated_fields(self) -> None:
        by_name = {item.name: item for item in self.klass.fields}
        self.assertTrue(by_name["kind"].is_static)
        self.assertFalse(by_name["balance"].is_static)
        self.assertEqual(str(by_name["balance"].field_type), "int")

    def test_constructor_is_identified(self) -> None:
        constructor = self.klass.constructor
        self.assertIsNotNone(constructor)
        self.assertEqual(constructor.name, "__init__")
        self.assertTrue(constructor.is_method)

    def test_receiver_is_removed_from_parameters_and_recorded(self) -> None:
        constructor = self.klass.constructor
        self.assertEqual([p.name for p in constructor.params], ["owner"])
        self.assertEqual(constructor.meta["pythonReceiver"], "self")

    def test_static_method_keeps_all_parameters_and_is_marked(self) -> None:
        method = self.klass.method_named("make")
        self.assertTrue(method.is_static)
        self.assertNotIn("pythonReceiver", method.meta)

    def test_method_visibility_follows_the_name(self) -> None:
        self.assertEqual(self.klass.method_named("_audit").visibility, "protected")

    def test_class_keywords_are_reported(self) -> None:
        result = parse("class C(metaclass=Meta):\n    pass\n")
        self.assertIn("CW0412", [d.code for d in result.diagnostics])

    def test_unsupported_class_body_statement_is_recorded(self) -> None:
        result = parse("class C:\n    for i in items:\n        pass\n")
        klass = result.module.class_named("C")
        self.assertEqual(len(klass.meta["unsupportedMembers"]), 1)

    def test_empty_class_body_is_fine(self) -> None:
        klass = parse("class C:\n    pass\n").module.class_named("C")
        self.assertEqual(klass.fields, [])
        self.assertEqual(klass.methods, [])


class UnsupportedReportingTests(unittest.TestCase):
    def test_unsupported_constructs_are_listed_on_the_module(self) -> None:
        module = parse(
            "def f(xs):\n    return (x for x in xs)\n\n"
            "def g():\n    with open('f') as h:\n        pass\n"
        ).module
        kinds = sorted(node.source_kind for node in module.unsupported_nodes())
        self.assertEqual(kinds, ["GeneratorExp", "With"])

    def test_module_with_no_gaps_reports_none(self) -> None:
        module = parse("def f(a):\n    return a\n").module
        self.assertEqual(module.unsupported_nodes(), [])


class FrontendAdapterTests(unittest.TestCase):
    def test_adapter_declares_its_extensions_and_limitations(self) -> None:
        frontend = PythonFrontend()
        self.assertEqual(frontend.language, "python")
        self.assertIn(".py", frontend.file_extensions)
        self.assertTrue(frontend.supported_constructs)
        self.assertTrue(any("comprehension" in item for item in frontend.known_limitations))

    def test_adapter_parses_through_the_protocol_method(self) -> None:
        result = PythonFrontend().parse_module("x = 1\n", "m.py")
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
