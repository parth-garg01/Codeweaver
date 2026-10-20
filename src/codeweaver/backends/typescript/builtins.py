"""Python builtin calls to TypeScript.

Only builtins with a faithful JavaScript counterpart are mapped.  Where the
semantics differ the mapping is still emitted — because leaving ``len(xs)`` in
the output guarantees a compile error that says nothing useful — but the
difference is reported so it reaches the migration report.

Deliberately *not* mapped: ``enumerate``, ``zip``, ``map``, ``filter``,
``any``, ``all`` over lazy iterables, and anything that depends on Python's
iterator protocol.  An approximation of those would be wrong in ways a type
checker cannot catch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from codeweaver.backends.typescript.context import EmitContext
from codeweaver.ir.expressions import IRCall, IRName
from codeweaver.ir.types import IRType, TypeKind

__all__ = ["BUILTIN_NAMES", "UNMAPPED_BUILTINS", "map_builtin_call"]

#: Builtins that have no safe TypeScript equivalent in this build.
UNMAPPED_BUILTINS: dict[str, str] = {
    "enumerate": "iterate with an index loop, or use Array.prototype.entries()",
    "zip": "index both sequences explicitly",
    "map": "use Array.prototype.map on the sequence",
    "filter": "use Array.prototype.filter on the sequence",
    "reversed": "use [...xs].reverse(), which copies",
    "iter": "JavaScript iterators are not modelled",
    "next": "JavaScript iterators are not modelled",
    "getattr": "dynamic attribute access is not modelled",
    "setattr": "dynamic attribute access is not modelled",
    "hasattr": "dynamic attribute access is not modelled",
    "eval": "dynamic evaluation is deliberately not generated",
    "exec": "dynamic evaluation is deliberately not generated",
    "globals": "module globals are not reified",
    "locals": "local scopes are not reified",
    "super": "super is handled only in a constructor or method call position",
}


@dataclass(frozen=True, slots=True)
class _Mapping:
    """One builtin's translation."""

    render: Callable[[list[str], IRCall, EmitContext], str]
    arity: tuple[int, int]
    note: str = ""


def _simple(template: str, arity: tuple[int, int] = (1, 1), note: str = "") -> _Mapping:
    def render(args: list[str], call: IRCall, ctx: EmitContext) -> str:  # noqa: ARG001
        return template.format(*args)

    return _Mapping(render, arity, note)


def _length(args: list[str], call: IRCall, ctx: EmitContext) -> str:
    """``len(x)`` depends on what ``x`` is, so the type decides the spelling."""
    argument = call.args[0] if call.args else None
    value_type: IRType | None = getattr(argument, "value_type", None)
    name = value_type.name if value_type else ""
    if name == "dict":
        return f"Object.keys({args[0]}).length"
    if name == "set":
        return f"{args[0]}.size"
    if name not in ("list", "str", "tuple"):
        ctx.warn(
            "CW0660",
            "len() assumes a value with a .length property",
            span=call.span,
            hint="annotate the argument so the right form can be generated",
        )
    return f"{args[0]}.length"


def _sum(args: list[str], call: IRCall, ctx: EmitContext) -> str:  # noqa: ARG001
    return f"{args[0]}.reduce((acc: number, item: number) => acc + item, 0)"


def _sorted(args: list[str], call: IRCall, ctx: EmitContext) -> str:
    ctx.warn(
        "CW0661",
        "sorted() becomes a copy and Array.prototype.sort, which compares "
        "elements as strings unless a comparator is supplied",
        span=call.span,
        hint="pass an explicit comparator after reviewing the generated code",
    )
    return f"[...{args[0]}].sort()"


#: Python type names used as isinstance targets, and their JavaScript
#: constructors.  Primitives are the awkward case: `typeof` is the correct test
#: for them, because `1 instanceof Number` is false in JavaScript.
_TYPEOF_TESTS: dict[str, str] = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
}
_INSTANCE_CONSTRUCTORS: dict[str, str] = {
    "list": "Array",
    "tuple": "Array",
    "dict": "Object",
    "set": "Set",
}


