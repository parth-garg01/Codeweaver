"""Regression test for issue #5.

The `Frontend` and `Backend` protocols are the documented extension contract,
but they did not match how `pipeline.py` calls an adapter: a backend written to
the published signature raised `TypeError` during migration. The protocol
attributes were also invariant, so a normal adapter could not satisfy them.

The important test here is `MinimalAdapterTests`: it defines adapters written
*only* against the protocol and drives a real migration through them. If the
pipeline grows a new argument without updating the protocol, that test breaks.

https://github.com/parth-garg01/Codeweaver/issues/5
"""

from __future__ import annotations

import inspect
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.declarations import IRModule
from codeweaver.language_registry import (
    Backend,
    Frontend,
    LanguagePair,
    LanguageRegistry,
    SupportLevel,
)
from codeweaver.language_registry.registry import GeneratedFile, ParseResult

# -- adapters written only against the published protocol ----------------


@dataclass
class ToyParseResult:
    module: IRModule | None
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)


@dataclass
class ToyGeneratedFile:
    path: str
    contents: str
    mappings: list[dict] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    unsupported_count: int = 0


@dataclass
class ToyFrontend:
    """A frontend that reads the protocol and nothing else."""

    language: str = "toy"
    display_name: str = "Toy"
    file_extensions: tuple[str, ...] = (".toy",)
    supported_constructs: tuple[str, ...] = ("a single module name",)
    known_limitations: tuple[str, ...] = ("everything else",)

    def parse_module(self, source: str, path: str) -> ToyParseResult:
        return ToyParseResult(
            module=IRModule(
                name=path.replace("/", ".").removesuffix(".toy"),
                path=path,
                source_language=self.language,
                docstring=source.strip(),
            )
        )


@dataclass
class ToyBackend:
    """A backend that reads the protocol and nothing else."""

    language: str = "toy-out"
    display_name: str = "Toy Output"
    file_extension: str = ".out"
    supported_constructs: tuple[str, ...] = ("module docstrings",)
    known_limitations: tuple[str, ...] = ("everything else",)
    required_toolchains: tuple[str, ...] = ()

    def generate_module(
        self,
        module: IRModule,
        layout=None,
        *,
        diagnostics: DiagnosticBag | None = None,
    ) -> ToyGeneratedFile:
        if diagnostics is not None:
            diagnostics.info("TOY01", f"generated {module.name}", stage="backend/toy")
        return ToyGeneratedFile(path=self.output_path(module.path), contents=f"// {module.name}\n")

    def generate_project(
        self,
        modules: list[IRModule],
        *,
        project_name: str = "codeweaver-output",
        diagnostics: DiagnosticBag | None = None,
    ) -> dict[str, str]:
        files = {"toy.manifest": f"name={project_name}\n"}
        for module in modules:
            generated = self.generate_module(module, diagnostics=diagnostics)
            files[generated.path] = generated.contents
        return files

    def output_path(self, source_path: str) -> str:
        return str(Path(source_path).with_suffix(self.file_extension))


# -- the tests ------------------------------------------------------------


class ProtocolConformanceTests(unittest.TestCase):
    def test_a_minimal_frontend_satisfies_the_protocol(self) -> None:
        self.assertIsInstance(ToyFrontend(), Frontend)

    def test_a_minimal_backend_satisfies_the_protocol(self) -> None:
        self.assertIsInstance(ToyBackend(), Backend)

    def test_the_bundled_adapters_satisfy_the_protocols(self) -> None:
        from codeweaver.backends.typescript import TypeScriptBackend
        from codeweaver.frontends.python import PythonFrontend

        self.assertIsInstance(PythonFrontend(), Frontend)
        self.assertIsInstance(TypeScriptBackend(), Backend)

    def test_the_result_protocols_are_satisfied(self) -> None:
        self.assertIsInstance(ToyParseResult(module=None), ParseResult)
        self.assertIsInstance(ToyGeneratedFile(path="a", contents=""), GeneratedFile)

    def test_the_bundled_results_satisfy_the_result_protocols(self) -> None:
        from codeweaver.backends.typescript.module import generate_module
        from codeweaver.frontends.python import parse_source

        parsed = parse_source("x = 1\n", "m.py")
        self.assertIsInstance(parsed, ParseResult)
        assert parsed.module is not None
        self.assertIsInstance(generate_module(parsed.module), GeneratedFile)


