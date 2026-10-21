from __future__ import annotations

import json
import unittest

from codeweaver.backends.javascript import JavaScriptBackend, render_package_json
from codeweaver.backends.typescript import TypeScriptBackend
from codeweaver.backends.typescript.module import (
    JAVASCRIPT,
    TYPESCRIPT,
    ModuleLayout,
    generate_module,
    output_path_for,
)
from codeweaver.frontends.python import parse_source
from codeweaver.language_registry import Backend
from codeweaver.transforms import default_pipeline
from codeweaver.transforms.inheritance import collect_inherited_fields

CLASS_SOURCE = (
    "class Account:\n"
    "    rate: float = 0.02\n"
    "\n"
    "    def __init__(self, owner: str, balance: int = 0) -> None:\n"
    "        self._owner = owner\n"
    "        self.balance = balance\n"
    "\n"
    "    @property\n"
    "    def owner(self) -> str:\n"
    "        return self._owner\n"
    "\n"
    "    @owner.setter\n"
    "    def owner(self, value: str) -> None:\n"
    "        self._owner = value\n"
    "\n"
    "    def deposit(self, amount: int) -> None:\n"
    "        self.balance += amount\n"
    "\n"
    "\n"
    "VERSION: str = '1.0'\n"
    "\n"
    "\n"
    "async def fetch(url: str) -> int:\n"
    "    total: int = 0\n"
    "    return total\n"
)


def prepared(source: str, target: str):
    result = parse_source(source, "m.py")
    assert result.module is not None, [d.format() for d in result.diagnostics]
    inherited = collect_inherited_fields([result.module])
    return default_pipeline(target, inherited_fields=inherited).run(result.module).module


def javascript(source: str = CLASS_SOURCE) -> str:
    return generate_module(prepared(source, "javascript"), dialect=JAVASCRIPT).contents


def typescript(source: str = CLASS_SOURCE) -> str:
    return generate_module(prepared(source, "typescript"), dialect=TYPESCRIPT).contents


class DialectTests(unittest.TestCase):
    def test_the_two_dialects_differ_in_extension_and_types(self) -> None:
        self.assertEqual((TYPESCRIPT.extension, TYPESCRIPT.emit_types), (".ts", True))
        self.assertEqual((JAVASCRIPT.extension, JAVASCRIPT.emit_types), (".js", False))

    def test_output_paths_follow_the_dialect(self) -> None:
        self.assertEqual(output_path_for("pkg/m.py"), "pkg/m.ts")
        self.assertEqual(output_path_for("pkg/m.py", extension=".js"), "pkg/m.js")

    def test_package_init_becomes_index_in_both_dialects(self) -> None:
        self.assertEqual(output_path_for("pkg/__init__.py", extension=".js"), "pkg/index.js")

    def test_the_layout_follows_the_dialect(self) -> None:
        module = prepared("x = 1\n", "javascript")
        layout = ModuleLayout.for_modules([module], dialect=JAVASCRIPT)
        self.assertEqual(list(layout.paths.values()), ["m.js"])


class TypeErasureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.js = javascript()
        self.ts = typescript()

    def test_no_colon_annotation_survives_in_javascript(self) -> None:
        for leaked in (": string", ": number", ": void", ": boolean", ": unknown"):
            self.assertNotIn(leaked, self.js, leaked)

    def test_typescript_still_carries_the_annotations(self) -> None:
        self.assertIn(": string", self.ts)
        self.assertIn(": number", self.ts)

    def test_parameters_lose_their_types(self) -> None:
        self.assertIn("deposit(amount) {", self.js)
        self.assertIn("deposit(amount: number): void {", self.ts)

    def test_defaults_survive_type_erasure(self) -> None:
        self.assertIn("constructor(owner, balance = 0) {", self.js)

    def test_fields_are_declared_without_types(self) -> None:
        self.assertIn("rate = 0.02;", self.js)
        self.assertIn("balance;", self.js)

    def test_module_constants_keep_their_value(self) -> None:
        self.assertIn('export const VERSION = "1.0";', self.js)
        self.assertIn('export const VERSION: string = "1.0";', self.ts)

    def test_local_declarations_lose_their_types(self) -> None:
        self.assertIn("let total = 0;", self.js)

    def test_async_functions_lose_the_promise_wrapper(self) -> None:
        self.assertIn("export async function fetch(url) {", self.js)
        self.assertIn("Promise<number>", self.ts)

    def test_accessors_survive_in_both_dialects(self) -> None:
        for contents in (self.js, self.ts):
            self.assertIn("get owner()", contents)
            self.assertIn("set owner(value", contents)

    def test_the_getter_return_type_is_erased(self) -> None:
        self.assertIn("get owner() {", self.js)
        self.assertIn("get owner(): string {", self.ts)

    def test_statements_are_identical_between_dialects(self) -> None:
        # The dialects share a statement emitter; only annotations differ.
        self.assertIn("this.balance += amount;", self.js)
        self.assertIn("this.balance += amount;", self.ts)


