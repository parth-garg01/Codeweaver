from __future__ import annotations

import unittest

from codeweaver.ir.base import IRUnsupported, node_from_dict
from codeweaver.ir.declarations import (
    IRClass,
    IRDecorator,
    IRField,
    IRFunction,
    IRImport,
    IRImportName,
    IRModule,
    Visibility,
)
from codeweaver.ir.expressions import IRLiteral, IRName, IRParam
from codeweaver.ir.statements import IRBlock, IRExpressionStatement, IRReturn, IRVarDecl
from codeweaver.ir.types import t_int, t_str


def roundtrip(node):
    return node_from_dict(node.to_dict())


class VisibilityTests(unittest.TestCase):
    def test_public_name(self) -> None:
        self.assertEqual(Visibility.from_name("total"), Visibility.PUBLIC)

    def test_single_underscore_is_protected(self) -> None:
        self.assertEqual(Visibility.from_name("_cache"), Visibility.PROTECTED)

    def test_double_underscore_is_private(self) -> None:
        self.assertEqual(Visibility.from_name("__secret"), Visibility.PRIVATE)

    def test_dunder_is_public(self) -> None:
        self.assertEqual(Visibility.from_name("__init__"), Visibility.PUBLIC)


class ImportTests(unittest.TestCase):
    def test_whole_module_import(self) -> None:
        node = IRImport("os.path", module_alias="p")
        self.assertTrue(node.imports_whole_module)
        self.assertEqual(roundtrip(node), node)

    def test_from_import_with_alias(self) -> None:
        node = IRImport("pkg.mod", [IRImportName("thing", "aliased")])
        self.assertFalse(node.imports_whole_module)
        self.assertEqual(node.names[0].local_name, "aliased")
        self.assertEqual(roundtrip(node), node)

    def test_import_name_without_alias_uses_its_own_name(self) -> None:
        self.assertEqual(IRImportName("thing").local_name, "thing")

    def test_relative_import_records_level(self) -> None:
        node = IRImport("sibling", [IRImportName("x")], is_relative=True, level=2)
        restored = roundtrip(node)
        self.assertTrue(restored.is_relative)
        self.assertEqual(restored.level, 2)

    def test_external_flag_round_trips(self) -> None:
        self.assertTrue(roundtrip(IRImport("requests", external=True)).external)


class FunctionTests(unittest.TestCase):
    def test_arity_and_param_lookup(self) -> None:
        function = IRFunction("add", [IRParam("a", t_int()), IRParam("b", t_int())])
        self.assertEqual(function.arity, 2)
        self.assertIsNotNone(function.param_named("b"))
        self.assertIsNone(function.param_named("c"))

    def test_round_trips_with_decorators_and_docstring(self) -> None:
        function = IRFunction(
            "run",
            params=[IRParam("self")],
            return_type=t_str(),
            body=IRBlock([IRReturn(IRLiteral("ok"))]),
            is_async=True,
            is_method=True,
            decorators=[IRDecorator(IRName("staticmethod"), "@staticmethod")],
            docstring="Run it.",
        )
        self.assertEqual(roundtrip(function), function)

    def test_abstract_and_static_flags_survive(self) -> None:
        function = IRFunction("f", is_static=True, is_abstract=True)
        restored = roundtrip(function)
        self.assertTrue(restored.is_static)
        self.assertTrue(restored.is_abstract)


class ClassTests(unittest.TestCase):
    def build(self) -> IRClass:
        return IRClass(
            "Account",
            bases=[IRName("Base")],
            fields=[IRField("balance", t_int(), IRLiteral(0))],
            methods=[
                IRFunction("__init__", is_method=True, is_constructor=True),
                IRFunction("deposit", is_method=True),
            ],
            docstring="An account.",
        )

    def test_round_trips(self) -> None:
        klass = self.build()
        self.assertEqual(roundtrip(klass), klass)

    def test_constructor_and_method_lookup(self) -> None:
        klass = self.build()
        self.assertIsNotNone(klass.constructor)
        self.assertEqual(klass.constructor.name, "__init__")
        self.assertIsNotNone(klass.method_named("deposit"))
        self.assertIsNone(klass.method_named("withdraw"))

    def test_class_without_constructor(self) -> None:
        self.assertIsNone(IRClass("Plain").constructor)

    def test_interface_flag_survives(self) -> None:
        self.assertTrue(roundtrip(IRClass("Shape", is_interface=True)).is_interface)


class ModuleTests(unittest.TestCase):
    def build(self) -> IRModule:
        return IRModule(
            name="pkg.mod",
            path="pkg/mod.py",
            source_language="python",
            imports=[IRImport("os")],
            body=[IRVarDecl("VERSION", t_str(), IRLiteral("1.0"), is_const=True)],
            functions=[IRFunction("main")],
            classes=[IRClass("Thing")],
            docstring="Module docs.",
        )

    def test_round_trips(self) -> None:
        module = self.build()
        self.assertEqual(roundtrip(module), module)

    def test_declared_names_cover_functions_classes_and_variables(self) -> None:
        self.assertEqual(sorted(self.build().declared_names), ["Thing", "VERSION", "main"])

    def test_lookup_helpers(self) -> None:
        module = self.build()
        self.assertIsNotNone(module.function_named("main"))
        self.assertIsNone(module.function_named("nope"))
        self.assertIsNotNone(module.class_named("Thing"))
        self.assertIsNone(module.class_named("Nope"))

    def test_unsupported_nodes_are_discoverable_at_any_depth(self) -> None:
        module = self.build()
        module.functions[0].body = IRBlock(
            [IRExpressionStatement(IRLiteral(1)), IRUnsupported("Yield", "not mapped")]
        )
        unsupported = module.unsupported_nodes()
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0].source_kind, "Yield")

    def test_module_with_no_unsupported_nodes(self) -> None:
        self.assertEqual(self.build().unsupported_nodes(), [])


if __name__ == "__main__":
    unittest.main()
