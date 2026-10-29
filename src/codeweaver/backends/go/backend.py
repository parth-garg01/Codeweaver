"""The Go target backend and the module scaffolding it emits."""

from __future__ import annotations

from dataclasses import dataclass, field

from codeweaver.backends.go.module import (
    GeneratedGoFile,
    GoLayout,
    generate_module,
    output_path_for,
)
from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.declarations import IRModule

__all__ = ["GO_VERSION", "GoBackend", "render_go_mod"]

#: Language version declared in the generated ``go.mod``.  Pinned rather than
#: taken from the local toolchain so that output is reproducible.
GO_VERSION = "1.22"


def render_go_mod(module_path: str = "codeweaver/output") -> str:
    """Render ``go.mod``.

    A Go module declaration is not optional: without it ``go build`` refuses to
    resolve the generated packages against each other.
    """
    return f"module {module_path}\n\ngo {GO_VERSION}\n"


@dataclass
class GoBackend:
    """Backend adapter for Go."""

    language: str = "go"
    display_name: str = "Go 1.22+"
    file_extension: str = ".go"
    module_path: str = "codeweaver/output"
    supported_constructs: tuple[str, ...] = field(
        default_factory=lambda: (
            "packages, one per source directory, with project imports resolved",
            "package-level constants and variables",
            "functions with typed parameters and results",
            "classes as structs with pointer-receiver methods",
            "constructors as NewT functions returning a pointer",
            "property accessors as GetX/SetX methods",
            "arithmetic, bitwise, shift, logical and comparison operators",
            "slices, maps, and sets as map[T]struct{}",
            "if/else-if chains, for, counted for loops, break, continue",
            "f-strings as fmt.Sprintf",
            "membership tests via slices.Contains, strings.Contains or comma-ok",
            "assert as a guarded panic",
        )
    )
    known_limitations: tuple[str, ...] = field(
        default_factory=lambda: (
            "try/except is not translated: Go returns errors as values, and "
            "defer/recover would turn ordinary control flow into panic "
            "handling and resume somewhere else",
            "raise becomes panic, which is not how Go reports an expected failure",
            "inheritance becomes struct embedding: fields and methods are "
            "promoted, but there is no virtual dispatch, so an overridden "
            "method is not called through a base-typed value",
            "Python's arbitrary-precision int becomes a 64-bit int",
            "parameter defaults have no equivalent; callers must pass the value",
            "keyword arguments have no equivalent",
            "a conditional expression becomes an immediately called function",
            "tuples become []any, losing the element types",
            "integer division truncates toward zero rather than flooring, so "
            "it disagrees with Python on negative operands",
            "async functions become ordinary functions; Go uses goroutines",
            "exported names are capitalised, which is the only renaming "
            "CodeWeaver does; every rename is listed in the report",
        )
    )
    required_toolchains: tuple[str, ...] = field(default_factory=lambda: ("go",))

    def generate_module(
        self,
        module: IRModule,
        layout: GoLayout | None = None,
        *,
        diagnostics: DiagnosticBag | None = None,
    ) -> GeneratedGoFile:
        return generate_module(module, layout, diagnostics=diagnostics)

    def generate_project(
        self,
        modules: list[IRModule],
        *,
        project_name: str = "codeweaver-output",
        diagnostics: DiagnosticBag | None = None,
    ) -> dict[str, str]:
        layout = self.layout_for(modules)
        files = self.project_files(project_name)
        for module in sorted(modules, key=lambda item: item.name):
            generated = self.generate_module(module, layout, diagnostics=diagnostics)
            files[generated.path] = generated.contents
        return files

    def output_path(self, source_path: str) -> str:
        return output_path_for(source_path)

    def layout_for(self, modules: list[IRModule]) -> GoLayout:
        return GoLayout.for_modules(modules, module_path=self.module_path)

    def project_files(self, project_name: str) -> dict[str, str]:  # noqa: ARG002
        return {"go.mod": render_go_mod(self.module_path)}
