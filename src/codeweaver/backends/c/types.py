"""IR types to C types.

* ``int`` -> ``int64_t``, ``float`` -> ``double``, ``bool`` -> ``bool``,
  ``str`` -> ``cw_str`` (an immutable ``const char*``);
* ``list[T]`` -> a pointer to the runtime's growable list for T
  (``cw_list_i64*``); lists of objects or of lists share ``cw_list_ptr``;
* an instance of a project class -> a pointer to its struct, and ``None`` is
  ``NULL``, so aliasing behaves as in Python.

``dict``, ``set``, ``tuple`` and optional primitives have no representation
in this build: :func:`render_type` returns ``None`` for them and the caller
reports the gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.inference import MODULE_REF

if TYPE_CHECKING:
    from codeweaver.backends.c.context import CContext

__all__ = ["PRIMITIVES", "list_kind", "render_type", "zero_value"]

PRIMITIVES: dict[str, str] = {"int": "int64_t", "float": "double", "bool": "bool", "str": "cw_str"}
_LIST_KINDS: dict[str, str] = {"int": "i64", "float": "f64", "bool": "bool", "str": "str"}


def list_kind(element: IRType | None, ctx: CContext) -> str | None:
    """The runtime list suffix for elements of *element* (``i64``, ``ptr``, ...)."""
    if element is None:
        return None
    if element.kind is TypeKind.PRIMITIVE:
        return _LIST_KINDS.get(element.name)
    if render_type(element, ctx) is not None:
        return "ptr"
    return None


def render_type(ir_type: IRType | None, ctx: CContext, *, returning: bool = False) -> str | None:
    if ir_type is None:
        return None
    kind = ir_type.kind
    if kind is TypeKind.PRIMITIVE:
        return PRIMITIVES.get(ir_type.name)
    if kind is TypeKind.NONE:
        return "void" if returning else None
    if kind is TypeKind.OPTIONAL:
        inner = ir_type.args[0] if ir_type.args else None
        rendered = render_type(inner, ctx)
        # Only pointers have a None value.
        if rendered is not None and (rendered.endswith("*") or rendered == "cw_str"):
            return rendered
        return None
    if kind is TypeKind.COLLECTION and ir_type.name == "list" and len(ir_type.args) == 1:
        suffix = list_kind(ir_type.args[0], ctx)
        return f"cw_list_{suffix}*" if suffix else None
    if kind is TypeKind.OBJECT and not ir_type.meta.get(MODULE_REF):
        info = ctx.class_of(ir_type)
        if info is not None and not info.is_exception:
            return f"{ctx.class_symbol(info)}*"
    return None


def zero_value(rendered: str) -> str:
    if rendered in ("int64_t", "double"):
        return "0"
    if rendered == "bool":
        return "false"
    return "NULL"
