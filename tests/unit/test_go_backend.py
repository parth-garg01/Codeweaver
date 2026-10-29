from __future__ import annotations

import unittest

from codeweaver.backends.go import (
    GO_VERSION,
    GoBackend,
    GoLayout,
    exported_name,
    generate_module,
    output_path_for,
    package_name_for,
    render_go_mod,
    render_type,
    unexported_name,
)
from codeweaver.frontends.python import parse_source
from codeweaver.graph import build_symbol_index
from codeweaver.ir.types import (
    IRType,
    TypeKind,
    t_bool,
    t_dict,
    t_float,
    t_int,
    t_list,
    t_optional,
    t_set,
    t_str,
    t_tuple,
    t_union,
    t_unknown,
)
from codeweaver.language_registry import Backend
from codeweaver.transforms import default_pipeline
from codeweaver.transforms.construction import ClassConstructionPass
from codeweaver.transforms.inheritance import collect_inherited_fields

SOURCE = (
    "class Account:\n"
    "    rate: float = 0.02\n"
    "\n"
    "    def __init__(self, owner: str, balance: int = 0) -> None:\n"
    "        self.owner = owner\n"
    "        self.balance = balance\n"
    "\n"
    "    def deposit(self, amount: int) -> None:\n"
    "        if amount <= 0:\n"
    "            raise ValueError('positive only')\n"
    "        self.balance += amount\n"
    "\n"
    "    def describe(self) -> str:\n"
    "        return f'{self.owner}: {self.balance}'\n"
    "\n"
    "\n"
    "def total(accounts: list[Account]) -> int:\n"
    "    running: int = 0\n"
    "    for i in range(len(accounts)):\n"
    "        running += accounts[i].balance\n"
    "    return running\n"
)


def prepared(source: str, path: str = "pkg/models.py"):
    result = parse_source(source, path)
    assert result.module is not None, [d.format() for d in result.diagnostics]
    module = result.module
    symbols = build_symbol_index([module])
    pipeline = default_pipeline("go", inherited_fields=collect_inherited_fields([module]))
    pipeline.passes.insert(0, ClassConstructionPass.for_module(module, symbols))
    return pipeline.run(module).module


def generated(source: str = SOURCE, path: str = "pkg/models.py"):
    module = prepared(source, path)
    return generate_module(module, GoLayout.for_modules([module]))


class NamingTests(unittest.TestCase):
    def test_a_public_name_is_capitalised(self) -> None:
        self.assertEqual(exported_name("total"), "Total")
        self.assertEqual(exported_name("total_count"), "Total_count")

    def test_an_already_capitalised_name_is_unchanged(self) -> None:
        self.assertEqual(exported_name("Account"), "Account")

    def test_leading_underscores_are_dropped_before_capitalising(self) -> None:
        # `_Total` is not exported in Go: the first *letter* decides, and an
        # underscore is not a letter.
        self.assertEqual(exported_name("_total"), "Total")

    def test_an_unexportable_name_is_prefixed(self) -> None:
        self.assertEqual(exported_name("1st"), "X1st")

    def test_unexported_names_start_lower_case(self) -> None:
        self.assertEqual(unexported_name("Total"), "total")
        self.assertEqual(unexported_name("_helper"), "helper")

    def test_a_go_keyword_is_made_safe(self) -> None:
        from codeweaver.backends.go.naming import GO_POLICY

        self.assertEqual(GO_POLICY.identifier("range"), "range_")
        self.assertEqual(GO_POLICY.identifier("string"), "string_")


