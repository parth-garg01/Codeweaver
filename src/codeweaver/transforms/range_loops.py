"""Lower ``for i in range(...)`` into a counted loop.

A target that has no range object would otherwise have to materialise an array
just to iterate over indices.  Recognising the pattern in the IR keeps that
decision out of the backend and makes it testable on its own.

The pass is deliberately narrow.  It only fires when the iterable is a direct
call to a name bound to the builtin ``range`` with one to three positional
arguments and no keywords, and when the loop target is a plain name.  Anything
else keeps its ``for-each`` shape, which is always correct even if it is less
idiomatic.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.base import IRNode
from codeweaver.ir.declarations import IRModule
from codeweaver.ir.expressions import IRCall, IRLiteral, IRName
from codeweaver.ir.statements import IRForEach, IRForRange
from codeweaver.ir.types import t_int
from codeweaver.transforms.rewrite import rewrite, rewrite_all

__all__ = ["RangeLoopPass", "lower_range_loops"]


def _range_arguments(iterable: IRNode) -> list[IRNode] | None:
    """Positional arguments of a ``range(...)`` call, or ``None``."""
    if not isinstance(iterable, IRCall):
        return None
    if not isinstance(iterable.callee, IRName) or iterable.callee.name != "range":
        return None
    if iterable.keywords or iterable.star_args or iterable.star_keywords:
        return None
    if not 1 <= len(iterable.args) <= 3:
        return None
    return list(iterable.args)


def _lower(node: IRNode) -> IRNode | None:
    if not isinstance(node, IRForEach) or not isinstance(node.target, IRName):
        return None
    args = _range_arguments(node.iterable)
    if args is None:
        return None
    if node.else_body is not None and not node.else_body.is_empty:
        # for/else is already reported by the backend; rewriting the loop shape
        # here would lose the else body before that report happens.
        return None
    if len(args) == 1:
        start, stop, step = IRLiteral(0, value_type=t_int()), args[0], None
    elif len(args) == 2:
        start, stop, step = args[0], args[1], None
    else:
        start, stop, step = args[0], args[1], args[2]
    return IRForRange(
        variable=node.target.name,
        start=start,
        stop=stop,
        step=step,
        body=node.body,
        span=node.span,
        meta={**node.meta, "loweredFrom": "range"},
    )


def lower_range_loops(module: IRModule) -> IRModule:
    """Return *module* with recognised range loops lowered."""
    return replace(
        module,
        body=rewrite_all(list(module.body), _lower),
        functions=[rewrite(function, _lower) for function in module.functions],
        classes=[rewrite(klass, _lower) for klass in module.classes],
    )


@dataclass
class RangeLoopPass:
    """Transform pass wrapper for :func:`lower_range_loops`."""

    name: str = "lower-range-loops"
    description: str = "Rewrite for-each over range(...) into a counted loop"
    targets: tuple[str, ...] = ()

    def applies_to(self, module: IRModule) -> bool:
        from codeweaver.ir.base import walk

        return any(
            isinstance(node, IRForEach) and _range_arguments(node.iterable) is not None
            for node in walk(module)
        )

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        lowered = lower_range_loops(module)
        from codeweaver.transforms.rewrite import count_nodes

        count = count_nodes(lowered, "for_range")
        if count:
            bag.info(
                "CW0700",
                f"lowered {count} range loop(s) in {module.name} into counted loops",
                stage="transform",
            )
        return lowered
