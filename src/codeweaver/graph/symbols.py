"""The symbol index.

The index answers "what does this project define, and where?".  It is built
from IR modules only, so it can never disagree with what the frontend actually
produced.  Every symbol carries the source span it came from, which is what
lets a diagnostic about a dependency point at a real line of code.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import SourceSpan
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule
from codeweaver.ir.statements import IRVarDecl

__all__ = ["Symbol", "SymbolIndex", "SymbolKind", "build_symbol_index"]


class SymbolKind(enum.StrEnum):
    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    FIELD = "field"
    VARIABLE = "variable"


@dataclass(frozen=True, slots=True)
class Symbol:
    """One named thing a project defines."""

    #: Fully qualified, dotted: ``pkg.mod.Account.deposit``.
    id: str
    name: str
    kind: SymbolKind
    module: str
    span: SourceSpan | None = None
    #: ``False`` for names a target should keep module-private.
    exported: bool = True
    #: For methods and fields: the owning class's qualified id.
    owner: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "module": self.module,
            "span": self.span.to_dict() if self.span else None,
            "exported": self.exported,
            "owner": self.owner,
        }


@dataclass
class SymbolIndex:
    """All symbols in a project, queryable by id, name and module."""

    symbols: dict[str, Symbol] = field(default_factory=dict)
    _by_name: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _by_module: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def add(self, symbol: Symbol) -> Symbol:
        self.symbols[symbol.id] = symbol
        self._by_name.setdefault(symbol.name, []).append(symbol.id)
        self._by_module.setdefault(symbol.module, []).append(symbol.id)
        return symbol

    def get(self, symbol_id: str) -> Symbol | None:
        return self.symbols.get(symbol_id)

    def by_name(self, name: str) -> list[Symbol]:
        return [self.symbols[item] for item in self._by_name.get(name, [])]

    def in_module(self, module: str) -> list[Symbol]:
        return [self.symbols[item] for item in self._by_module.get(module, [])]

    def modules(self) -> list[str]:
        return sorted(
            symbol.id for symbol in self.symbols.values() if symbol.kind is SymbolKind.MODULE
        )

    def top_level_of(self, module: str) -> list[Symbol]:
        """Symbols declared directly in *module*, excluding the module itself."""
        return [
            symbol
            for symbol in self.in_module(module)
            if symbol.kind is not SymbolKind.MODULE and not symbol.owner
        ]

    def resolve_in_module(self, module: str, name: str) -> Symbol | None:
        """Find *name* as declared by *module*."""
        return self.get(f"{module}.{name}") if module else self.get(name)

    def members_of(self, class_id: str) -> list[Symbol]:
        return sorted(
            (symbol for symbol in self.symbols.values() if symbol.owner == class_id),
            key=lambda symbol: symbol.id,
        )

    def __len__(self) -> int:
        return len(self.symbols)

    def __contains__(self, symbol_id: object) -> bool:
        return symbol_id in self.symbols

    def to_dict(self) -> dict[str, Any]:
        return {"symbols": [self.symbols[key].to_dict() for key in sorted(self.symbols)]}


def build_symbol_index(modules: list[IRModule]) -> SymbolIndex:
    """Build an index from IR modules, in a deterministic order."""
    index = SymbolIndex()
    for module in sorted(modules, key=lambda item: item.name):
        index.add(
            Symbol(
                id=module.name,
                name=module.name.rsplit(".", 1)[-1],
                kind=SymbolKind.MODULE,
                module=module.name,
                span=module.span,
            )
        )
        for function in module.functions:
            _add_function(index, module.name, function)
        for klass in module.classes:
            _add_class(index, module.name, klass)
        for statement in module.body:
            if isinstance(statement, IRVarDecl) and statement.name:
                index.add(
                    Symbol(
                        id=f"{module.name}.{statement.name}",
                        name=statement.name,
                        kind=SymbolKind.VARIABLE,
                        module=module.name,
                        span=statement.span,
                        exported=not statement.name.startswith("_"),
                    )
                )
    return index


def _add_function(index: SymbolIndex, module: str, function: IRFunction) -> None:
    index.add(
        Symbol(
            id=f"{module}.{function.name}",
            name=function.name,
            kind=SymbolKind.FUNCTION,
            module=module,
            span=function.span,
            exported=function.visibility == "public",
        )
    )


def _add_class(index: SymbolIndex, module: str, klass: IRClass) -> None:
    class_id = f"{module}.{klass.name}"
    index.add(
        Symbol(
            id=class_id,
            name=klass.name,
            kind=SymbolKind.CLASS,
            module=module,
            span=klass.span,
            exported=klass.visibility == "public",
        )
    )
    for method in klass.methods:
        index.add(
            Symbol(
                id=f"{class_id}.{method.name}",
                name=method.name,
                kind=SymbolKind.METHOD,
                module=module,
                span=method.span,
                exported=method.visibility == "public",
                owner=class_id,
            )
        )
    for item in klass.fields:
        index.add(
            Symbol(
                id=f"{class_id}.{item.name}",
                name=item.name,
                kind=SymbolKind.FIELD,
                module=module,
                span=item.span,
                exported=item.visibility == "public",
                owner=class_id,
            )
        )
