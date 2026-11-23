"""IR expressions to C++ expressions.

Every decision is driven by the inferred type of the operands:

* operators whose Python meaning differs from C++ (``/``, ``//``, ``%``,
  ``**``, ``in``) call the runtime, which implements the Python semantics
  including ``ZeroDivisionError``;
* ``xs[i]`` is bounds-checked and accepts negative indices, except inside a
  loop that provably stays in bounds, where it compiles to a raw access;
* member access uses ``->`` for class instances (held by ``std::shared_ptr``)
  and ``.`` for values;
* keyword arguments are matched against the callee's real signature and
  emitted positionally, so no options object is needed;
* builtins and builtin-type methods map onto the runtime's inline functions.

Where C++ needs a type the IR does not have, the expression is still emitted
(``auto`` covers most of these) and the gap is reported.
"""

from __future__ import annotations

from collections.abc import Callable

from codeweaver.backends.common.clike import (
    BINARY_SPELLING,
    COMPARE_SPELLING,
    CONDITIONAL,
    EQUALITY,
    LOGICAL_AND,
    LOGICAL_OR,
    POSTFIX,
    PRIMARY,
    UNARY,
    Emitted,
)
from codeweaver.backends.common.strings import c_string_literal
from codeweaver.backends.cpp.context import CppContext
from codeweaver.backends.cpp.naming import safe_identifier
from codeweaver.backends.cpp.types import is_value_optional, render_type
from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS, ClassInfo, ScopeKind
from codeweaver.ir.base import CONSTRUCTS_CLASS, IRNode, IRUnsupported
from codeweaver.ir.declarations import IRFunction
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRAwait,
    IRBinary,
    IRCall,
    IRCompare,
    IRComprehension,
    IRConditional,
    IRExpression,
    IRIndex,
    IRLambda,
    IRLiteral,
    IRLogical,
    IRMapLiteral,
    IRName,
    IRParam,
    IRSetLiteral,
    IRSlice,
    IRTemplateString,
    IRTupleLiteral,
    IRUnary,
)
from codeweaver.ir.operators import BinaryOp, CompareOp, LogicalOp, UnaryOp
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.inference import CLASS_REF, MODULE_REF, element_type

__all__ = ["condition", "emit_expression", "is_super_call", "lower_arguments"]

#: Python builtins that have no faithful C++ mapping in this build.
UNMAPPED_BUILTINS: dict[str, str] = {
    "enumerate": "iterate with an index loop",
    "zip": "index both sequences explicitly",
    "map": "use a comprehension",
    "filter": "use a comprehension with a condition",
    "reversed": "copy and reverse the sequence explicitly",
    "iter": "iterators are not modelled",
    "next": "iterators are not modelled",
    "getattr": "dynamic attribute access is not modelled",
    "setattr": "dynamic attribute access is not modelled",
    "hasattr": "dynamic attribute access is not modelled",
    "delattr": "dynamic attribute access is not modelled",
    "eval": "dynamic evaluation is deliberately never generated",
    "exec": "dynamic evaluation is deliberately never generated",
    "globals": "module globals are not reified",
    "locals": "local scopes are not reified",
    "vars": "namespaces are not reified",
    "type": "runtime type objects are not modelled",
    "id": "object identity numbers are not modelled",
    "hash": "use std::hash explicitly",
    "open": "file I/O is not translated; use <fstream>",
    "input": "console input is not translated; use std::getline",
    "format": "use an f-string",
}

#: Builtin-type methods the runtime implements as cw::<name>(receiver, ...).
_RUNTIME_METHODS: dict[str, frozenset[str]] = {
    "str": frozenset(
        {
            "upper",
            "lower",
            "strip",
            "lstrip",
            "rstrip",
            "startswith",
            "endswith",
            "find",
            "rfind",
            "index",
            "count",
            "replace",
            "split",
            "join",
            "isdigit",
            "isalpha",
            "isalnum",
            "isspace",
            "isupper",
            "islower",
            "capitalize",
            "title",
            "zfill",
        }
    ),
    "list": frozenset(
        {
            "append",
            "extend",
            "insert",
            "pop",
            "remove",
            "index",
            "count",
            "clear",
            "copy",
            "reverse",
            "sort",
        }
    ),
    "dict": frozenset(
        {"get", "keys", "values", "items", "pop", "setdefault", "update", "clear", "copy"}
    ),
    "set": frozenset(
        {
            "add",
            "remove",
            "discard",
            "update",
            "clear",
            "copy",
            "union",
            "intersection",
            "difference",
        }
    ),
}

#: math module members -> C++ spelling.  `{0}` is the first argument.
_MATH_FUNCTIONS: dict[str, str] = {
    "sqrt": "std::sqrt({0})",
    "exp": "std::exp({0})",
    "log2": "std::log2({0})",
    "log10": "std::log10({0})",
    "sin": "std::sin({0})",
    "cos": "std::cos({0})",
    "tan": "std::tan({0})",
    "asin": "std::asin({0})",
    "acos": "std::acos({0})",
    "atan": "std::atan({0})",
    "atan2": "std::atan2({0}, {1})",
    "hypot": "std::hypot({0}, {1})",
    "fabs": "std::fabs({0})",
    "pow": "std::pow({0}, {1})",
    "floor": "static_cast<std::int64_t>(std::floor({0}))",
    "ceil": "static_cast<std::int64_t>(std::ceil({0}))",
    "trunc": "static_cast<std::int64_t>(std::trunc({0}))",
    "isnan": "std::isnan({0})",
    "isinf": "std::isinf({0})",
    "isfinite": "std::isfinite({0})",
    "gcd": "std::gcd({0}, {1})",
    "lcm": "std::lcm({0}, {1})",
    "copysign": "std::copysign({0}, {1})",
}
_MATH_CONSTANTS: dict[str, str] = {
    "pi": "std::numbers::pi",
    "e": "std::numbers::e",
    "tau": "(2 * std::numbers::pi)",
    "inf": "std::numeric_limits<double>::infinity()",
    "nan": "std::numeric_limits<double>::quiet_NaN()",
}


def _type(node: IRNode | None) -> IRType | None:
    return getattr(node, "value_type", None)


