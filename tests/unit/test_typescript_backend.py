from __future__ import annotations

import json
import unittest

from codeweaver.backends.typescript import (
    TypeScriptBackend,
    project_files,
    render_package_json,
    render_tsconfig,
)
from codeweaver.frontends.python import parse_source
from codeweaver.language_registry import LanguageRegistry, SupportLevel


def ir_module(source: str, path: str):
    result = parse_source(source, path)
    assert result.module is not None
    return result.module


class TsconfigTests(unittest.TestCase):
    def test_strict_mode_is_on_by_default(self) -> None:
        config = json.loads(render_tsconfig())
        self.assertTrue(config["compilerOptions"]["strict"])
        self.assertTrue(config["compilerOptions"]["noImplicitAny"])

    def test_strict_mode_can_be_relaxed(self) -> None:
        config = json.loads(render_tsconfig(strict=False))
        self.assertFalse(config["compilerOptions"]["strict"])

    def test_output_is_deterministic_and_sorted(self) -> None:
        self.assertEqual(render_tsconfig(), render_tsconfig())
        text = render_tsconfig()
        self.assertLess(text.index('"compilerOptions"'), text.index('"include"'))

    def test_type_checking_does_not_emit_files(self) -> None:
        self.assertTrue(json.loads(render_tsconfig())["compilerOptions"]["noEmit"])


class PackageJsonTests(unittest.TestCase):
    def test_name_and_defaults(self) -> None:
        payload = json.loads(render_package_json("demo"))
        self.assertEqual(payload["name"], "demo")
        self.assertTrue(payload["private"])
        self.assertEqual(payload["type"], "module")

    def test_empty_name_falls_back(self) -> None:
        self.assertEqual(json.loads(render_package_json(""))["name"], "codeweaver-output")

    def test_typecheck_script_is_provided(self) -> None:
        payload = json.loads(render_package_json("demo"))
        self.assertEqual(payload["scripts"]["typecheck"], "tsc --noEmit")

    def test_description_warns_about_review(self) -> None:
        self.assertIn("Review before use", json.loads(render_package_json("d"))["description"])


class ProjectFileTests(unittest.TestCase):
    def test_both_manifests_are_produced(self) -> None:
        files = project_files("demo")
        self.assertEqual(sorted(files), ["package.json", "tsconfig.json"])


class BackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = TypeScriptBackend()

    def test_declares_its_language_and_toolchain(self) -> None:
        self.assertEqual(self.backend.language, "typescript")
        self.assertEqual(self.backend.file_extension, ".ts")
        self.assertEqual(self.backend.required_toolchains, ("tsc",))

    def test_limitations_are_declared(self) -> None:
        self.assertTrue(any("keyword arguments" in item for item in self.backend.known_limitations))
        self.assertTrue(any("IEEE-754" in item for item in self.backend.known_limitations))

    def test_output_path(self) -> None:
        self.assertEqual(self.backend.output_path("a/b.py"), "a/b.ts")

    def test_generate_project_emits_sources_and_manifests(self) -> None:
        modules = [
            ir_module("def helper():\n    pass\n", "pkg/util.py"),
            ir_module("from pkg.util import helper\n", "pkg/app.py"),
        ]
        files = self.backend.generate_project(modules, project_name="demo")
        self.assertEqual(
            sorted(files),
            ["package.json", "pkg/app.ts", "pkg/util.ts", "tsconfig.json"],
        )
        self.assertIn('from "./util"', files["pkg/app.ts"])

    def test_generate_project_is_deterministic(self) -> None:
        modules = [ir_module("def f():\n    pass\n", "a.py")]
        self.assertEqual(
            self.backend.generate_project(modules),
            self.backend.generate_project(modules),
        )

    def test_cycles_do_not_prevent_import_resolution(self) -> None:
        modules = [
            ir_module("from b import g\n\ndef f():\n    g()\n", "a.py"),
            ir_module("from a import f\n\ndef g():\n    pass\n", "b.py"),
        ]
        files = self.backend.generate_project(modules)
        self.assertIn('from "./b"', files["a.ts"])
        self.assertIn('from "./a"', files["b.ts"])

    def test_empty_project_still_gets_manifests(self) -> None:
        files = self.backend.generate_project([])
        self.assertEqual(sorted(files), ["package.json", "tsconfig.json"])


class RegistrationTests(unittest.TestCase):
    def test_register_all_declares_the_python_typescript_pair(self) -> None:
        from codeweaver.adapters import register_all

        reg = register_all(LanguageRegistry())
        self.assertTrue(reg.is_available("python", "typescript"))
        pair = reg.find_pair("python", "typescript")
        self.assertEqual(pair.level, SupportLevel.PARTIAL)
        self.assertIn("Comprehensions", pair.notes)

    def test_global_registry_is_populated_on_import(self) -> None:
        import codeweaver.adapters  # noqa: F401
        from codeweaver.language_registry import registry

        self.assertIn("python->typescript", {p.key for p in registry.available_pairs()})

    def test_support_matrix_lists_both_sides(self) -> None:
        from codeweaver.adapters import register_all

        matrix = register_all(LanguageRegistry()).support_matrix()
        self.assertEqual(matrix["sources"][0]["language"], "python")
        self.assertEqual(matrix["targets"][0]["language"], "typescript")


if __name__ == "__main__":
    unittest.main()
