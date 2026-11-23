"""IR types to C++ types.

The mapping keeps Python's reference semantics where programs depend on them
and uses value types where they are faster and equivalent:

* ``int`` -> ``std::int64_t``, ``float`` -> ``double``, ``str`` ->
  ``std::string``, ``bool`` -> ``bool``;
* ``list[T]`` -> ``std::vector<T>``, ``set[T]`` -> ``std::unordered_set<T>``,
  ``dict[K, V]`` -> ``cw::dict<K, V>`` (insertion-ordered, like Python);
* an instance of a project class -> ``std::shared_ptr<C>``, so aliasing,
  ``None`` and identity behave as they do in Python;
* an exception class -> the class itself, because C++ throws by value;
* ``X | None`` -> the pointer itself for classes, ``std::optional<X>`` for
  values.

:func:`render_type` returns ``None`` when part of the type is unknown; the
caller decides whether ``auto`` is acceptable there or the gap must be
reported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.inference import EXCEPTION_REF, MODULE_REF

if TYPE_CHECKING:
    from codeweaver.backends.cpp.context import CppContext

__all__ = ["PRIMITIVES", "is_class_pointer", "is_value_optional", "render_type"]

PRIMITIVES: dict[str, str] = {
    "int": "std::int64_t",
    "float": "double",
    "str": "std::string",
    "bool": "bool",
    "bytes": "std::vector<std::uint8_t>",
}


def is_class_pointer(ir_type: IRType | None, ctx: CppContext) -> bool:
    """Whether a value of *ir_type* is held through ``std::shared_ptr``."""
    if ir_type is None:
        return False
    if ir_type.kind is TypeKind.OPTIONAL and ir_type.args:
        return is_class_pointer(ir_type.args[0], ctx)
    info = ctx.index.class_for_type(ir_type, ctx.module.name) if ctx.index else None
    return info is not None and not info.is_exception


def is_value_optional(ir_type: IRType | None, ctx: CppContext) -> bool:
    """``X | None`` rendered as ``std::optional<X>``."""
    return bool(
        ir_type is not None
        and ir_type.kind is TypeKind.OPTIONAL
        and ir_type.args
        and not is_class_pointer(ir_type.args[0], ctx)
    )


def render_type(ir_type: IRType | None, ctx: CppContext, *, returning: bool = False) -> str | None:
    """Spell *ir_type* in C++, or ``None`` if any part of it is unknown."""
    if ir_type is None:
        return None
    kind = ir_type.kind
    if kind is TypeKind.PRIMITIVE:
        return PRIMITIVES.get(ir_type.name)
    if kind is TypeKind.NONE:
        return "void" if returning else "std::nullptr_t"
    if kind in (TypeKind.ANY, TypeKind.UNKNOWN):
        return None
    if kind is TypeKind.OPTIONAL:
        inner_type = ir_type.args[0] if ir_type.args else None
        inner = render_type(inner_type, ctx)
        if inner is None:
            return None
        if is_class_pointer(inner_type, ctx):
            return inner
        return f"std::optional<{inner}>"
    if kind is TypeKind.UNION:
        members = [render_type(arg, ctx) for arg in ir_type.args]
        if any(member is None for member in members):
            return None
        return f"std::variant<{', '.join(m for m in members if m)}>"
    if kind is TypeKind.CALLABLE:
        if not ir_type.args:
            return None
        *params, returns = ir_type.args
        rendered_params = [render_type(param, ctx) for param in params]
        rendered_return = render_type(returns, ctx, returning=True)
        if rendered_return is None or any(param is None for param in rendered_params):
            return None
        return f"std::function<{rendered_return}({', '.join(p for p in rendered_params if p)})>"
    if kind is TypeKind.COLLECTION:
        return _collection(ir_type, ctx)
    if kind is TypeKind.OBJECT:
        return _object(ir_type, ctx)
    return None


def _collection(ir_type: IRType, ctx: CppContext) -> str | None:
    args = [render_type(arg, ctx) for arg in ir_type.args]
    if any(arg is None for arg in args):
        return None
    name = ir_type.name
    if name == "list" and len(args) == 1:
        return f"std::vector<{args[0]}>"
    if name == "set" and len(args) == 1:
        return f"std::unordered_set<{args[0]}>"
    if name == "dict" and len(args) == 2:
        return f"cw::dict<{args[0]}, {args[1]}>"
    if name == "tuple" and args:
        return f"std::tuple<{', '.join(a for a in args if a)}>"
    return None


def _object(ir_type: IRType, ctx: CppContext) -> str | None:
    if ir_type.meta.get(MODULE_REF):
        return None
    if ir_type.meta.get(EXCEPTION_REF) or ir_type.name in BUILTIN_EXCEPTIONS:
        if ctx.index is None or ctx.index.class_for_type(ir_type, ctx.module.name) is None:
            return f"cw::{ir_type.name}"
    info = ctx.index.class_for_type(ir_type, ctx.module.name) if ctx.index else None
    if info is None:
        return None
    spelled = ctx.class_spelling(info)
    if info.is_exception:
        return spelled
    return f"std::shared_ptr<{spelled}>"
