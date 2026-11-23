"""Project layout and per-file emission state for the C backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codeweaver.backends.c.naming import RESERVED_WORDS, safe_identifier
from codeweaver.backends.common.analysis import ParamMutability, is_main_guard
from codeweaver.backends.common.context import EmitContext
from codeweaver.graph.project_index import ClassInfo, ProjectIndex, ScopeEntry
from codeweaver.ir.declarations import IRFunction, IRModule

__all__ = ["RUNTIME_HEADER", "SOURCE_ROOT", "CContext", "CLayout", "LoopFrame"]

SOURCE_ROOT = "src"
RUNTIME_HEADER = "cw_runtime/cw.h"


def _stem(module: IRModule) -> str:
    pure = PurePosixPath(module.path or module.name.replace(".", "/") + ".py")
    stem = "index" if pure.stem == "__init__" else pure.stem
    return str(pure.with_name(stem))


@dataclass
class CLayout:
    index: ProjectIndex
    stems: dict[str, str] = field(default_factory=dict)
    #: qualified Python name -> C symbol, for every top-level name
    symbols: dict[str, str] = field(default_factory=dict)
    entry_modules: list[str] = field(default_factory=list)
    mutability: ParamMutability = field(default_factory=ParamMutability)

    @classmethod
    def build(cls, modules: list[IRModule], index: ProjectIndex) -> CLayout:
        layout = cls(index=index)
        owners: dict[str, list[str]] = {}
        for module in sorted(modules, key=lambda item: item.name):
            layout.stems[module.name] = _stem(module)
            names = [klass.name for klass in module.classes] + [
                function.name for function in module.functions
            ]
            names += [statement.name for statement in module.body if statement.kind == "var_decl"]  # type: ignore[attr-defined]
            for name in names:
                owners.setdefault(name, []).append(module.name)
            if any(is_main_guard(statement) for statement in module.body):
                layout.entry_modules.append(module.name)
        for name, modules_with_name in owners.items():
            for owner in modules_with_name:
                plain = safe_identifier(name)
                if len(modules_with_name) == 1 and plain == name and name not in RESERVED_WORDS:
                    symbol = name
                else:
                    prefix = layout.stems[owner].replace("/", "_")
                    symbol = f"{prefix}_{name.strip('_') or 'x'}"
                layout.symbols[f"{owner}.{name}"] = symbol
        layout.mutability = ParamMutability.compute(index)
        return layout

    def header(self, module: str) -> str:
        return f"{self.stems[module]}.h"

    def source(self, module: str) -> str:
        return f"{self.stems[module]}.c"

    def main_source(self, module: str) -> str:
        return f"{self.stems[module]}.main.c"

    def guard(self, module: str) -> str:
        cleaned = "".join(char if char.isalnum() else "_" for char in self.stems[module]).upper()
        return f"CW_{cleaned}_H"


@dataclass
class LoopFrame:
    broke: str = ""


@dataclass
class CContext(EmitContext):
    stage: str = "backend/c"
    layout: CLayout | None = None
    index: ProjectIndex | None = None
    module: IRModule = field(default_factory=lambda: IRModule(""))
    klass: ClassInfo | None = None
    function: IRFunction | None = None
    receiver: str = ""
    locals: set[str] = field(default_factory=set)
    unchecked: frozenset[tuple[str, str]] = frozenset()
    loops: list[LoopFrame] = field(default_factory=list)
    escaping: set[str] = field(default_factory=set)
    declared: set[str] = field(default_factory=set)
    pre: list[str] = field(default_factory=list)
    signature_reports: set[tuple[int, str]] = field(default_factory=set)
    _counter: int = 0

    def temp(self, prefix: str) -> str:
        self._counter += 1
        return f"cw_{prefix}{self._counter}"

    def scope_entry(self, name: str) -> ScopeEntry | None:
        if self.index is None or name in self.locals:
            return None
        return self.index.lookup(self.module.name, name)

    def symbol(self, module: str, name: str) -> str:
        if self.layout is None:
            return safe_identifier(name)
        return self.layout.symbols.get(f"{module}.{name}", safe_identifier(name))

    def class_symbol(self, info: ClassInfo) -> str:
        return self.symbol(info.module, info.name)

    def class_of(self, node_type) -> ClassInfo | None:
        if self.index is None:
            return None
        return self.index.class_for_type(node_type, self.module.name)
