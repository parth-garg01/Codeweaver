from __future__ import annotations

import unittest

from codeweaver.errors import IRSchemaError
from codeweaver.ir import (
    IRBlock,
    IRCatch,
    IRClass,
    IRExpressionStatement,
    IRField,
    IRFunction,
    IRImport,
    IRImportName,
    IRLiteral,
    IRModule,
    IRName,
    IRNoOp,
    IRParam,
    IRTry,
    IRUnsupported,
    IRVarDecl,
)
from codeweaver.ir.schema import assert_valid, validate_module, validate_node
from codeweaver.ir.statements import IRAssign, IRIf
from codeweaver.ir.types import t_int


def codes(bag) -> list[str]:
    return sorted({item.code for item in bag})


class ModuleValidationTests(unittest.TestCase):
    def test_a_minimal_module_is_valid(self) -> None:
        bag = validate_module(IRModule("pkg.mod", "pkg/mod.py"))
        self.assertFalse(bag.has_errors)
        self.assertEqual(len(bag), 0)

    def test_non_module_input_is_rejected(self) -> None:
        bag = validate_module(IRLiteral(1))  # type: ignore[arg-type]
        self.assertIn("CW0301", codes(bag))

    def test_missing_name_is_an_error(self) -> None:
        self.assertIn("CW0302", codes(validate_module(IRModule("", "m.py"))))

    def test_missing_path_is_a_warning_not_an_error(self) -> None:
        bag = validate_module(IRModule("m"))
        self.assertIn("CW0303", codes(bag))
        self.assertFalse(bag.has_errors)

    def test_duplicate_top_level_names_are_rejected(self) -> None:
        module = IRModule(
            "m",
            "m.py",
            functions=[IRFunction("thing")],
            classes=[IRClass("thing")],
        )
        self.assertIn("CW0304", codes(validate_module(module)))

    def test_assert_valid_raises_on_errors(self) -> None:
        with self.assertRaises(IRSchemaError):
            assert_valid(IRModule("", "m.py"))

    def test_assert_valid_passes_on_warnings(self) -> None:
        assert_valid(IRModule("m"))


class FunctionValidationTests(unittest.TestCase):
    def test_unnamed_function_is_rejected(self) -> None:
        self.assertIn("CW0306", codes(validate_node(IRFunction(""))))

    def test_duplicate_parameters_are_rejected(self) -> None:
        function = IRFunction("f", [IRParam("a"), IRParam("a")])
        self.assertIn("CW0307", codes(validate_node(function)))

    def test_unknown_parameter_style_is_rejected(self) -> None:
        function = IRFunction("f", [IRParam("a", style="sideways")])
        self.assertIn("CW0308", codes(validate_node(function)))

    def test_parameter_after_kwargs_is_rejected(self) -> None:
        function = IRFunction("f", [IRParam("kw", style="var_keyword"), IRParam("late")])
        self.assertIn("CW0309", codes(validate_node(function)))

    def test_two_star_args_are_rejected(self) -> None:
        function = IRFunction(
            "f",
            [IRParam("a", style="var_positional"), IRParam("b", style="var_positional")],
        )
        self.assertIn("CW0310", codes(validate_node(function)))

    def test_valid_parameter_order_passes(self) -> None:
        function = IRFunction(
            "f",
            [
                IRParam("a"),
                IRParam("b", default=IRLiteral(1)),
                IRParam("args", style="var_positional"),
                IRParam("kwargs", style="var_keyword"),
            ],
        )
        self.assertFalse(validate_node(function).has_errors)

    def test_constructor_must_be_a_method(self) -> None:
        function = IRFunction("__init__", is_constructor=True)
        self.assertIn("CW0311", codes(validate_node(function)))

    def test_abstract_function_with_a_body_warns(self) -> None:
        function = IRFunction("f", is_abstract=True, body=IRBlock([IRNoOp()]))
        bag = validate_node(function)
        self.assertIn("CW0312", codes(bag))
        self.assertFalse(bag.has_errors)


class ClassValidationTests(unittest.TestCase):
    def test_unnamed_class_is_rejected(self) -> None:
        self.assertIn("CW0313", codes(validate_node(IRClass(""))))

    def test_duplicate_fields_and_methods_are_rejected(self) -> None:
        klass = IRClass(
            "C",
            fields=[IRField("x"), IRField("x")],
            methods=[IRFunction("m"), IRFunction("m")],
        )
        found = codes(validate_node(klass))
        self.assertIn("CW0314", found)
        self.assertIn("CW0315", found)

    def test_two_constructors_are_rejected(self) -> None:
        klass = IRClass(
            "C",
            methods=[
                IRFunction("a", is_method=True, is_constructor=True),
                IRFunction("b", is_method=True, is_constructor=True),
            ],
        )
        self.assertIn("CW0316", codes(validate_node(klass)))

    def test_interface_with_fields_warns(self) -> None:
        klass = IRClass("I", fields=[IRField("x")], is_interface=True)
        self.assertIn("CW0317", codes(validate_node(klass)))


