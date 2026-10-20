from __future__ import annotations

import unittest
from dataclasses import dataclass

from codeweaver.errors import UnsupportedPairError
from codeweaver.language_registry.registry import (
    LanguagePair,
    LanguageRegistry,
    SupportLevel,
)


@dataclass
class FakeFrontend:
    language: str = "python"
    display_name: str = "Python"
    file_extensions: tuple[str, ...] = (".py",)
    supported_constructs: tuple[str, ...] = ("function",)
    known_limitations: tuple[str, ...] = ("no metaclasses",)

    def parse_module(self, source: str, path: str) -> object:
        return object()


@dataclass
class FakeBackend:
    language: str = "typescript"
    display_name: str = "TypeScript"
    file_extension: str = ".ts"
    supported_constructs: tuple[str, ...] = ("function",)
    known_limitations: tuple[str, ...] = ("no decorators",)
    required_toolchains: tuple[str, ...] = ("tsc",)

    def generate_module(self, module: object) -> object:
        return object()


def populated() -> LanguageRegistry:
    reg = LanguageRegistry()
    reg.register_frontend(FakeFrontend())
    reg.register_backend(FakeBackend())
    reg.declare_pair(LanguagePair("python", "typescript", SupportLevel.PARTIAL, "subset"))
    return reg


class SupportLevelTests(unittest.TestCase):
    def test_only_supported_and_partial_are_runnable(self) -> None:
        self.assertTrue(SupportLevel.SUPPORTED.runnable)
        self.assertTrue(SupportLevel.PARTIAL.runnable)
        self.assertFalse(SupportLevel.PLANNED.runnable)


class RegistryTests(unittest.TestCase):
    def test_empty_registry_offers_nothing(self) -> None:
        reg = LanguageRegistry()
        self.assertEqual(reg.available_pairs(), [])
        self.assertFalse(reg.is_available("python", "typescript"))

    def test_registered_pair_becomes_available(self) -> None:
        reg = populated()
        self.assertTrue(reg.is_available("python", "typescript"))
        self.assertEqual([p.key for p in reg.available_pairs()], ["python->typescript"])

    def test_planned_pair_is_declared_but_not_available(self) -> None:
        reg = populated()
        reg.declare_pair(LanguagePair("python", "go", SupportLevel.PLANNED))
        self.assertEqual(len(reg.all_pairs()), 2)
        self.assertEqual(len(reg.available_pairs()), 1)

    def test_pair_without_backend_is_not_available(self) -> None:
        reg = LanguageRegistry()
        reg.register_frontend(FakeFrontend())
        reg.declare_pair(LanguagePair("python", "typescript", SupportLevel.SUPPORTED))
        self.assertFalse(reg.is_available("python", "typescript"))

    def test_resolve_returns_components(self) -> None:
        frontend, backend, pair = populated().resolve("python", "typescript")
        self.assertEqual(frontend.language, "python")
        self.assertEqual(backend.language, "typescript")
        self.assertEqual(pair.level, SupportLevel.PARTIAL)

    def test_resolve_explains_an_undeclared_pair(self) -> None:
        with self.assertRaises(UnsupportedPairError) as ctx:
            populated().resolve("python", "rust")
        self.assertIn("no declared migration path", str(ctx.exception))

    def test_resolve_explains_a_planned_pair(self) -> None:
        reg = populated()
        reg.declare_pair(LanguagePair("python", "go", SupportLevel.PLANNED))
        with self.assertRaises(UnsupportedPairError) as ctx:
            reg.resolve("python", "go")
        self.assertIn("not usable yet", str(ctx.exception))

    def test_resolve_explains_a_missing_frontend(self) -> None:
        reg = LanguageRegistry()
        reg.register_backend(FakeBackend())
        reg.declare_pair(LanguagePair("ruby", "typescript", SupportLevel.SUPPORTED))
        with self.assertRaises(UnsupportedPairError) as ctx:
            reg.resolve("ruby", "typescript")
        self.assertIn("no registered frontend", str(ctx.exception))

    def test_language_for_extension(self) -> None:
        reg = populated()
        self.assertEqual(reg.language_for_extension(".py"), "python")
        self.assertEqual(reg.language_for_extension("py"), "python")
        self.assertIsNone(reg.language_for_extension(".rs"))

    def test_support_matrix_lists_limitations(self) -> None:
        matrix = populated().support_matrix()
        self.assertEqual(matrix["sources"][0]["knownLimitations"], ["no metaclasses"])
        self.assertEqual(matrix["targets"][0]["requiredToolchains"], ["tsc"])
        self.assertTrue(matrix["pairs"][0]["runnable"])

    def test_source_and_target_language_lists(self) -> None:
        reg = populated()
        self.assertEqual(reg.source_languages(), ["python"])
        self.assertEqual(reg.target_languages(), ["typescript"])


if __name__ == "__main__":
    unittest.main()
