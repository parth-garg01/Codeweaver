"""Mark the calls that construct a class.

``Account("a")`` is a call in Python and a construction in JavaScript, and the
two are not interchangeable: ``Account("a")`` without ``new`` is a type error in
TypeScript and a runtime error in JavaScript.  The backend cannot tell a
constructor call from an ordinary one by looking at the expression, so this pass
marks the ones it can prove.

What it can prove is limited and the limits are deliberate:

* a call whose callee is a bare name that the project declares as a class;
* in the module where the class is declared, or in a module that imports that
  name.

It does not guess from capitalisation, and it does not mark a call through an
attribute or a variable, because a false positive would emit ``new`` on a plain
function and break code that was previously correct.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.graph.dependency import resolve_relative_import
from codeweaver.graph.symbols import SymbolIndex, SymbolKind
from codeweaver.ir.base import CONSTRUCTS_CLASS, IRNode
from codeweaver.ir.declarations import IRModule
from codeweaver.ir.expressions import IRCall, IRName
from codeweaver.transforms.rewrite import rewrite

__all__ = ["CONSTRUCTS_META_KEY", "ClassConstructionPass", "class_names_visible_in"]

#: Metadata key the backend reads to decide whether to emit ``new``.
CONSTRUCTS_META_KEY = CONSTRUCTS_CLASS


def class_names_visible_in(module: IRModule, symbols: SymbolIndex) -> frozenset[str]:
    """Class names a bare reference in *module* could resolve to."""
    visible: set[str] = {klass.name for klass in module.classes}
    for symbol in symbols.in_module(module.name):
        if symbol.kind is SymbolKind.CLASS and not symbol.owner:
            visible.add(symbol.name)
    for imported in module.imports:
        target = resolve_relative_import(module.name, imported.module, imported.level)
        for name in imported.names:
            found = symbols.resolve_in_module(target, name.name)
            if found is not None and found.kind is SymbolKind.CLASS:
                visible.add(name.local_name)
    return frozenset(visible)


def _marker(class_names: frozenset[str]):
    def rewriter(node: IRNode) -> IRNode | None:
        if (
            isinstance(node, IRCall)
            and isinstance(node.callee, IRName)
            and node.callee.name in class_names
            and not node.meta.get(CONSTRUCTS_META_KEY)
        ):
            return replace(node, meta={**node.meta, CONSTRUCTS_META_KEY: True})
        return None

    return rewriter


def mark_class_construction(module: IRModule, class_names: frozenset[str]) -> IRModule:
    """Return *module* with constructor calls marked."""
    if not class_names:
        return module
    rewriter = _marker(class_names)
    return replace(
        module,
        body=[rewrite(statement, rewriter) for statement in module.body],
        functions=[rewrite(function, rewriter) for function in module.functions],
        classes=[rewrite(klass, rewriter) for klass in module.classes],
    )


@dataclass
class ClassConstructionPass:
    """Transform pass wrapper for :func:`mark_class_construction`.

    The pass needs project-wide knowledge, so it is constructed with the class
    names it should recognise rather than deriving them from one module.
    """

    class_names: frozenset[str] = frozenset()
    name: str = "mark-class-construction"
    description: str = "Mark calls that construct a project class"
    targets: tuple[str, ...] = ()

    @classmethod
    def for_module(cls, module: IRModule, symbols: SymbolIndex) -> ClassConstructionPass:
        return cls(class_names=class_names_visible_in(module, symbols))

    def applies_to(self, module: IRModule) -> bool:
        if not self.class_names:
            return False
        from codeweaver.ir.base import walk

        return any(
            isinstance(node, IRCall)
            and isinstance(node.callee, IRName)
            and node.callee.name in self.class_names
            for node in walk(module)
        )

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:  # noqa: ARG002
        return mark_class_construction(module, self.class_names)