def _isinstance(args: list[str], call: IRCall, ctx: EmitContext) -> str:
    target = call.args[1] if len(call.args) > 1 else None
    name = target.name if isinstance(target, IRName) else ""
    if name in _TYPEOF_TESTS:
        if name in ("int", "float"):
            ctx.warn(
                "CW0667",
                f"isinstance(x, {name}) becomes a typeof number test, which "
                "cannot tell integers from floats",
                span=call.span,
            )
        return f'typeof {args[0]} === "{_TYPEOF_TESTS[name]}"'
    if name in _INSTANCE_CONSTRUCTORS:
        return f"{args[0]} instanceof {_INSTANCE_CONSTRUCTORS[name]}"
    return f"{args[0]} instanceof {args[1]}"


def _print(args: list[str], call: IRCall, ctx: EmitContext) -> str:  # noqa: ARG001
    return f"console.log({', '.join(args)})"


def _min_max(name: str) -> _Mapping:
    def render(args: list[str], call: IRCall, ctx: EmitContext) -> str:
        if len(args) == 1:
            ctx.warn(
                "CW0662",
                f"{name}() over a single sequence is spread into Math.{name}, "
                "which fails on a very large sequence",
                span=call.span,
            )
            return f"Math.{name}(...{args[0]})"
        return f"Math.{name}({', '.join(args)})"

    return _Mapping(render, (1, 64))


#: Builtin name -> mapping.
_MAPPINGS: dict[str, _Mapping] = {
    "len": _Mapping(_length, (1, 1)),
    "print": _Mapping(_print, (0, 64)),
    "str": _simple("String({0})"),
    "int": _simple(
        "Math.trunc(Number({0}))",
        note="int() truncates towards zero; Python's int() rejects some inputs "
        "that Number() accepts",
    ),
    "float": _simple("Number({0})"),
    "bool": _simple("Boolean({0})"),
    "abs": _simple("Math.abs({0})"),
    "round": _simple(
        "Math.round({0})",
        note="Python rounds halves to even; Math.round rounds halves up",
    ),
    "list": _simple("Array.from({0})"),
    "tuple": _simple("Array.from({0})"),
    "set": _simple("new Set({0})"),
    "sum": _Mapping(_sum, (1, 1)),
    "sorted": _Mapping(_sorted, (1, 1)),
    "isinstance": _Mapping(_isinstance, (2, 2)),
    "min": _min_max("min"),
    "max": _min_max("max"),
}

#: Every builtin this backend knows about, mapped or not.
BUILTIN_NAMES: frozenset[str] = frozenset(_MAPPINGS) | frozenset(UNMAPPED_BUILTINS)


def map_builtin_call(call: IRCall, args: list[str], ctx: EmitContext) -> str | None:
    """Render *call* as a TypeScript builtin, or return ``None``.

    ``None`` means "not a builtin call I handle"; the caller then emits an
    ordinary call.  A builtin that is known-unmappable is reported here and
    rendered as a marker, so it never looks like a successful translation.
    """
    callee = call.callee
    if not isinstance(callee, IRName):
        return None
    name = callee.name
    if call.star_args or call.star_keywords:
        return None
    if name in UNMAPPED_BUILTINS and name != "super":
        ctx.warn(
            "CW0663",
            f"builtin {name}() has no TypeScript mapping",
            span=call.span,
            hint=UNMAPPED_BUILTINS[name],
        )
        return f"/* unmapped builtin: {name}() */ undefined"
    mapping = _MAPPINGS.get(name)
    if mapping is None:
        return None
    if call.keywords:
        ctx.warn(
            "CW0664",
            f"builtin {name}() was called with keyword arguments, which are not mapped",
            span=call.span,
        )
        return None
    low, high = mapping.arity
    if not low <= len(args) <= high:
        ctx.warn(
            "CW0665",
            f"builtin {name}() called with {len(args)} argument(s); "
            f"the mapping handles {low}..{high}",
            span=call.span,
        )
        return None
    if mapping.note:
        ctx.warn("CW0666", f"{name}(): {mapping.note}", span=call.span)
    return mapping.render(args, call, ctx)


def dict_literal_type(value_type: IRType | None) -> bool:
    """Whether *value_type* is a mapping type."""
    return bool(value_type and value_type.kind is TypeKind.COLLECTION and value_type.name == "dict")