class TypeMappingTests(unittest.TestCase):
    def test_primitives(self) -> None:
        self.assertEqual(render_type(t_int()), "int")
        self.assertEqual(render_type(t_float()), "float64")
        self.assertEqual(render_type(t_str()), "string")
        self.assertEqual(render_type(t_bool()), "bool")

    def test_unknown_and_any_become_any(self) -> None:
        self.assertEqual(render_type(t_unknown()), "any")
        self.assertEqual(render_type(None), "any")

    def test_a_list_becomes_a_slice(self) -> None:
        self.assertEqual(render_type(t_list(t_int())), "[]int")

    def test_a_dict_becomes_a_map(self) -> None:
        self.assertEqual(render_type(t_dict(t_str(), t_int())), "map[string]int")

    def test_a_set_becomes_a_map_to_the_empty_struct(self) -> None:
        self.assertEqual(render_type(t_set(t_str())), "map[string]struct{}")

    def test_an_object_type_is_a_pointer(self) -> None:
        # Python objects are references; a value receiver would mutate a copy.
        self.assertEqual(render_type(IRType(TypeKind.OBJECT, "Account")), "*Account")

    def test_an_optional_object_is_not_double_pointed(self) -> None:
        optional = t_optional(IRType(TypeKind.OBJECT, "Account"))
        self.assertEqual(render_type(optional), "*Account")

    def test_an_optional_primitive_becomes_a_pointer(self) -> None:
        self.assertEqual(render_type(t_optional(t_int())), "*int")

    def test_an_optional_slice_stays_a_slice(self) -> None:
        self.assertEqual(render_type(t_optional(t_list(t_int()))), "[]int")

    def test_a_union_becomes_any(self) -> None:
        self.assertEqual(render_type(t_union(t_int(), t_str())), "any")

    def test_a_homogeneous_tuple_becomes_a_slice(self) -> None:
        self.assertEqual(render_type(t_tuple(t_int(), t_int())), "[]int")

    def test_a_mixed_tuple_becomes_a_slice_of_any(self) -> None:
        self.assertEqual(render_type(t_tuple(t_int(), t_str())), "[]any")


class LayoutTests(unittest.TestCase):
    def test_output_paths_name_the_file_after_the_module(self) -> None:
        self.assertEqual(output_path_for("pkg/models.py"), "pkg/models.go")

    def test_a_package_init_is_named_after_its_directory(self) -> None:
        # Go has no special filename for a package.
        self.assertEqual(output_path_for("pkg/__init__.py"), "pkg/pkg.go")

    def test_the_package_name_is_the_directory(self) -> None:
        self.assertEqual(package_name_for("pkg/models.go"), "pkg")
        self.assertEqual(package_name_for("main.go"), "main")

    def test_import_paths_are_rooted_at_the_module_path(self) -> None:
        module = prepared("x = 1\n", "pkg/models.py")
        layout = GoLayout.for_modules([module], module_path="example.com/out")
        self.assertEqual(layout.import_path("pkg.models"), "example.com/out/pkg")

    def test_an_unknown_module_has_no_import_path(self) -> None:
        self.assertIsNone(GoLayout().import_path("nope"))

    def test_go_mod_declares_the_module_and_version(self) -> None:
        text = render_go_mod("example.com/out")
        self.assertIn("module example.com/out", text)
        self.assertIn(f"go {GO_VERSION}", text)


class CodegenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = generated()
        self.contents = self.result.contents

    def test_the_package_clause_matches_the_directory(self) -> None:
        self.assertIn("package pkg", self.contents)

    def test_a_class_becomes_a_struct(self) -> None:
        self.assertIn("type Account struct {", self.contents)
        self.assertIn("Owner string", self.contents)
        self.assertIn("Balance int", self.contents)

    def test_methods_take_a_pointer_receiver(self) -> None:
        self.assertIn("func (self *Account) Deposit(amount int) {", self.contents)

    def test_the_constructor_becomes_a_new_function(self) -> None:
        self.assertIn("func NewAccount(owner string, balance int) *Account {", self.contents)
        self.assertIn("return self", self.contents)

    def test_class_attribute_defaults_are_assigned_in_the_constructor(self) -> None:
        # A Go struct field cannot have a default, so the value would be lost.
        self.assertIn("self.Rate = 0.02", self.contents)

    def test_a_builtin_exception_becomes_an_error_value(self) -> None:
        self.assertIn('panic(errors.New("positive only"))', self.contents)
        self.assertIn("CW0726", [item.code for item in self.result.diagnostics])

    def test_an_fstring_becomes_sprintf(self) -> None:
        self.assertIn('fmt.Sprintf("%v: %v", self.Owner, self.Balance)', self.contents)

    def test_a_counted_loop_uses_an_index(self) -> None:
        self.assertIn("for i := 0; i < len(accounts); i++ {", self.contents)

    def test_only_the_packages_actually_used_are_imported(self) -> None:
        # Go rejects an unused import, so the block is built from what was used.
        self.assertIn('"errors"', self.contents)
        self.assertIn('"fmt"', self.contents)
        self.assertNotIn('"strings"', self.contents)

    def test_a_module_with_no_imports_emits_no_import_block(self) -> None:
        contents = generated("def f(a: int) -> int:\n    return a\n", "pkg/m.py").contents
        self.assertNotIn("import", contents)

    def test_exported_renames_are_recorded(self) -> None:
        self.assertEqual(self.result.renames.get("total"), "Total")

    def test_the_rename_is_reported(self) -> None:
        self.assertIn("CW0730", [item.code for item in self.result.diagnostics])

    def test_generation_is_deterministic(self) -> None:
        self.assertEqual(generated().contents, generated().contents)

    def test_mappings_point_back_at_the_python_source(self) -> None:
        self.assertTrue(self.result.mappings)
        self.assertEqual(self.result.mappings[0]["sourceFile"], "pkg/models.py")