class VisibilityTests(unittest.TestCase):
    SOURCE = (
        "class C:\n"
        "    _protected: int = 1\n"
        "    __private: int = 2\n"
        "\n"
        "    def _helper(self) -> None:\n"
        "        pass\n"
    )

    def test_javascript_has_no_visibility_keywords(self) -> None:
        import re

        contents = javascript(self.SOURCE)
        # Matched on a word boundary: "__private = 2;" contains "private " as a
        # substring but is a field name, not a keyword.
        self.assertIsNone(re.search(r"\bprivate\s", contents), contents)
        self.assertIsNone(re.search(r"\bprotected\s", contents), contents)

    def test_typescript_keeps_them(self) -> None:
        contents = typescript(self.SOURCE)
        self.assertIn("protected _protected", contents)

    def test_an_abstract_member_is_reported_in_javascript(self) -> None:
        source = "class C:\n    @abstractmethod\n    def f(self) -> int:\n        ...\n"
        generated = generate_module(prepared(source, "javascript"), dialect=JAVASCRIPT)
        self.assertIn("CW0644", [item.code for item in generated.diagnostics])
        self.assertNotIn("abstract ", generated.contents)


class PackageJsonTests(unittest.TestCase):
    def test_type_module_is_set(self) -> None:
        payload = json.loads(render_package_json("demo"))
        self.assertEqual(payload["type"], "module")

    def test_there_is_no_typecheck_script(self) -> None:
        payload = json.loads(render_package_json("demo"))
        self.assertNotIn("typecheck", payload["scripts"])
        self.assertIn("check", payload["scripts"])

    def test_output_is_deterministic(self) -> None:
        self.assertEqual(render_package_json("demo"), render_package_json("demo"))


class BackendAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = JavaScriptBackend()

    def test_it_satisfies_the_backend_protocol(self) -> None:
        self.assertIsInstance(self.backend, Backend)

    def test_it_declares_node_as_its_toolchain(self) -> None:
        self.assertEqual(self.backend.required_toolchains, ("node",))

    def test_it_declares_the_weaker_validation_as_a_limitation(self) -> None:
        joined = " ".join(self.backend.known_limitations)
        self.assertIn("syntax check only", joined)
        self.assertIn("type annotations are erased", joined)

    def test_output_paths_use_the_js_extension(self) -> None:
        self.assertEqual(self.backend.output_path("a/b.py"), "a/b.js")

    def test_generate_project_emits_js_and_a_manifest(self) -> None:
        modules = [
            prepared("def helper():\n    pass\n", "javascript"),
        ]
        files = self.backend.generate_project(modules, project_name="demo")
        self.assertEqual(sorted(files), ["m.js", "package.json"])

    def test_no_tsconfig_is_emitted(self) -> None:
        files = self.backend.generate_project([prepared("x = 1\n", "javascript")])
        self.assertNotIn("tsconfig.json", files)

    def test_generation_is_deterministic(self) -> None:
        modules = [prepared(CLASS_SOURCE, "javascript")]
        self.assertEqual(
            self.backend.generate_project(modules), self.backend.generate_project(modules)
        )

    def test_both_backends_expose_the_same_protocol_surface(self) -> None:
        for name in ("layout_for", "project_files", "output_path", "generate_project"):
            self.assertTrue(hasattr(JavaScriptBackend(), name), name)
            self.assertTrue(hasattr(TypeScriptBackend(), name), name)


if __name__ == "__main__":
    unittest.main()