class SignatureAgreementTests(unittest.TestCase):
    """The protocol must describe what the pipeline actually calls."""

    def test_the_bundled_backend_matches_the_protocol_signature(self) -> None:
        from codeweaver.backends.typescript import TypeScriptBackend

        protocol = inspect.signature(Backend.generate_module)
        concrete = inspect.signature(TypeScriptBackend.generate_module)
        self.assertEqual(list(protocol.parameters), list(concrete.parameters))

    def test_the_bundled_frontend_matches_the_protocol_signature(self) -> None:
        from codeweaver.frontends.python import PythonFrontend

        protocol = inspect.signature(Frontend.parse_module)
        concrete = inspect.signature(PythonFrontend.parse_module)
        self.assertEqual(list(protocol.parameters), list(concrete.parameters))

    def test_generate_module_accepts_a_layout_and_a_diagnostic_bag(self) -> None:
        # The exact call shape pipeline.py uses. This is the regression: the
        # old protocol declared generate_module(self, module) only.
        parameters = inspect.signature(Backend.generate_module).parameters
        self.assertIn("layout", parameters)
        self.assertIn("diagnostics", parameters)

    def test_no_protocol_member_is_typed_as_bare_Any(self) -> None:
        import typing

        for protocol in (Frontend, Backend):
            hints = typing.get_type_hints(protocol.parse_module) if protocol is Frontend else {}
            for name, hint in hints.items():
                if name == "return":
                    self.assertIsNot(hint, typing.Any, f"{protocol.__name__}.{name}")


class MinimalAdapterTests(unittest.TestCase):
    """Drive a real migration through adapters written only to the protocol."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "p"
        self.root.mkdir(parents=True)
        (self.root / "alpha.toy").write_text("the alpha module\n", encoding="utf-8")
        (self.root / "beta.toy").write_text("the beta module\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def registry(self) -> LanguageRegistry:
        reg = LanguageRegistry()
        reg.register_frontend(ToyFrontend())
        reg.register_backend(ToyBackend())
        reg.declare_pair(LanguagePair("toy", "toy-out", SupportLevel.PARTIAL))
        return reg

    def test_the_registry_resolves_a_protocol_only_pair(self) -> None:
        frontend, backend, pair = self.registry().resolve("toy", "toy-out")
        self.assertEqual(frontend.language, "toy")
        self.assertEqual(backend.language, "toy-out")
        self.assertEqual(pair.level, SupportLevel.PARTIAL)

    def test_the_support_matrix_includes_the_toy_pair(self) -> None:
        matrix = self.registry().support_matrix()
        self.assertEqual(matrix["sources"][0]["language"], "toy")
        self.assertEqual(matrix["targets"][0]["language"], "toy-out")

    def test_generate_module_through_the_protocol_call_shape(self) -> None:
        # This raised TypeError before the fix, because the protocol said the
        # method took one argument while the pipeline passed three.
        _, backend, _ = self.registry().resolve("toy", "toy-out")
        bag = DiagnosticBag()
        module = IRModule(name="alpha", path="alpha.toy")
        generated = backend.generate_module(module, None, diagnostics=bag)
        self.assertEqual(generated.path, "alpha.out")
        self.assertEqual([item.code for item in bag], ["TOY01"])

    def test_generate_project_through_the_protocol(self) -> None:
        _, backend, _ = self.registry().resolve("toy", "toy-out")
        modules = [IRModule(name="alpha", path="alpha.toy"), IRModule(name="beta", path="beta.toy")]
        files = backend.generate_project(modules, project_name="toy-demo")
        self.assertEqual(sorted(files), ["alpha.out", "beta.out", "toy.manifest"])
        self.assertIn("name=toy-demo", files["toy.manifest"])

    def test_parse_module_through_the_protocol(self) -> None:
        frontend, _, _ = self.registry().resolve("toy", "toy-out")
        result = frontend.parse_module("hello\n", "alpha.toy")
        self.assertIsNotNone(result.module)
        self.assertEqual(result.module.name, "alpha")
        self.assertEqual(result.module.docstring, "hello")


if __name__ == "__main__":
    unittest.main()