def _is(node_type: IRType | None, name: str) -> bool:
    return bool(
        node_type
        and node_type.name == name
        and node_type.kind in (TypeKind.PRIMITIVE, TypeKind.COLLECTION)
    )


def _is_int(node_type: IRType | None) -> bool:
    return _is(node_type, "int") or _is(node_type, "bool")


def _is_float(node_type: IRType | None) -> bool:
    return _is(node_type, "float")


def is_super_call(node: IRNode | None) -> bool:
    return (
        isinstance(node, IRCall)
        and isinstance(node.callee, IRName)
        and node.callee.name == "super"
        and not node.args
    )


# --------------------------------------------------------------------- entry


def emit_expression(
    node: IRNode | None, ctx: CppContext, expected: IRType | None = None
) -> Emitted:
    """Render *node* as a C++ expression."""
    if node is None:
        return Emitted("nullptr")
    if isinstance(node, IRUnsupported):
        marker = ctx.record_gap(node)
        return Emitted(f"{marker} cw::unsupported({c_string_literal(node.source_kind)})", POSTFIX)
    handler = _HANDLERS.get(type(node))
    if handler is None:
        return Emitted(
            ctx.gap(node.kind, "no C++ mapping for this expression", node.span)
            + ' cw::unsupported("expression")'
        )
    return handler(node, ctx, expected)


def _is_boolean(node: IRNode | None) -> bool:
    if _is(_type(node), "bool"):
        return True
    return isinstance(node, IRCompare) or (isinstance(node, IRUnary) and node.op is UnaryOp.NOT)


def _used_for_value(node: IRLogical) -> bool:
    """``a or b`` whose result is an operand (not a bool) in Python."""
    node_type = _type(node)
    return (
        node_type is not None
        and node_type.kind is not TypeKind.UNKNOWN
        and not _is(node_type, "bool")
    )


def _condition_emitted(node: IRNode | None, ctx: CppContext) -> Emitted:
    rendered = emit_expression(node, ctx)
    if _is_boolean(node):
        return rendered
    if isinstance(node, IRLogical) and not _used_for_value(node):
        return rendered
    return Emitted(f"cw::truthy({rendered.text})", POSTFIX)


def condition(node: IRNode | None, ctx: CppContext) -> str:
    """Render *node* where C++ needs a ``bool``, applying Python truthiness."""
    return _condition_emitted(node, ctx).text


# ------------------------------------------------------------------ literals


def _int_literal(value: int) -> str:
    if -(2**31) <= value < 2**31:
        return str(value)
    if -(2**63) <= value < 2**63:
        return f"INT64_C({value})"
    return str(value)  # out of range; the compiler reports it


def _float_literal(value: float) -> str:
    if value != value:
        return "std::numeric_limits<double>::quiet_NaN()"
    if value in (float("inf"), float("-inf")):
        sign = "-" if value < 0 else ""
        return f"{sign}std::numeric_limits<double>::infinity()"
    text = repr(value)
    if "." not in text and "e" not in text:
        text += ".0"
    return text


def _literal(node: IRLiteral, ctx: CppContext, expected: IRType | None) -> Emitted:
    value = node.value
    if node.meta.get("pythonEllipsis"):
        return Emitted('cw::unsupported("...")', POSTFIX)
    if value is None:
        if expected is not None and is_value_optional(expected, ctx):
            return Emitted("std::nullopt")
        return Emitted("nullptr")
    if isinstance(value, bool):
        return Emitted("true" if value else "false")
    if isinstance(value, int):
        if expected is not None and _is_float(expected):
            return Emitted(_float_literal(float(value)))
        text = _int_literal(value)
        return Emitted(text, UNARY if text.startswith("-") else PRIMARY)
    if isinstance(value, float):
        text = _float_literal(value)
        return Emitted(text, UNARY if text.startswith("-") else PRIMARY)
    if isinstance(value, str):
        return Emitted(f"{c_string_literal(value)}s")
    if isinstance(value, bytes):
        return Emitted("std::vector<std::uint8_t>{" + ", ".join(str(byte) for byte in value) + "}")
    return Emitted(
        ctx.gap("literal", f"literal of type {type(value).__name__} has no C++ spelling", node.span)
    )


# --------------------------------------------------------------------- names


def _name(node: IRName, ctx: CppContext, expected: IRType | None) -> Emitted:
    name = node.name
    if ctx.receiver and name == ctx.receiver:
        if ctx.in_constructor:
            ctx.warn(
                "CW0680",
                "self is used as a value inside __init__, where it is not yet owned by a shared pointer",
                span=node.span,
                hint="move the code that hands out self to a method called after construction",
            )
        class_name = safe_identifier(ctx.klass.name) if ctx.klass else "cw::Object"
        return Emitted(f"cw::self<{class_name}>(this)", POSTFIX)
    if ctx.class_receiver and name == ctx.class_receiver and ctx.klass is not None:
        return Emitted(safe_identifier(ctx.klass.name))
    if name in ctx.locals:
        return Emitted(safe_identifier(name))
    entry = ctx.scope_entry(name)
    if entry is not None:
        if entry.kind is ScopeKind.EXTERNAL:
            return _external_name(entry.qualified, node, ctx)
        return Emitted(ctx.entry_spelling(entry, name))
    if name in BUILTIN_EXCEPTIONS:
        return Emitted(f"cw::{name}")
    return Emitted(safe_identifier(name))


def _external_name(qualified: str, node: IRNode, ctx: CppContext) -> Emitted:
    module, _, member = qualified.rpartition(".")
    if module == "math" and member in _MATH_CONSTANTS:
        return Emitted(_MATH_CONSTANTS[member])
    ctx.warn(
        "CW0682",
        f"{qualified!r} comes from an external module that has no C++ mapping",
        span=node.span,
        hint="re-implement it, or link a C++ library that provides it",
    )
    return Emitted(safe_identifier(member or qualified))


# ----------------------------------------------------------------- operators


