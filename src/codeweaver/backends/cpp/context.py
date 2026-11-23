"""Project layout and per-file emission state for the C++ backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codeweaver.backends.common.analysis import ParamMutability, is_main_guard
from codeweaver.backends.common.context import EmitContext
from codeweaver.backends.cpp.naming import safe_identifier
from codeweaver.graph.project_index import ClassInfo, ProjectIndex, ScopeEntry, ScopeKind
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule

__all__ = ["CppContext", "CppLayout", "LoopFrame", "SOURCE_ROOT"]

#: Generated sources live under this directory, which is the include root.
SOURCE_ROOT = "src"
RUNTIME_HEADER = "cw_runtime/runtime.hpp"


def _stem_path(source_path: str) -> PurePosixPath:
    pure = PurePosixPath(source_path)
    stem = "index" if pure.stem == "__init__" else pure.stem
    return pure.with_name(stem)


@dataclass
class CppLayout:
    """Where every module goes and the project-wide facts generation needs."""

    index: ProjectIndex
    #: module name -> path stem relative to the include root (no extension)
    stems: dict[str, str] = field(default_factory=dict)
    mutability: ParamMutability = field(default_factory=ParamMutability)
    #: modules with an ``if __name__ == "__main__":`` block
    entry_modules: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, modules: list[IRModule], index: ProjectIndex) -> CppLayout:
        layout = cls(index=index)
        for module in sorted(modules, key=lambda item: item.name):
            layout.stems[module.name] = str(
                _stem_path(module.path or module.name.replace(".", "/") + ".py")
            )
            if any(is_main_guard(statement) for statement in module.body):
                layout.entry_modules.append(module.name)
        layout.mutability = ParamMutability.compute(index)
        return layout

    def header(self, module: str) -> str:
        return f"{self.stems[module]}.hpp"

    def source(self, module: str) -> str:
        return f"{self.stems[module]}.cpp"

    def main_source(self, module: str) -> str:
        return f"{self.stems[module]}.main.cpp"

    @staticmethod
    def namespace(module: str) -> str:
        return "::".join(safe_identifier(part) for part in module.split(".") if part)


@dataclass
class LoopFrame:
    """A loop being emitted; ``broke`` names the flag for a ``for/else``."""

    broke: str = ""


@dataclass
class CppContext(EmitContext):
    """State for generating one module's header and source."""

    stage: str = "backend/cpp"
    layout: CppLayout | None = None
    index: ProjectIndex | None = None
    module: IRModule = field(default_factory=lambda: IRModule(""))
    klass: ClassInfo | None = None
    function: IRFunction | None = None
    #: Python's ``self``/``cls`` in the body being emitted.
    receiver: str = ""
    class_receiver: str = ""
    #: Names bound in the function being emitted.
    locals: set[str] = field(default_factory=set)
    #: ``(container, index)`` pairs proven in bounds by an enclosing loop.
    unchecked: frozenset[tuple[str, str]] = frozenset()
    loops: list[LoopFrame] = field(default_factory=list)
    _counter: int = 0
    #: True while emitting a constructor body (self is not yet owned).
    in_constructor: bool = False
    #: Loop variables read outside their loop, declared before the loop.
    escaping: set[str] = field(default_factory=set)
    #: Escaping loop variables already declared in the current function.
    declared: set[str] = field(default_factory=set)

    def temp(self, prefix: str) -> str:
        self._counter += 1
        return f"cw_{prefix}_{self._counter}"

    @property
    def namespace(self) -> str:
        return CppLayout.namespace(self.module.name)

    def scope_entry(self, name: str) -> ScopeEntry | None:
        if self.index is None or name in self.locals:
            return None
        return self.index.lookup(self.module.name, name)

    def qualified(self, module: str, name: str) -> str:
        return f"::{CppLayout.namespace(module)}::{safe_identifier(name)}"

    def entry_spelling(self, entry: ScopeEntry, local: str) -> str:
        """How a top-level name is spelled in this module."""
        if entry.module == self.module.name:
            return safe_identifier(entry.name)
        if entry.kind is ScopeKind.MODULE:
            # A namespace alias is emitted for every whole-module import.
            return safe_identifier(local)
        if entry.name == local and entry.kind in (
            ScopeKind.CLASS,
            ScopeKind.FUNCTION,
            ScopeKind.VARIABLE,
        ):
            # Imported under its own name: a using-declaration makes it visible.
            return safe_identifier(local)
        if entry.kind is ScopeKind.CLASS:
            return safe_identifier(local)  # type alias
        return self.qualified(entry.module, entry.name)

    def class_spelling(self, info: ClassInfo) -> str:
        if info.module == self.module.name:
            return safe_identifier(info.name)
        if self.index is not None:
            for local, entry in self.index.scopes.get(self.module.name, {}).items():
                if entry.kind is ScopeKind.CLASS and entry.qualified == info.qualified:
                    return safe_identifier(local)
        return self.qualified(info.module, info.name)

    def class_of(self, node_type) -> ClassInfo | None:
        if self.index is None:
            return None
        return self.index.class_for_type(node_type, self.module.name)

    def own_class(self, klass: IRClass) -> ClassInfo | None:
        if self.index is None:
            return None
        return self.index.classes.get(f"{self.module.name}.{klass.name}")
