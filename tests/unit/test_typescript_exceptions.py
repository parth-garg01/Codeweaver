from __future__ import annotations

import ast
import unittest

from codeweaver.backends.typescript.context import EmitContext
from codeweaver.backends.typescript.exceptions import (
    COLLAPSED_TO_ERROR,
    EXCEPTION_MAP,
    is_builtin_exception,
    map_exception_name,
)
from codeweaver.backends.typescript.module import generate_module
from codeweaver.backends.typescript.statements import emit_statement
from codeweaver.backends.typescript.writer import CodeWriter
from codeweaver.frontends.python import parse_source
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.statements import convert_statements


class MappingTests(unittest.TestCase):
    def test_known_exceptions_map_to_javascript_errors(self) -> None:
        self.assertEqual(map_exception_name("ValueError"), "Error")
        self.assertEqual(map_exception_name("TypeError"), "TypeError")
        self.assertEqual(map_exception_name("IndexError"), "RangeError")
        self.assertEqual(map_exception_name("NameError"), "ReferenceError")

    def test_project_exceptions_keep_their_name(self) -> None:
        self.assertEqual(map_exception_name("MyError"), "MyError")
        self.assertFalse(is_builtin_exception("MyError"))

    def test_collapsed_set_lists_only_error_mappings(self) -> None:
        for name in COLLAPSED_TO_ERROR:
            self.assertEqual(EXCEPTION_MAP[name], "Error")

    def test_type_error_is_not_collapsed(self) -> None:
        self.assertNotIn("TypeError", COLLAPSED_TO_ERROR)


class ThrowEmitTests(unittest.TestCase):
    def emit(self, source: str) -> str:
        convert_ctx = ConversionContext(path="m.py", source_lines=source.splitlines())
        statements = convert_statements(ast.parse(source).body, convert_ctx)
        self.ctx = EmitContext()
        writer = CodeWriter()
        for statement in statements:
            emit_statement(statement, writer, self.ctx)
        return writer.render()

    def codes(self) -> list[str]:
        return sorted({item.code for item in self.ctx.diagnostics})

    def test_raise_constructs_with_new(self) -> None:
        self.assertEqual(self.emit("raise MyError('x')"), 'throw new MyError("x");\n')

    def test_builtin_exception_is_mapped_and_reported(self) -> None:
        self.assertEqual(self.emit("raise ValueError('x')"), 'throw new Error("x");\n')
        self.assertIn("CW0629", self.codes())

    def test_non_collapsed_builtin_is_not_reported_as_collapsed(self) -> None:
        self.assertEqual(self.emit("raise TypeError('x')"), 'throw new TypeError("x");\n')
        self.assertNotIn("CW0629", self.codes())

    def test_raise_without_arguments_still_constructs(self) -> None:
        self.assertEqual(self.emit("raise MyError()"), "throw new MyError();\n")

    def test_raising_a_bare_name_throws_the_value(self) -> None:
        self.assertEqual(self.emit("raise err"), "throw err;\n")

    def test_raising_a_bare_builtin_name_is_mapped(self) -> None:
        self.assertEqual(self.emit("raise ValueError"), "throw Error;\n")

    def test_raise_from_wraps_with_a_cause(self) -> None:
        rendered = self.emit("raise MyError('x') from original")
        self.assertIn('Object.assign(new MyError("x"), { cause: original })', rendered)


class CatchEmitTests(ThrowEmitTests):
    def test_catch_type_is_mapped_and_reported(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept ValueError:\n    g()")
        self.assertIn("error instanceof Error", rendered)
        self.assertIn("CW0630", self.codes())

    def test_project_exception_catch_is_unchanged(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept MyError:\n    g()")
        self.assertIn("error instanceof MyError", rendered)
        self.assertNotIn("CW0630", self.codes())

    def test_tuple_of_types_maps_each_one(self) -> None:
        rendered = self.emit("try:\n    f()\nexcept (KeyError, IndexError):\n    g()")
        self.assertIn("error instanceof Error || error instanceof RangeError", rendered)


class ExceptionSubclassTests(unittest.TestCase):
    def generate(self, source: str):
        result = parse_source(source, "m.py")
        assert result.module is not None
        return generate_module(result.module)

    def test_subclass_of_a_builtin_exception_extends_error(self) -> None:
        generated = self.generate("class MyError(Exception):\n    pass\n")
        self.assertIn("class MyError extends Error {", generated.contents)
        self.assertIn("CW0642", [item.code for item in generated.diagnostics])

    def test_subclass_of_value_error_extends_error(self) -> None:
        generated = self.generate("class BadInput(ValueError):\n    pass\n")
        self.assertIn("extends Error", generated.contents)

    def test_subclass_of_a_project_class_is_unchanged(self) -> None:
        generated = self.generate("class B(A):\n    pass\n")
        self.assertIn("class B extends A {", generated.contents)
        self.assertNotIn("CW0642", [item.code for item in generated.diagnostics])


if __name__ == "__main__":
    unittest.main()