def _binary(node: IRBinary, ctx: CppContext, expected: IRType | None) -> Emitted:
    left_type, right_type = _type(node.left), _type(node.right)
    op = node.op
    left = emit_expression(node.left, ctx)
    right = emit_expression(node.right, ctx)
    if op is BinaryOp.DIV:
        return Emitted(f"cw::truediv({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.FLOOR_DIV:
        return Emitted(f"cw::floordiv({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MOD:
        if _is(left_type, "str"):
            return Emitted(
                ctx.gap(
                    "BinOp",
                    "printf-style string formatting has no C++ mapping",
                    node.span,
                    "use an f-string",
                )
                + ' cw::unsupported("%")',
                POSTFIX,
            )
        return Emitted(f"cw::mod({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.POW:
        return Emitted(f"cw::pow({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MAT_MUL:
        return Emitted(
            ctx.gap("MatMult", "matrix multiplication has no C++ operator", node.span)
            + ' cw::unsupported("@")',
            POSTFIX,
        )
    if op is BinaryOp.ADD and _is(left_type, "list"):
        return Emitted(f"cw::concat({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MUL:
        if _is(left_type, "str") or _is(left_type, "list"):
            return Emitted(f"cw::repeat({left.text}, {right.text})", POSTFIX)
        if _is(right_type, "str") or _is(right_type, "list"):
            return Emitted(f"cw::repeat({right.text}, {left.text})", POSTFIX)
    spelling, precedence = BINARY_SPELLING[op]
    return Emitted(
        f"{left.at_least(precedence)} {spelling} {right.at_least(precedence + 1)}", precedence
    )


def _unary(node: IRUnary, ctx: CppContext, expected: IRType | None) -> Emitted:
    if node.op is UnaryOp.NOT:
        inner = _condition_emitted(node.operand, ctx)
        return Emitted(f"!{inner.at_least(UNARY)}", UNARY)
    spelling = {UnaryOp.NEG: "-", UnaryOp.POS: "+", UnaryOp.BIT_NOT: "~"}[node.op]
    operand = emit_expression(node.operand, ctx).at_least(UNARY)
    if spelling == "-" and operand.startswith("-"):
        operand = f"({operand})"
    return Emitted(f"{spelling}{operand}", UNARY)


def _is_none(node: IRNode) -> bool:
    return (
        isinstance(node, IRLiteral) and node.value is None and not node.meta.get("pythonEllipsis")
    )


def _compare(node: IRCompare, ctx: CppContext, expected: IRType | None) -> Emitted:
    op = node.op
    if op in (CompareOp.IN, CompareOp.NOT_IN):
        container = emit_expression(node.right, ctx).text
        item = emit_expression(node.left, ctx, element_type(_type(node.right))).text
        test = f"cw::contains({container}, {item})"
        return Emitted(f"!{test}", UNARY) if op is CompareOp.NOT_IN else Emitted(test, POSTFIX)
    if op in (CompareOp.IS, CompareOp.IS_NOT, CompareOp.EQ, CompareOp.NE) and (
        _is_none(node.right) or _is_none(node.left)
    ):
        subject = node.left if _is_none(node.right) else node.right
        rendered = emit_expression(subject, ctx).at_least(POSTFIX)
        positive = op in (CompareOp.IS, CompareOp.EQ)
        if is_value_optional(_type(subject), ctx):
            return Emitted(
                f"!{rendered}.has_value()" if positive else f"{rendered}.has_value()",
                UNARY if positive else POSTFIX,
            )
        spelling = "==" if positive else "!="
        return Emitted(f"{rendered} {spelling} nullptr", EQUALITY)
    info = ctx.class_of(_type(node.left))
    dunder = {
        CompareOp.EQ: "__eq__",
        CompareOp.NE: "__ne__",
        CompareOp.LT: "__lt__",
        CompareOp.LE: "__le__",
        CompareOp.GT: "__gt__",
        CompareOp.GE: "__ge__",
    }.get(op)
    if (
        info is not None
        and dunder
        and ctx.index is not None
        and ctx.index.find_method(info, dunder)
    ):
        left = emit_expression(node.left, ctx).at_least(POSTFIX)
        right = emit_expression(node.right, ctx).text
        return Emitted(f"{left}->{safe_identifier(dunder)}({right})", POSTFIX)
    spelling, precedence = COMPARE_SPELLING[op]
    left = emit_expression(node.left, ctx).at_least(precedence)
    right = emit_expression(node.right, ctx, _type(node.left)).at_least(precedence + 1)
    return Emitted(f"{left} {spelling} {right}", precedence)


def _logical(node: IRLogical, ctx: CppContext, expected: IRType | None) -> Emitted:
    is_and = node.op is LogicalOp.AND
    if not node.operands:
        return Emitted("true" if is_and else "false")
    value_type = _type(node)
    if (
        value_type is not None
        and value_type.kind is not TypeKind.UNKNOWN
        and not _is(value_type, "bool")
    ):
        # `a or b` used for its value: Python returns an operand, not a bool.
        result = emit_expression(node.operands[-1], ctx).text
        for operand in reversed(node.operands[:-1]):
            temp = ctx.temp("v")
            first = emit_expression(operand, ctx).text
            picked = f"{temp} : ({result})" if not is_and else f"({result}) : {temp}"
            result = f"[&] {{ auto {temp} = {first}; return cw::truthy({temp}) ? {picked}; }}()"
        return Emitted(result, POSTFIX)
    spelling, precedence = ("&&", LOGICAL_AND) if is_and else ("||", LOGICAL_OR)
    parts = [_condition_emitted(operand, ctx).at_least(precedence + 1) for operand in node.operands]
    return Emitted(f" {spelling} ".join(parts), precedence)


def _conditional(node: IRConditional, ctx: CppContext, expected: IRType | None) -> Emitted:
    test = _condition_emitted(node.test, ctx).at_least(CONDITIONAL + 1)
    target = expected if expected is not None else _type(node)
    then_value = emit_expression(node.then_value, ctx, target).at_least(CONDITIONAL)
    else_value = emit_expression(node.else_value, ctx, target).at_least(CONDITIONAL)
    return Emitted(f"{test} ? {then_value} : {else_value}", CONDITIONAL)


# -------------------------------------------------------------- member access


def _attribute(node: IRAttribute, ctx: CppContext, expected: IRType | None) -> Emitted:
    target = node.target
    name = node.name
    if is_super_call(target) and ctx.klass is not None:
        return Emitted(f"{_base_spelling(ctx.klass, ctx)}::{safe_identifier(name)}", POSTFIX)
    target_type = _type(target)
    module_ref = target_type.meta.get(MODULE_REF) if target_type else None
    if isinstance(target, IRName) and target.name not in ctx.locals:
        entry = ctx.scope_entry(target.name)
        if entry is not None and entry.kind is ScopeKind.EXTERNAL:
            if entry.qualified == "math" and name in _MATH_CONSTANTS:
                return Emitted(_MATH_CONSTANTS[name])
            return _external_name(f"{entry.qualified}.{name}", node, ctx)
    if isinstance(module_ref, str):
        rendered = emit_expression(target, ctx).text
        return Emitted(f"{rendered}::{safe_identifier(name)}", POSTFIX)
    if ctx.receiver and isinstance(target, IRName) and target.name == ctx.receiver:
        member = f"this->{safe_identifier(name)}"
        return Emitted(f"{member}()" if _is_property(ctx.klass, name, ctx) else member, POSTFIX)
    if target_type is not None and target_type.meta.get(CLASS_REF):
        return Emitted(f"{emit_expression(target, ctx).text}::{safe_identifier(name)}", POSTFIX)
    info = ctx.class_of(target_type)
    rendered = emit_expression(target, ctx).at_least(POSTFIX)
    if info is not None:
        accessor = "." if info.is_exception else "->"
        member = f"{rendered}{accessor}{safe_identifier(name)}"
        return Emitted(f"{member}()" if _is_property(info, name, ctx) else member, POSTFIX)
    if target_type is None or target_type.kind is TypeKind.UNKNOWN:
        ctx.warn(
            "CW0681",
            f"type of the object whose attribute {name!r} is read is unknown; '.' access is assumed",
            span=node.span,
            hint="annotate the variable so the right access can be generated",
        )
    return Emitted(f"{rendered}.{safe_identifier(name)}", POSTFIX)


def _is_property(info: ClassInfo | None, name: str, ctx: CppContext) -> bool:
    if info is None or ctx.index is None:
        return False
    found = ctx.index.find_method(info, name)
    return bool(found and name in found[0].properties)


def _base_spelling(info: ClassInfo, ctx: CppContext) -> str:
    if info.base and ctx.index is not None and info.base in ctx.index.classes:
        return ctx.class_spelling(ctx.index.classes[info.base])
    if info.builtin_base:
        return f"cw::{info.builtin_base}"
    return "cw::Object"


def _index(
    node: IRIndex, ctx: CppContext, expected: IRType | None, *, store: bool = False
) -> Emitted:
    container_type = _type(node.target)
    target = emit_expression(node.target, ctx)
    if _is(container_type, "tuple"):
        index = node.index
        if (
            isinstance(index, IRLiteral)
            and isinstance(index.value, int)
            and container_type is not None
        ):
            position = index.value + (len(container_type.args) if index.value < 0 else 0)
            return Emitted(f"std::get<{position}>({target.text})", POSTFIX)
        return Emitted(
            ctx.gap("Subscript", "a tuple can only be indexed by a constant in C++", node.span)
            + ' cw::unsupported("tuple index")',
            POSTFIX,
        )
    if _is(container_type, "dict"):
        key_type = container_type.args[0] if container_type and container_type.args else None
        key = emit_expression(node.index, ctx, key_type).text
        if store:
            return Emitted(f"{target.at_least(POSTFIX)}[{key}]", POSTFIX)
        return Emitted(f"cw::at({target.text}, {key})", POSTFIX)
    index_text = emit_expression(node.index, ctx).text
    if (
        isinstance(node.target, IRName)
        and isinstance(node.index, IRName)
        and (node.target.name, node.index.name) in ctx.unchecked
        and _is(container_type, "list")
    ):
        # Proven in bounds by the enclosing `for i in range(len(xs))`.
        return Emitted(
            f"{target.at_least(POSTFIX)}[static_cast<std::size_t>({index_text})]", POSTFIX
        )
    info = ctx.class_of(container_type)
    if info is not None and ctx.index is not None and ctx.index.find_method(info, "__getitem__"):
        return Emitted(f"{target.at_least(POSTFIX)}->get_item_({index_text})", POSTFIX)
    return Emitted(f"cw::at({target.text}, {index_text})", POSTFIX)


def emit_store_target(node: IRNode, ctx: CppContext) -> str:
    """Render an assignment target (dict stores insert rather than throw)."""
    if isinstance(node, IRIndex):
        return _index(node, ctx, None, store=True).text
    return emit_expression(node, ctx).text


def _slice(node: IRSlice, ctx: CppContext, expected: IRType | None) -> Emitted:
    target = emit_expression(node.target, ctx).text
    bounds = [
        emit_expression(item, ctx).text if item is not None else "cw::None"
        for item in (node.lower, node.upper)
    ]
    if node.step is not None:
        bounds.append(emit_expression(node.step, ctx).text)
    return Emitted(f"cw::slice({target}, {', '.join(bounds)})", POSTFIX)


# ----------------------------------------------------------------- literals


def _container_type(node: IRExpression, expected: IRType | None, ctx: CppContext) -> str | None:
    for candidate in (expected, _type(node)):
        rendered = render_type(candidate, ctx) if candidate is not None else None
        if rendered is not None and candidate is not None and candidate.kind is TypeKind.COLLECTION:
            return rendered
    return None


def _element_of(expected: IRType | None, position: int = 0) -> IRType | None:
    if (
        expected is None
        or expected.kind is not TypeKind.COLLECTION
        or len(expected.args) <= position
    ):
        return None
    return expected.args[position]


def _array(node: IRArrayLiteral, ctx: CppContext, expected: IRType | None) -> Emitted:
    element = _element_of(expected) or _element_of(_type(node))
    items = ", ".join(emit_expression(item, ctx, element).text for item in node.elements)
    spelled = _container_type(node, expected, ctx)
    if spelled is not None:
        return Emitted(f"{spelled}{{{items}}}", POSTFIX)
    if node.elements:
        return Emitted(f"std::vector{{{items}}}", POSTFIX)
    ctx.warn(
        "CW0683",
        "element type of an empty list is unknown",
        span=node.span,
        hint="annotate the variable",
    )
    return Emitted("{}")


def _set(node: IRSetLiteral, ctx: CppContext, expected: IRType | None) -> Emitted:
    element = _element_of(expected) or _element_of(_type(node))
    items = ", ".join(emit_expression(item, ctx, element).text for item in node.elements)
    spelled = _container_type(node, expected, ctx)
    if spelled is not None:
        return Emitted(f"{spelled}{{{items}}}", POSTFIX)
    return Emitted(f"std::unordered_set{{{items}}}", POSTFIX)


def _tuple(node: IRTupleLiteral, ctx: CppContext, expected: IRType | None) -> Emitted:
    target = expected if expected is not None and _is(expected, "tuple") else _type(node)
    items = []
    for position, item in enumerate(node.elements):
        items.append(emit_expression(item, ctx, _element_of(target, position)).text)
    spelled = render_type(target, ctx) if target is not None and _is(target, "tuple") else None
    if spelled is not None:
        return Emitted(f"{spelled}{{{', '.join(items)}}}", POSTFIX)
    return Emitted(f"std::make_tuple({', '.join(items)})", POSTFIX)


def _map(node: IRMapLiteral, ctx: CppContext, expected: IRType | None) -> Emitted:
    key_type = _element_of(expected, 0) or _element_of(_type(node), 0)
    value_type = _element_of(expected, 1) or _element_of(_type(node), 1)
    entries = ", ".join(
        f"{{{emit_expression(entry.key, ctx, key_type).text}, {emit_expression(entry.value, ctx, value_type).text}}}"
        for entry in node.entries
    )
    spelled = _container_type(node, expected, ctx)
    if spelled is not None:
        return Emitted(f"{spelled}{{{entries}}}", POSTFIX)
    ctx.warn(
        "CW0683",
        "key and value types of a dict literal are unknown",
        span=node.span,
        hint="annotate the variable",
    )
    return Emitted(f"{{{entries}}}")


def _template(node: IRTemplateString, ctx: CppContext, expected: IRType | None) -> Emitted:
    parts: list[str] = []
    for part in node.parts:
        if isinstance(part, IRLiteral) and isinstance(part.value, str):
            parts.append(c_string_literal(part.value))
        else:
            parts.append(emit_expression(part, ctx).text)
    if not parts:
        return Emitted('""s')
    return Emitted(f"cw::cat({', '.join(parts)})", POSTFIX)


def _lambda(
    node: IRLambda, ctx: CppContext, expected: IRType | None, *, capture: str = ""
) -> Emitted:
    names = [param.name for param in node.params]
    params = []
    for param in node.params:
        rendered = render_type(param.param_type, ctx)
        params.append(
            f"{rendered or 'auto'} {safe_identifier(param.name)}"
            if rendered
            else f"auto&& {safe_identifier(param.name)}"
        )
    saved = set(ctx.locals)
    ctx.locals.update(names)
    body = emit_expression(node.body, ctx).text
    ctx.locals = saved
    if not capture:
        capture = "=, this" if ctx.receiver else "="
    return Emitted(f"[{capture}]({', '.join(params)}) {{ return {body}; }}", POSTFIX)


def _await(node: IRAwait, ctx: CppContext, expected: IRType | None) -> Emitted:
    return Emitted(
        ctx.gap(
            "Await", "async/await is not translated to C++", node.span, "use std::future explicitly"
        )
        + ' cw::unsupported("await")',
        POSTFIX,
    )


def _comprehension(node: IRComprehension, ctx: CppContext, expected: IRType | None) -> Emitted:
    variable = safe_identifier(node.target)
    iterable = emit_expression(node.iterable, ctx).text
    iterable_type = _type(node.iterable)
    if _is(iterable_type, "dict"):
        iterable = f"cw::keys({iterable})"  # iterating a dict yields its keys
    elif _is(iterable_type, "str"):
        iterable = f"cw::list({iterable})"  # iterating a str yields 1-char strs
    saved = set(ctx.locals)
    ctx.locals.add(node.target)
    result_type = expected if expected is not None and _is(expected, node.form) else _type(node)
    element_ir = _element_of(result_type, 1 if node.form == "dict" else 0)
    element = emit_expression(node.element, ctx, element_ir).text
    key = (
        emit_expression(node.key, ctx, _element_of(result_type, 0)).text
        if node.key is not None
        else ""
    )
    predicate = condition(node.condition, ctx) if node.condition is not None else ""
    ctx.locals = saved
    lam = f"[&](const auto& {variable})"
    if node.form == "dict":
        name = "cw::dict_comprehend_if" if predicate else "cw::dict_comprehend"
        pieces = [iterable]
        if predicate:
            pieces.append(f"{lam} {{ return {predicate}; }}")
        pieces += [f"{lam} {{ return {key}; }}", f"{lam} {{ return {element}; }}"]
        return Emitted(f"{name}({', '.join(pieces)})", POSTFIX)
    base = "cw::set_comprehend" if node.form == "set" else "cw::comprehend"
    name = f"{base}_if" if predicate else base
    element_spelled = render_type(element_ir, ctx) if element_ir is not None else None
    explicit = f"<{element_spelled}>" if element_spelled else ""
    pieces = [iterable]
    if predicate:
        pieces.append(f"{lam} {{ return {predicate}; }}")
    pieces.append(f"{lam} {{ return {element}; }}")
    return Emitted(f"{name}{explicit}({', '.join(pieces)})", POSTFIX)


# --------------------------------------------------------------------- calls


def _argument(node: IRExpression, ctx: CppContext, expected: IRType | None = None) -> str:
    if isinstance(node, IRLambda):
        return _lambda(node, ctx, expected, capture="&").text
    return emit_expression(node, ctx, expected).text


def lower_arguments(
    params: list[IRParam],
    call: IRCall,
    ctx: CppContext,
    *,
    callee_name: str,
    module_of_callee: str,
) -> list[str]:
    """Match *call*'s arguments to *params* and render them positionally."""
    del module_of_callee  # defaults are rendered in the caller's context
    slots: list[str | None] = [None] * len(params)
    variadic = next((i for i, p in enumerate(params) if p.style == "var_positional"), None)
    extra: list[str] = []
    for position, argument in enumerate(call.args):
        if variadic is not None and position >= variadic:
            extra.append(_argument(argument, ctx))
            continue
        if position < len(params):
            slots[position] = _argument(argument, ctx, params[position].param_type)
        else:
            ctx.warn("CW0684", f"too many arguments in call to {callee_name!r}", span=call.span)
            extra.append(_argument(argument, ctx))
    for keyword in call.keywords:
        found = next(
            (
                i
                for i, p in enumerate(params)
                if p.name == keyword.name and p.style != "var_keyword"
            ),
            None,
        )
        if found is None:
            ctx.warn(
                "CW0685",
                f"{callee_name!r} has no parameter named {keyword.name!r}",
                span=call.span,
                hint="keyword arguments collected by **kwargs are not translated",
            )
            continue
        slots[found] = _argument(keyword.value, ctx, params[found].param_type)
    if call.star_args or call.star_keywords:
        ctx.gap(
            "Starred",
            "argument unpacking (*args / **kwargs at a call) is not translated",
            call.span,
        )
    if variadic is not None:
        element = params[variadic].param_type
        spelled = render_type(element, ctx)
        container = f"std::vector<{spelled}>" if spelled else "std::vector"
        slots[variadic] = f"{container}{{{', '.join(extra)}}}" if (extra or spelled) else "{}"
        extra = []
    # Trailing parameters left unset use their C++ default arguments; a gap in
    # the middle is filled with the Python default expression.
    last = max((i for i, slot in enumerate(slots) if slot is not None), default=-1)
    rendered: list[str] = []
    for position in range(last + 1):
        slot = slots[position]
        if slot is None:
            default = params[position].default
            if default is None:
                ctx.warn(
                    "CW0686",
                    f"missing argument {params[position].name!r} in call to {callee_name!r}",
                    span=call.span,
                )
                slot = 'cw::unsupported("missing argument")'
            else:
                slot = emit_expression(default, ctx, params[position].param_type).text
        rendered.append(slot)
    return rendered + extra


def _call(node: IRCall, ctx: CppContext, expected: IRType | None) -> Emitted:
    callee = node.callee
    if (
        isinstance(callee, IRAttribute)
        and callee.name == "__init__"
        and is_super_call(callee.target)
    ):
        return Emitted(
            ctx.gap(
                "Call",
                "super().__init__() must be the first statement of a constructor in C++",
                node.span,
            )
            + ' cw::unsupported("super().__init__")',
            POSTFIX,
        )

    if isinstance(callee, IRName) and callee.name not in ctx.locals and callee.name != ctx.receiver:
        entry = ctx.scope_entry(callee.name)
        if entry is None:
            if callee.name in BUILTIN_EXCEPTIONS:
                exception_args = ", ".join(_argument(item, ctx) for item in node.args)
                return Emitted(f"cw::{callee.name}({exception_args})", POSTFIX)
            mapped = _builtin(callee.name, node, ctx, expected)
            if mapped is not None:
                return mapped
        elif entry.kind is ScopeKind.CLASS and ctx.index is not None:
            return _construct(
                ctx.index.classes[entry.qualified],
                ctx.entry_spelling(entry, callee.name),
                node,
                ctx,
            )
        elif entry.kind is ScopeKind.FUNCTION and ctx.index is not None:
            function = ctx.index.functions[entry.qualified].node
            args = lower_arguments(
                function.params, node, ctx, callee_name=callee.name, module_of_callee=entry.module
            )
            return Emitted(f"{ctx.entry_spelling(entry, callee.name)}({', '.join(args)})", POSTFIX)
        elif entry.kind is ScopeKind.EXTERNAL:
            return _external_call(entry.qualified, node, ctx)

    if (
        ctx.class_receiver
        and isinstance(callee, IRName)
        and callee.name == ctx.class_receiver
        and ctx.klass
    ):
        return _construct(ctx.klass, safe_identifier(ctx.klass.name), node, ctx)

    if isinstance(callee, IRAttribute):
        method = _method_call(node, callee, ctx, expected)
        if method is not None:
            return method

    callee_text = emit_expression(callee, ctx).at_least(POSTFIX)
    if node.meta.get(CONSTRUCTS_CLASS):
        info = ctx.class_of(_type(node))
        if info is not None:
            return _construct(info, callee_text, node, ctx)
    plain_args = [_argument(item, ctx) for item in node.args]
    if node.keywords:
        ctx.warn(
            "CW0685",
            "keyword arguments to a callee with no known signature are dropped",
            span=node.span,
        )
    return Emitted(f"{callee_text}({', '.join(plain_args)})", POSTFIX)


def _construct(info: ClassInfo, spelled: str, node: IRCall, ctx: CppContext) -> Emitted:
    found = ctx.index.constructor(info) if ctx.index is not None else None
    if found is None and (info.is_exception or info.builtin_base):
        # Inherits the builtin constructor, which takes the message.
        args = [_argument(item, ctx) for item in node.args]
    else:
        params = found[1].params if found is not None else []
        args = lower_arguments(
            params, node, ctx, callee_name=info.name, module_of_callee=info.module
        )
    if info.is_exception:
        return Emitted(f"{spelled}({', '.join(args)})", POSTFIX)
    return Emitted(f"std::make_shared<{spelled}>({', '.join(args)})", POSTFIX)


def _method_signature(info: ClassInfo, name: str, ctx: CppContext) -> IRFunction | None:
    if ctx.index is None:
        return None
    found = ctx.index.find_method(info, name)
    return found[1] if found else None


def _method_call(
    node: IRCall, callee: IRAttribute, ctx: CppContext, expected: IRType | None
) -> Emitted | None:
    target = callee.target
    name = callee.name
    target_type = _type(target)

    if is_super_call(target) and ctx.klass is not None and ctx.index is not None:
        base = ctx.index.classes.get(ctx.klass.base) if ctx.klass.base else None
        method = _method_signature(base, name, ctx) if base else None
        args = (
            lower_arguments(
                method.params, node, ctx, callee_name=name, module_of_callee=base.module
            )
            if method and base
            else [_argument(a, ctx) for a in node.args]
        )
        return Emitted(
            f"{_base_spelling(ctx.klass, ctx)}::{safe_identifier(name)}({', '.join(args)})", POSTFIX
        )

    if isinstance(target, IRName) and target.name not in ctx.locals:
        entry = ctx.scope_entry(target.name)
        if entry is not None and entry.kind is ScopeKind.EXTERNAL:
            return _external_call(f"{entry.qualified}.{name}", node, ctx)

    module_ref = target_type.meta.get(MODULE_REF) if target_type else None
    if isinstance(module_ref, str) and ctx.index is not None:
        entry = ctx.index.lookup(module_ref, name)
        prefix = emit_expression(target, ctx).text
        if entry is not None and entry.kind is ScopeKind.CLASS:
            return _construct(
                ctx.index.classes[entry.qualified], f"{prefix}::{safe_identifier(name)}", node, ctx
            )
        if entry is not None and entry.kind is ScopeKind.FUNCTION:
            function = ctx.index.functions[entry.qualified].node
            args = lower_arguments(
                function.params, node, ctx, callee_name=name, module_of_callee=entry.module
            )
            return Emitted(f"{prefix}::{safe_identifier(name)}({', '.join(args)})", POSTFIX)
        return None

    if target_type is not None and target_type.meta.get(CLASS_REF):
        info = ctx.class_of(target_type)
        if info is not None:
            method = _method_signature(info, name, ctx)
            args = (
                lower_arguments(
                    method.params, node, ctx, callee_name=name, module_of_callee=info.module
                )
                if method
                else [_argument(a, ctx) for a in node.args]
            )
            return Emitted(
                f"{emit_expression(target, ctx).text}::{safe_identifier(name)}({', '.join(args)})",
                POSTFIX,
            )

    is_self = bool(ctx.receiver and isinstance(target, IRName) and target.name == ctx.receiver)
    info = ctx.klass if is_self else ctx.class_of(target_type)
    if info is not None:
        method = _method_signature(info, name, ctx)
        args = (
            lower_arguments(
                method.params, node, ctx, callee_name=name, module_of_callee=info.module
            )
            if method
            else [_argument(a, ctx) for a in node.args]
        )
        if is_self:
            receiver = "this->"
        else:
            receiver = emit_expression(target, ctx).at_least(POSTFIX) + (
                "." if info.is_exception else "->"
            )
        return Emitted(f"{receiver}{safe_identifier(name)}({', '.join(args)})", POSTFIX)

    builtin_kind = _builtin_kind(target_type)
    if builtin_kind is not None:
        return _builtin_method(builtin_kind, node, callee, ctx)
    return None


def _builtin_kind(node_type: IRType | None) -> str | None:
    if node_type is None:
        return None
    if node_type.kind is TypeKind.PRIMITIVE and node_type.name == "str":
        return "str"
    if node_type.kind is TypeKind.COLLECTION and node_type.name in ("list", "dict", "set"):
        return node_type.name
    return None


def _builtin_method(kind: str, node: IRCall, callee: IRAttribute, ctx: CppContext) -> Emitted:
    name = callee.name
    receiver = emit_expression(callee.target, ctx)
    receiver_type = _type(callee.target)
    element = element_type(receiver_type)
    args = [
        _argument(
            item,
            ctx,
            element if name in ("append", "add", "remove", "discard", "index", "count") else None,
        )
        for item in node.args
    ]
    if name not in _RUNTIME_METHODS.get(kind, frozenset()):
        marker = ctx.gap("Call", f"{kind}.{name}() has no C++ mapping in this build", node.span)
        return Emitted(f"{marker} cw::unsupported({c_string_literal(f'{kind}.{name}')})", POSTFIX)
    if kind == "list" and name == "append":
        return Emitted(f"{receiver.at_least(POSTFIX)}.push_back({', '.join(args)})", POSTFIX)
    if kind == "list" and name == "sort":
        key = next((k.value for k in node.keywords if k.name == "key"), None)
        reverse = next((k.value for k in node.keywords if k.name == "reverse"), None)
        reverse_text = condition(reverse, ctx) if reverse is not None else "false"
        if key is not None:
            return Emitted(
                f"cw::sort_by({receiver.text}, {_argument(key, ctx)}, {reverse_text})", POSTFIX
            )
        return Emitted(f"cw::sort({receiver.text}, {reverse_text})", POSTFIX)
    if kind == "set" and name == "add":
        return Emitted(f"{receiver.at_least(POSTFIX)}.insert({', '.join(args)})", POSTFIX)
    if kind == "str" and name == "join":
        return Emitted(f"cw::join({receiver.text}, {', '.join(args)})", POSTFIX)
    if kind == "set" and name == "union":
        name = "union_"
    if node.keywords:
        ctx.warn(
            "CW0685",
            f"keyword arguments to {kind}.{callee.name}() are not translated",
            span=node.span,
        )
    return Emitted(f"cw::{name}({', '.join([receiver.text, *args])})", POSTFIX)


def _external_call(qualified: str, node: IRCall, ctx: CppContext) -> Emitted:
    module, _, member = qualified.rpartition(".")
    args = [emit_expression(item, ctx).text for item in node.args]
    if module == "math":
        if member == "log":
            if len(args) == 2:
                return Emitted(f"(std::log({args[0]}) / std::log({args[1]}))", PRIMARY)
            return Emitted(f"std::log({args[0]})", POSTFIX)
        template = _MATH_FUNCTIONS.get(member)
        if template is not None and template.count("{") <= len(args):
            ctx.need("cmath")
            return Emitted(template.format(*args), POSTFIX)
    callee = _external_name(qualified, node, ctx).text
    return Emitted(f"{callee}({', '.join(args)})", POSTFIX)


# ------------------------------------------------------------------ builtins


def _builtin(name: str, node: IRCall, ctx: CppContext, expected: IRType | None) -> Emitted | None:
    args = node.args
    rendered = [_argument(item, ctx) for item in args]
    joined = ", ".join(rendered)
    keywords = {keyword.name: keyword.value for keyword in node.keywords}

    def call(function: str) -> Emitted:
        return Emitted(f"{function}({joined})", POSTFIX)

    if name in UNMAPPED_BUILTINS:
        marker = ctx.gap(
            "Call", f"builtin {name}() has no C++ mapping", node.span, UNMAPPED_BUILTINS[name]
        )
        return Emitted(f"{marker} cw::unsupported({c_string_literal(name)})", POSTFIX)
    if name == "print":
        if keywords:
            ctx.warn(
                "CW0687",
                "print() keyword arguments (sep, end, file) are not translated",
                span=node.span,
            )
        return call("cw::print")
    simple: dict[str, str] = {
        "len": "cw::len",
        "str": "cw::str",
        "repr": "cw::repr",
        "abs": "cw::abs",
        "ord": "cw::ord",
        "chr": "cw::chr",
        "any": "cw::any",
        "all": "cw::all",
        "pow": "cw::pow",
    }
    if name in simple:
        return call(simple[name])
    if name == "int":
        if len(args) > 1:
            return Emitted(
                ctx.gap("Call", "int() with a base is not translated", node.span)
                + ' cw::unsupported("int")',
                POSTFIX,
            )
        return call("cw::to_int") if args else Emitted("INT64_C(0)")
    if name == "float":
        return call("cw::to_float") if args else Emitted("0.0")
    if name == "bool":
        return Emitted(condition(args[0], ctx), POSTFIX) if args else Emitted("false")
    if name == "round":
        return call("cw::round")
    if name in ("min", "max"):
        if keywords:
            ctx.warn("CW0687", f"{name}() keyword arguments are not translated", span=node.span)
        return call(f"cw::{name}")
    if name == "sum":
        if len(args) == 2:
            return Emitted(f"(cw::sum({rendered[0]}) + {rendered[1]})", PRIMARY)
        return call("cw::sum")
    if name == "sorted":
        reverse = keywords.get("reverse")
        reverse_text = condition(reverse, ctx) if reverse is not None else "false"
        key = keywords.get("key")
        if key is not None:
            return Emitted(
                f"cw::sorted_by({rendered[0]}, {_argument(key, ctx)}, {reverse_text})", POSTFIX
            )
        return Emitted(f"cw::sorted({rendered[0]}, {reverse_text})", POSTFIX)
    if name in ("list", "set", "dict", "tuple"):
        if not args:
            spelled = render_type(expected, ctx) if expected is not None else None
            if spelled is None:
                spelled = render_type(_type(node), ctx)
            if spelled is None:
                ctx.warn("CW0683", f"element type of an empty {name}() is unknown", span=node.span)
                return Emitted("{}")
            return Emitted(f"{spelled}{{}}", POSTFIX)
        if name == "list":
            return call("cw::list")
        if name == "set":
            return call("cw::to_set")
        if name == "dict":
            return call("cw::copy")
        return Emitted(
            ctx.gap("Call", "tuple() of an iterable has no C++ mapping", node.span)
            + ' cw::unsupported("tuple")',
            POSTFIX,
        )
    if name == "range":
        return call("cw::range")
    if name == "divmod" and len(rendered) == 2:
        return Emitted(
            f"std::make_tuple(cw::floordiv({rendered[0]}, {rendered[1]}), cw::mod({rendered[0]}, {rendered[1]}))",
            POSTFIX,
        )
    if name == "isinstance" and len(args) == 2:
        return _isinstance(node, ctx)
    if name == "super":
        return None
    return None


def _isinstance(node: IRCall, ctx: CppContext) -> Emitted:
    subject, kinds = node.args
    candidates = kinds.elements if isinstance(kinds, IRTupleLiteral) else [kinds]
    subject_text = emit_expression(subject, ctx).text
    subject_type = _type(subject)
    tests: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, IRName):
            tests.append("false")
            continue
        info = ctx.class_of(candidate.value_type)
        if info is not None and not info.is_exception:
            tests.append(
                f"(std::dynamic_pointer_cast<{ctx.class_spelling(info)}>({subject_text}) != nullptr)"
            )
            continue
        builtin = {
            "int": "int",
            "float": "float",
            "str": "str",
            "bool": "bool",
            "list": "list",
            "dict": "dict",
            "set": "set",
        }.get(candidate.name)
        if (
            builtin is not None
            and subject_type is not None
            and subject_type.kind is not TypeKind.UNKNOWN
        ):
            # C++ types are static: the answer is known when the program is compiled.
            matches = subject_type.name == builtin or (
                builtin == "int" and subject_type.name == "bool"
            )
            tests.append("true" if matches else "false")
            continue
        ctx.warn(
            "CW0688",
            f"isinstance check against {candidate.name!r} cannot be decided statically",
            span=node.span,
        )
        tests.append("false")
    if len(tests) == 1:
        return Emitted(tests[0], PRIMARY)
    return Emitted(" || ".join(tests), LOGICAL_OR)


_Handler = Callable[[IRNode, CppContext, IRType | None], Emitted]

_HANDLERS: dict[type[IRNode], _Handler] = {
    IRLiteral: _literal,  # type: ignore[dict-item]
    IRName: _name,  # type: ignore[dict-item]
    IRBinary: _binary,  # type: ignore[dict-item]
    IRUnary: _unary,  # type: ignore[dict-item]
    IRCompare: _compare,  # type: ignore[dict-item]
    IRLogical: _logical,  # type: ignore[dict-item]
    IRConditional: _conditional,  # type: ignore[dict-item]
    IRCall: _call,  # type: ignore[dict-item]
    IRAttribute: _attribute,  # type: ignore[dict-item]
    IRIndex: _index,  # type: ignore[dict-item]
    IRSlice: _slice,  # type: ignore[dict-item]
    IRArrayLiteral: _array,  # type: ignore[dict-item]
    IRSetLiteral: _set,  # type: ignore[dict-item]
    IRTupleLiteral: _tuple,  # type: ignore[dict-item]
    IRMapLiteral: _map,  # type: ignore[dict-item]
    IRTemplateString: _template,  # type: ignore[dict-item]
    IRLambda: _lambda,  # type: ignore[dict-item]
    IRAwait: _await,  # type: ignore[dict-item]
    IRComprehension: _comprehension,  # type: ignore[dict-item]
}
