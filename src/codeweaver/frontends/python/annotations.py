"""Python type annotations to :class:`IRType`.

Annotations are read as syntax, not evaluated.  CodeWeaver never imports the
project being migrated, so ``typing.get_type_hints`` is not available to it —
and would be unsafe even if it were.  What cannot be understood becomes
``unknown`` carrying the original spelling, which a backend can surface as a
comment instead of inventing a type.
"""

from __future__ import annotations

import ast

from codeweaver.ir.types import (
    IRType,
    TypeKind,
    t_any,
    t_bool,
    t_callable,
    t_dict,
    t_float,
    t_int,
    t_list,
    t_none,
    t_optional,
    t_set,
    t_str,
    t_tuple,
    t_union,
    t_unknown,
)

__all__ = [
    "COLLECTION_ALIASES",
    "PRIMITIVE_ALIASES",
    "annotation_to_type",
    "infer_literal_type",
]

#: Python names that map onto IR primitives.
PRIMITIVE_ALIASES: dict[str, IRType] = {
    "int": t_int(),
    "float": t_float(),
    "str": t_str(),
    "bool": t_bool(),
    "bytes": IRType(TypeKind.PRIMITIVE, "bytes", source_text="bytes"),
}

#: Typing spellings collapsed onto one IR collection name.  ``Iterable`` and
#: ``Sequence`` become ``list`` because the IR records the shape a target has
#: to produce, not the protocol the source promised.
COLLECTION_ALIASES: dict[str, str] = {
    "list": "list",
    "List": "list",
    "Sequence": "list",
    "MutableSequence": "list",
    "Iterable": "list",
    "Iterator": "list",
    "dict": "dict",
    "Dict": "dict",
    "Mapping": "dict",
    "MutableMapping": "dict",
    "DefaultDict": "dict",
    "set": "set",
    "Set": "set",
    "FrozenSet": "set",
    "frozenset": "set",
    "tuple": "tuple",
    "Tuple": "tuple",
}


def annotation_to_type(node: ast.expr | None) -> IRType:
    """Convert an annotation expression into an :class:`IRType`."""
    if node is None:
        return t_unknown()
    source_text = _unparse(node)
    converted = _convert(node)
    if converted.kind is TypeKind.UNKNOWN and not converted.source_text:
        return t_unknown(source_text)
    return converted


def _convert(node: ast.expr) -> IRType:
    if isinstance(node, ast.Constant):
        if node.value is None:
            return t_none()
        if isinstance(node.value, str):
            return _convert_forward_reference(node.value)
        return t_unknown(_unparse(node))
    if isinstance(node, ast.Name):
        return _convert_name(node.id)
    if isinstance(node, ast.Attribute):
        return _convert_name(node.attr)
    if isinstance(node, ast.Subscript):
        return _convert_subscript(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _convert_union([node.left, node.right])
    if isinstance(node, ast.List | ast.Tuple):
        # `Callable[[int, str], bool]` reaches here for its parameter list.
        return t_tuple(*[_convert(element) for element in node.elts])
    return t_unknown(_unparse(node))


def _convert_name(name: str) -> IRType:
    if name in PRIMITIVE_ALIASES:
        return PRIMITIVE_ALIASES[name]
    if name in ("None", "NoneType"):
        return t_none()
    if name in ("Any", "object"):
        return t_any()
    if name in COLLECTION_ALIASES:
        # A bare `list` or `Dict` with no parameters: the shape is known, the
        # element type is not.
        collection = COLLECTION_ALIASES[name]
        if collection == "dict":
            return t_dict()
        if collection == "set":
            return t_set()
        if collection == "tuple":
            return IRType(TypeKind.COLLECTION, "tuple", source_text=name)
        return t_list()
    if name in ("Callable", "Coroutine", "Awaitable"):
        return t_callable()
    return IRType(TypeKind.OBJECT, name, source_text=name)


def _convert_subscript(node: ast.Subscript) -> IRType:
    base = node.value
    name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
    args = _subscript_args(node)

    if name == "Optional":
        return t_optional(_convert(args[0])) if args else t_unknown(_unparse(node))
    if name == "Union":
        return _convert_union(args)
    if name in ("Callable", "Coroutine", "Awaitable"):
        return _convert_callable(name, args, node)
    if name in COLLECTION_ALIASES:
        collection = COLLECTION_ALIASES[name]
        converted = [_convert(arg) for arg in args]
        if collection == "dict":
            key = converted[0] if converted else t_unknown()
            value = converted[1] if len(converted) > 1 else t_unknown()
            return t_dict(key, value)
        if collection == "set":
            return t_set(converted[0] if converted else None)
        if collection == "tuple":
            return t_tuple(*converted)
        return t_list(converted[0] if converted else None)
    if name:
        return IRType(
            TypeKind.OBJECT,
            name,
            tuple(_convert(arg) for arg in args),
            source_text=_unparse(node),
        )
    return t_unknown(_unparse(node))


def _convert_callable(name: str, args: list[ast.expr], node: ast.Subscript) -> IRType:
    if name in ("Coroutine", "Awaitable"):
        # Awaitable[T] / Coroutine[Y, S, T]: the useful part is the result.
        returns = _convert(args[-1]) if args else t_unknown()
        return t_callable((), returns)
    if len(args) != 2:
        return t_callable()
    params_node, returns_node = args
    if isinstance(params_node, ast.List):
        params = tuple(_convert(element) for element in params_node.elts)
    elif isinstance(params_node, ast.Constant) and params_node.value is Ellipsis:
        params = ()
    else:
        return t_unknown(_unparse(node))
    return t_callable(params, _convert(returns_node))


def _convert_union(args: list[ast.expr]) -> IRType:
    members = [_convert(arg) for arg in args]
    flattened: list[IRType] = []
    for member in members:
        if member.kind is TypeKind.UNION:
            flattened.extend(member.args)
        else:
            flattened.append(member)
    non_none = [member for member in flattened if member.kind is not TypeKind.NONE]
    if len(non_none) != len(flattened):
        inner = t_union(*non_none) if non_none else t_none()
        return t_optional(inner)
    return t_union(*flattened)


def _convert_forward_reference(text: str) -> IRType:
    """Convert a string annotation such as ``"Account"`` or ``"list[int]"``."""
    try:
        parsed = ast.parse(text, mode="eval")
    except SyntaxError:
        return t_unknown(text)
    return _convert(parsed.body)


def _subscript_args(node: ast.Subscript) -> list[ast.expr]:
    sliced = node.slice
    if isinstance(sliced, ast.Tuple):
        return list(sliced.elts)
    return [sliced]


def infer_literal_type(value: object) -> IRType:
    """The IR type of a Python literal value.

    ``bool`` is checked before ``int`` because ``bool`` is a subclass of
    ``int`` and a target language will almost certainly not agree.
    """
    if value is None:
        return t_none()
    if isinstance(value, bool):
        return t_bool()
    if isinstance(value, int):
        return t_int()
    if isinstance(value, float):
        return t_float()
    if isinstance(value, str):
        return t_str()
    if isinstance(value, bytes):
        return PRIMITIVE_ALIASES["bytes"]
    return t_unknown(type(value).__name__)


def _unparse(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):  # pragma: no cover - defensive
        return ""
