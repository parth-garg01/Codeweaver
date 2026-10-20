"""IR types to TypeScript type annotations.

Where the IR says it does not know a type, the output says ``unknown`` rather
than ``any``: ``unknown`` makes the compiler ask for a narrowing step, which
turns a gap in the migration into a visible task instead of a silent hole in
the type system.
"""

from __future__ import annotations

from codeweaver.backends.typescript.naming import safe_identifier
from codeweaver.ir.types import IRType, TypeKind

__all__ = ["PRIMITIVE_MAP", "render_return_type", "render_type", "render_type_arguments"]

#: IR primitive name -> TypeScript type.
PRIMITIVE_MAP: dict[str, str] = {
    "int": "number",
    "float": "number",
    "str": "string",
    "bool": "boolean",
    "bytes": "Uint8Array",
}


def render_type(ir_type: IRType | None) -> str:
    """Render *ir_type* as a TypeScript type expression."""
    if ir_type is None:
        return "unknown"
    kind = ir_type.kind
    if kind is TypeKind.PRIMITIVE:
        return PRIMITIVE_MAP.get(ir_type.name, "unknown")
    if kind is TypeKind.NONE:
        return "null"
    if kind is TypeKind.ANY:
        return "any"
    if kind is TypeKind.UNKNOWN:
        return "unknown"
    if kind is TypeKind.OPTIONAL:
        inner = render_type(ir_type.args[0]) if ir_type.args else "unknown"
        return f"{_wrap_union_member(inner)} | null"
    if kind is TypeKind.UNION:
        return " | ".join(_wrap_union_member(render_type(arg)) for arg in ir_type.args)
    if kind is TypeKind.CALLABLE:
        return _render_callable(ir_type)
    if kind is TypeKind.COLLECTION:
        return _render_collection(ir_type)
    if kind is TypeKind.OBJECT:
        name = safe_identifier(ir_type.name) if ir_type.name else "unknown"
        if ir_type.args:
            return f"{name}<{render_type_arguments(ir_type.args)}>"
        return name
    return "unknown"  # pragma: no cover - every kind is handled above


def render_return_type(ir_type: IRType | None) -> str:
    """Render *ir_type* as a return type.

    ``-> None`` means "returns nothing", which TypeScript spells ``void``.
    Rendering it as ``null`` would be a different promise: a function typed
    ``: null`` has to ``return null`` explicitly, so every translated procedure
    would fail to compile.
    """
    if ir_type is not None and ir_type.kind is TypeKind.NONE:
        return "void"
    return render_type(ir_type)


def render_type_arguments(args: tuple[IRType, ...]) -> str:
    return ", ".join(render_type(arg) for arg in args)


def _render_collection(ir_type: IRType) -> str:
    name = ir_type.name
    args = ir_type.args
    if name == "list":
        item = render_type(args[0]) if args else "unknown"
        return f"{_wrap_array_item(item)}[]"
    if name == "set":
        item = render_type(args[0]) if args else "unknown"
        return f"Set<{item}>"
    if name == "tuple":
        if not args:
            return "unknown[]"
        return f"[{render_type_arguments(args)}]"
    if name == "dict":
        key = render_type(args[0]) if args else "unknown"
        value = render_type(args[1]) if len(args) > 1 else "unknown"
        # Record keys must be string, number or symbol.  Anything else needs a
        # Map, which is a different runtime value — so the choice is made from
        # the key type rather than defaulting to one and hoping.
        if key in ("string", "number"):
            return f"Record<{key}, {value}>"
        return f"Map<{key}, {value}>"
    return "unknown"


def _render_callable(ir_type: IRType) -> str:
    if not ir_type.args:
        return "(...args: unknown[]) => unknown"
    *params, returns = ir_type.args
    rendered = ", ".join(f"arg{index}: {render_type(param)}" for index, param in enumerate(params))
    return f"({rendered}) => {render_type(returns)}"


def _wrap_union_member(rendered: str) -> str:
    """Parenthesise a union member that would otherwise re-associate."""
    if "=>" in rendered:
        return f"({rendered})"
    return rendered


def _wrap_array_item(rendered: str) -> str:
    """Parenthesise an array element type that needs it, e.g. ``(a | b)[]``."""
    if "|" in rendered or "=>" in rendered:
        return f"({rendered})"
    return rendered
