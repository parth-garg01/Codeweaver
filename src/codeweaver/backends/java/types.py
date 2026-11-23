"""IR types to Java types.

``int``, ``float`` and ``bool`` are the primitives ``long``, ``double`` and
``boolean`` wherever Java allows a primitive, and their boxes inside generics
and wherever ``None`` is a possible value.  Containers are the
``java.util`` interfaces backed by insertion-ordered implementations, so
iteration order matches Python's.

:func:`render_type` returns ``None`` when part of the type is unknown; the
caller decides between ``var``, ``Object`` and reporting a gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.inference import EXCEPTION_REF, MODULE_REF

if TYPE_CHECKING:
    from codeweaver.backends.java.context import JavaContext

__all__ = ["BOXES", "EXCEPTIONS", "PRIMITIVES", "default_value", "is_primitive", "render_type"]

PRIMITIVES: dict[str, str] = {"int": "long", "float": "double", "bool": "boolean", "str": "String"}
BOXES: dict[str, str] = {"int": "Long", "float": "Double", "bool": "Boolean", "str": "String"}

#: Python exceptions that have a natural Java counterpart; the rest live in Py.
EXCEPTIONS: dict[str, str] = {
    "BaseException": "Throwable",
    "Exception": "RuntimeException",
    "ValueError": "IllegalArgumentException",
    "IndexError": "IndexOutOfBoundsException",
    "KeyError": "NoSuchElementException",
    "ZeroDivisionError": "ArithmeticException",
    "ArithmeticError": "ArithmeticException",
    "OverflowError": "ArithmeticException",
    "NotImplementedError": "UnsupportedOperationException",
    "AssertionError": "AssertionError",
    "TypeError": "Py.TypeError",
    "RuntimeError": "Py.RuntimeError",
    "RecursionError": "Py.RecursionError",
    "AttributeError": "Py.AttributeError",
    "NameError": "Py.NameError",
    "LookupError": "Py.LookupError",
    "OSError": "Py.OSError",
    "IOError": "Py.OSError",
    "FileNotFoundError": "Py.FileNotFoundError",
    "PermissionError": "Py.PermissionError",
    "TimeoutError": "Py.TimeoutError",
    "StopIteration": "Py.StopIteration",
    "ImportError": "Py.ImportError",
    "ModuleNotFoundError": "Py.ImportError",
    "EOFError": "Py.EOFError",
    "MemoryError": "Py.MemoryError",
    "UnicodeError": "Py.UnicodeError",
}


def is_primitive(rendered: str | None) -> bool:
    return rendered in ("long", "double", "boolean")


def default_value(rendered: str) -> str:
    """The value a declaration without an initialiser starts with."""
    return {"long": "0L", "double": "0.0", "boolean": "false"}.get(rendered, "null")


def render_type(
    ir_type: IRType | None, ctx: JavaContext, *, boxed: bool = False, returning: bool = False
) -> str | None:
    """Spell *ir_type* in Java, or ``None`` if any part of it is unknown."""
    if ir_type is None:
        return None
    kind = ir_type.kind
    if kind is TypeKind.PRIMITIVE:
        table = BOXES if boxed else PRIMITIVES
        return table.get(ir_type.name)
    if kind is TypeKind.NONE:
        return "void" if returning else "Object"
    if kind in (TypeKind.ANY, TypeKind.UNKNOWN):
        return None
    if kind is TypeKind.OPTIONAL:
        inner = ir_type.args[0] if ir_type.args else None
        return render_type(inner, ctx, boxed=True)
    if kind is TypeKind.UNION:
        return "Object" if all(render_type(arg, ctx) for arg in ir_type.args) else None
    if kind is TypeKind.CALLABLE:
        return _callable(ir_type, ctx)
    if kind is TypeKind.COLLECTION:
        return _collection(ir_type, ctx)
    if kind is TypeKind.OBJECT:
        return _object(ir_type, ctx)
    return None


def _callable(ir_type: IRType, ctx: JavaContext) -> str | None:
    if not ir_type.args:
        return None
    *params, returns = ir_type.args
    rendered = [render_type(param, ctx, boxed=True) for param in params]
    if any(item is None for item in rendered):
        return None
    void = returns.kind is TypeKind.NONE
    result = None if void else render_type(returns, ctx, boxed=True)
    if not void and result is None:
        return None
    if len(rendered) == 0:
        return "Runnable" if void else f"java.util.function.Supplier<{result}>"
    if len(rendered) == 1:
        if void:
            return f"java.util.function.Consumer<{rendered[0]}>"
        return f"java.util.function.Function<{rendered[0]}, {result}>"
    if len(rendered) == 2:
        if void:
            return f"java.util.function.BiConsumer<{rendered[0]}, {rendered[1]}>"
        return f"java.util.function.BiFunction<{rendered[0]}, {rendered[1]}, {result}>"
    return None


def _collection(ir_type: IRType, ctx: JavaContext) -> str | None:
    args = [render_type(arg, ctx, boxed=True) for arg in ir_type.args]
    if any(arg is None for arg in args):
        return None
    name = ir_type.name
    if name == "list" and len(args) == 1:
        return f"List<{args[0]}>"
    if name == "set" and len(args) == 1:
        return f"Set<{args[0]}>"
    if name == "dict" and len(args) == 2:
        return f"Map<{args[0]}, {args[1]}>"
    if name == "tuple" and 2 <= len(args) <= 4:
        return f"Py.Tuple{len(args)}<{', '.join(a for a in args if a)}>"
    return None


def _object(ir_type: IRType, ctx: JavaContext) -> str | None:
    if ir_type.meta.get(MODULE_REF):
        return None
    info = ctx.index.class_for_type(ir_type, ctx.module.name) if ctx.index else None
    if info is not None:
        return ctx.class_spelling(info)
    if ir_type.meta.get(EXCEPTION_REF) or ir_type.name in BUILTIN_EXCEPTIONS:
        return EXCEPTIONS.get(ir_type.name, "RuntimeException")
    return None