class UnsupportedConstructTests(unittest.TestCase):
    def emit(self, source: str):
        return generated(source, "pkg/m.py")

    def test_try_except_is_reported_not_translated(self) -> None:
        result = self.emit(
            "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n"
        )
        self.assertIn("CW0724", [item.code for item in result.diagnostics])
        self.assertIn("CODEWEAVER-UNSUPPORTED", result.contents)

    def test_a_raise_is_reported_as_a_panic(self) -> None:
        result = self.emit("def f():\n    raise RuntimeError('x')\n")
        self.assertIn("CW0723", [item.code for item in result.diagnostics])

    def test_inheritance_embedding_is_reported(self) -> None:
        result = self.emit("class A:\n    pass\n\n\nclass B(A):\n    pass\n")
        self.assertIn("CW0733", [item.code for item in result.diagnostics])
        self.assertIn("*A", result.contents)

    def test_a_parameter_default_is_reported(self) -> None:
        result = self.emit("def f(a: int = 1) -> int:\n    return a\n")
        self.assertIn("CW0715", [item.code for item in result.diagnostics])

    def test_exponentiation_becomes_math_pow_and_is_reported(self) -> None:
        result = self.emit("def f(a: int) -> int:\n    return a ** 2\n")
        self.assertIn("CW0703", [item.code for item in result.diagnostics])
        self.assertIn("math.Pow", result.contents)

    def test_mixed_numeric_arithmetic_inserts_a_conversion(self) -> None:
        result = self.emit("def f(a: int, b: float) -> float:\n    return a * b\n")
        self.assertIn("CW0716", [item.code for item in result.diagnostics])
        self.assertIn("float64(a)", result.contents)

    def test_an_async_function_is_reported(self) -> None:
        result = self.emit("async def f() -> int:\n    return 1\n")
        self.assertIn("CW0735", [item.code for item in result.diagnostics])

    def test_a_conditional_expression_is_reported(self) -> None:
        result = self.emit("def f(c: bool, a: int, b: int) -> int:\n    return a if c else b\n")
        self.assertIn("CW0713", [item.code for item in result.diagnostics])


class BackendAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = GoBackend()

    def test_it_satisfies_the_backend_protocol(self) -> None:
        self.assertIsInstance(self.backend, Backend)

    def test_it_declares_go_as_its_toolchain(self) -> None:
        self.assertEqual(self.backend.required_toolchains, ("go",))

    def test_it_declares_the_important_limitations(self) -> None:
        joined = " ".join(self.backend.known_limitations)
        for expected in ("try/except", "virtual dispatch", "64-bit int", "capitalised"):
            self.assertIn(expected, joined)

    def test_generate_project_emits_go_files_and_a_go_mod(self) -> None:
        files = self.backend.generate_project([prepared(SOURCE)])
        self.assertEqual(sorted(files), ["go.mod", "pkg/models.go"])

    def test_generation_is_deterministic(self) -> None:
        modules = [prepared(SOURCE)]
        self.assertEqual(
            self.backend.generate_project(modules), self.backend.generate_project(modules)
        )


if __name__ == "__main__":
    unittest.main()
