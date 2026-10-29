"""IR types to Go type expressions.

Two mappings are lossy in ways the report has to carry:

* Python's ``int`` is arbitrary precision; Go's ``int`` is 64-bit on every
  platform CodeWeaver supports.  Choosing ``int`` keeps arithmetic and indexing
  idiomatic, and the difference is declared as a limitation rather than hidden
  behind a big-integer type nobody asked for.
* Go has no sum types, so an optional becomes a pointer (``*T``) and a union
  becomes ``any``.  A pointer carries nil-ness honestly; ``any`` does not carry
  anything, which is why a union is also reported.
"""

from __future__ import annotations

from codeweaver.backends.go.naming import exported_name
from codeweaver.ir.types import IRType, TypeKind

__all__ = ["PRIMITIVE_MAP", "render_type", "needs_pointer"]

#: IR primitive -> Go type.
PRIMITIVE_MAP: dict[str, str] = {
    "int": "int",
    "float": "float64",
    "str": "string",
    "bool": "bool",
    "bytes": "[]byte",
}


def needs_pointer(ir_type: IRType | None) -> bool:
    """Whether a value of this type is passed around as a pointer.

    Structs are: Python objects have reference semantics, and a Go struct
    passed by value would copy, so a method that mutates its receiver would
    mutate a copy.  That is a behaviour change a type checker cannot see, so
    object types become pointers everywhere.
    """
    return bool(ir_type and ir_type.kind is TypeKind.OBJECT and bool(ir_type.name))


def render_type(ir_type: IRType | None) -> str:
    """Render *ir_type* as a Go type expression."""
    if ir_type is None:
        return "any"
    kind = ir_type.kind
    if kind is TypeKind.PRIMITIVE:
        return PRIMITIVE_MAP.get(ir_type.name, "any")
    if kind is TypeKind.NONE:
        return "any"
    if kind in (TypeKind.ANY, TypeKind.UNKNOWN, TypeKind.UNION):
        return "any"
    if kind is TypeKind.OPTIONAL:
        inner = ir_type.args[0] if ir_type.args else None
        rendered = render_type(inner)
        # A pointer is how Go spells "may be absent".  A slice, a map, an
        # interface and an object type are already nilable — an object type
        # renders as `*T` precisely because Python objects are references — so
        # wrapping them again would produce `**T`.
        if rendered.startswith(("[]", "map[", "*", "func(")) or rendered == "any":
            return rendered
        return f"*{rendered}"
    if kind is TypeKind.CALLABLE:
        return _render_callable(ir_type)
    if kind is TypeKind.COLLECTION:
        return _render_collection(ir_type)
    if kind is TypeKind.OBJECT:
        return f"*{exported_name(ir_type.name)}" if ir_type.name else "any"
    return "any"  # pragma: no cover - every kind is handled


def _render_collection(ir_type: IRType) -> str:
    name = ir_type.name
    args = ir_type.args
    if name == "list":
        return f"[]{render_type(args[0]) if args else 'any'}"
    if name == "set":
        # Go has no set type.  A map to the empty struct is the idiomatic
        # stand-in and costs no space per element.
        return f"map[{render_type(args[0]) if args else 'any'}]struct{{}}"
    if name == "dict":
        key = render_type(args[0]) if args else "any"
        value = render_type(args[1]) if len(args) > 1 else "any"
        return f"map[{key}]{value}"
    if name == "tuple":
        # Go has no tuple type.  A heterogeneous tuple becomes []any, which
        # loses the element types; the report says so.
        if args and all(render_type(arg) == render_type(args[0]) for arg in args):
            return f"[]{render_type(args[0])}"
        return "[]any"
    return "any"


def _render_callable(ir_type: IRType) -> str:
    if not ir_type.args:
        return "func(...any) any"
    *params, returns = ir_type.args
    rendered = ", ".join(render_type(param) for param in params)
    result = render_type(returns)
    if returns.kind is TypeKind.NONE:
        return f"func({rendered})"
    return f"func({rendered}) {result}"