class ImportValidationTests(unittest.TestCase):
    def test_import_without_a_module_is_rejected(self) -> None:
        self.assertIn("CW0318", codes(validate_node(IRImport(""))))

    def test_relative_import_without_a_module_is_allowed(self) -> None:
        node = IRImport("", [IRImportName("x")], is_relative=True, level=1)
        self.assertFalse(validate_node(node).has_errors)

    def test_level_without_relative_flag_is_rejected(self) -> None:
        self.assertIn("CW0319", codes(validate_node(IRImport("m", level=1))))

    def test_duplicate_local_names_are_rejected(self) -> None:
        node = IRImport("m", [IRImportName("a"), IRImportName("b", "a")])
        self.assertIn("CW0320", codes(validate_node(node)))


class StatementValidationTests(unittest.TestCase):
    def test_expression_in_a_block_is_rejected(self) -> None:
        block = IRBlock([IRLiteral(1)])  # type: ignore[list-item]
        self.assertIn("CW0321", codes(validate_node(block)))

    def test_try_without_handler_or_finally_is_rejected(self) -> None:
        self.assertIn("CW0322", codes(validate_node(IRTry(body=IRBlock([IRNoOp()])))))

    def test_try_with_only_finally_is_valid(self) -> None:
        node = IRTry(body=IRBlock([IRNoOp()]), finally_body=IRBlock([IRNoOp()]))
        self.assertFalse(validate_node(node).has_errors)

    def test_catch_all_handler_must_come_last(self) -> None:
        node = IRTry(
            body=IRBlock([IRNoOp()]),
            handlers=[
                IRCatch(body=IRBlock([IRNoOp()])),
                IRCatch([IRName("ValueError")], body=IRBlock([IRNoOp()])),
            ],
        )
        self.assertIn("CW0323", codes(validate_node(node)))

    def test_else_without_handlers_warns(self) -> None:
        node = IRTry(
            body=IRBlock([IRNoOp()]),
            finally_body=IRBlock([IRNoOp()]),
            else_body=IRBlock([IRNoOp()]),
        )
        self.assertIn("CW0324", codes(validate_node(node)))


class ExpressionSlotTests(unittest.TestCase):
    def test_missing_required_expression_is_rejected(self) -> None:
        node = IRIf(None)  # type: ignore[arg-type]
        self.assertIn("CW0325", codes(validate_node(node)))

    def test_statement_in_an_expression_slot_is_rejected(self) -> None:
        node = IRAssign(IRName("x"), IRNoOp())  # type: ignore[arg-type]
        self.assertIn("CW0326", codes(validate_node(node)))

    def test_non_expression_parameter_default_is_rejected(self) -> None:
        node = IRParam("a", default=IRNoOp())  # type: ignore[arg-type]
        self.assertIn("CW0327", codes(validate_node(node)))

    def test_valid_expression_statement_passes(self) -> None:
        self.assertFalse(validate_node(IRExpressionStatement(IRLiteral(1))).has_errors)


class UnsupportedReportingTests(unittest.TestCase):
    def test_unsupported_nodes_are_warned_about_not_errors(self) -> None:
        module = IRModule(
            "m",
            "m.py",
            functions=[
                IRFunction(
                    "gen",
                    body=IRBlock([IRUnsupported("Yield", "generators are not mapped")]),
                )
            ],
        )
        bag = validate_module(module)
        self.assertIn("CW0300", codes(bag))
        self.assertFalse(bag.has_errors)
        self.assertIn("generators are not mapped", bag.warnings[0].message)

    def test_a_realistic_valid_module_produces_no_diagnostics(self) -> None:
        module = IRModule(
            "pkg.mod",
            "pkg/mod.py",
            source_language="python",
            imports=[IRImport("os", [IRImportName("path")])],
            body=[IRVarDecl("VERSION", t_int(), IRLiteral(1), is_const=True)],
            functions=[
                IRFunction(
                    "add",
                    [IRParam("a", t_int()), IRParam("b", t_int())],
                    return_type=t_int(),
                    body=IRBlock([IRExpressionStatement(IRLiteral(0))]),
                )
            ],
            classes=[
                IRClass(
                    "Thing",
                    fields=[IRField("count", t_int(), IRLiteral(0))],
                    methods=[IRFunction("__init__", is_method=True, is_constructor=True)],
                )
            ],
        )
        self.assertEqual(len(validate_module(module)), 0)


if __name__ == "__main__":
    unittest.main()
