"""IR expressions to C expressions.

C has no overloading, so every operation is spelled for the operand types the
inference pass found: ``a // b`` on ints is ``cw_floordiv_i64(a, b)``,
``xs[i]`` on a ``list[int]`` is ``cw_list_i64_get(xs, i)``, and ``str(x)`` on
a float is ``cw_str_f64(x)``.  An operation whose operand types are unknown
cannot be spelled and is reported as a gap.

Comprehensions become a loop placed before the statement that uses them
(``ctx.pre``), because C has no expression that can contain a loop.

Methods that are overridden anywhere in the hierarchy are called through a
function pointer stored in the object, so dispatch matches Python's; every
other method is a direct call the compiler can inline.
"""

from __future__ import annotations

from collections.abc import Callable

from codeweaver.backends.c.context import CContext
from codeweaver.backends.c.naming import safe_identifier
from codeweaver.backends.c.types import list_kind, render_type
from codeweaver.backends.common.clike import (
    ADDITIVE,
    BINARY_SPELLING,
    COMPARE_SPELLING,
    CONDITIONAL,
    EQUALITY,
    LOGICAL_AND,
    LOGICAL_OR,
    POSTFIX,
    PRIMARY,
    RELATIONAL,
    UNARY,
    Emitted,
)
from codeweaver.backends.common.context import UNSUPPORTED_MARKER
from codeweaver.backends.common.strings import c_string_literal
from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS, ClassInfo, ScopeKind
from codeweaver.ir.base import CONSTRUCTS_CLASS, IRNode, IRUnsupported, walk
from codeweaver.ir.declarations import IRFunction
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
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
    IRName,
    IRParam,
    IRSlice,
    IRTemplateString,
    IRUnary,
)
from codeweaver.ir.operators import BinaryOp, CompareOp, LogicalOp, UnaryOp
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.inference import CLASS_REF, MODULE_REF, element_type

__all__ = [
    "condition",
    "dispatch_root",
    "emit_expression",
    "emit_store",
    "field_home",
    "is_dispatched",
    "lower_arguments",
    "member_path",
    "string_of",
]

_MATH_FUNCTIONS: dict[str, str] = {
    "sqrt": "sqrt({0})",
    "exp": "exp({0})",
    "log10": "log10({0})",
    "log2": "log2({0})",
    "sin": "sin({0})",
    "cos": "cos({0})",
    "tan": "tan({0})",
    "asin": "asin({0})",
    "acos": "acos({0})",
    "atan": "atan({0})",
    "atan2": "atan2({0}, {1})",
    "hypot": "hypot({0}, {1})",
    "fabs": "fabs({0})",
    "pow": "pow({0}, {1})",
    "floor": "(int64_t)floor({0})",
    "ceil": "(int64_t)ceil({0})",
    "trunc": "(int64_t)trunc({0})",
    "isnan": "isnan({0})",
    "isinf": "isinf({0})",
    "isfinite": "isfinite({0})",
}
_MATH_CONSTANTS: dict[str, str] = {
    "pi": "3.141592653589793",
    "e": "2.718281828459045",
    "tau": "6.283185307179586",
    "inf": "INFINITY",
    "nan": "NAN",
}


# ------------------------------------------------------------------ helpers


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


def _unsupported(ctx: CContext, kind: str, reason: str, node: IRNode, hint: str = "") -> Emitted:
    marker = ctx.gap(kind, reason, node.span, hint)
    return Emitted(
        f"({marker} cw_unsupported({c_string_literal(kind, trigraphs=True)}), 0)", PRIMARY
    )


def _is_gap(rendered: Emitted) -> bool:
    return UNSUPPORTED_MARKER in rendered.text


def _kind(list_type: IRType | None, ctx: CContext) -> str | None:
    if not _is(list_type, "list") or list_type is None or not list_type.args:
        return None
    return list_kind(list_type.args[0], ctx)


def string_of(node: IRNode, ctx: CContext) -> str | None:
    """``str(node)`` as a C expression, or ``None`` if the type has no spelling."""
    node_type = _type(node)
    text = emit_expression(node, ctx).text
    if isinstance(node, IRLiteral) and node.value is None:
        return '"None"'
    if isinstance(node, IRLiteral) and isinstance(node.value, str):
        return text
    if node_type is None:
        return None
    if node_type.kind is TypeKind.PRIMITIVE:
        function = {
            "int": "cw_str_i64",
            "float": "cw_str_f64",
            "bool": "cw_str_bool",
            "str": "cw_str_str",
        }.get(node_type.name)
        return f"{function}({text})" if function else None
    kind = _kind(node_type, ctx)
    if kind is not None:
        return f"cw_list_{kind}_repr({text})"
    info = ctx.class_of(node_type)
    if info is not None and ctx.index is not None:
        found = ctx.index.find_method(info, "__str__")
        if found is not None:
            return method_call_text(info, "__str__", text, [], ctx)
    return None


# --------------------------------------------------------------- dispatch


def dispatch_root(info: ClassInfo, method: str, ctx: CContext) -> ClassInfo | None:
    """The topmost class of *info*'s ancestry that declares *method*."""
    if ctx.index is None:
        return None
    root = None
    for klass in ctx.index.ancestors(info):
        if method in klass.methods:
            root = klass
    return root


def is_dispatched(info: ClassInfo, method: str, ctx: CContext) -> bool:
    root = dispatch_root(info, method, ctx)
    return bool(
        root is not None and ctx.index is not None and ctx.index.is_overridden(root, method)
    )


def member_path(info: ClassInfo, owner: ClassInfo, ctx: CContext) -> str:
    """``base.base.`` from *info* to the embedded struct of *owner*."""
    if ctx.index is None:
        return ""
    for depth, klass in enumerate(ctx.index.ancestors(info)):
        if klass.qualified == owner.qualified:
            return "base." * depth
    return ""


def method_call_text(
    info: ClassInfo, method: str, receiver: str, args: list[str], ctx: CContext
) -> str:
    """Call *method* on *receiver*, a pointer whose static class is *info*."""
    found = ctx.index.find_method(info, method) if ctx.index is not None else None
    name = safe_identifier(method)
    if found is None:
        return f"{ctx.class_symbol(info)}_{name}({', '.join([receiver, *args])})"
    owner, function = found
    if function.is_static or function.meta.get("pythonClassMethod"):
        return f"{ctx.class_symbol(owner)}_{name}({', '.join(args)})"
    if is_dispatched(info, method, ctx):
        root = dispatch_root(info, method, ctx)
        assert root is not None
        root_symbol = ctx.class_symbol(root)
        cast = f"(({root_symbol}*)({receiver}))"
        return f"{cast}->vf_{name}({', '.join([cast, *args])})"
    cast = (
        receiver
        if owner.qualified == info.qualified
        else f"(({ctx.class_symbol(owner)}*)({receiver}))"
    )
    return f"{ctx.class_symbol(owner)}_{name}({', '.join([cast, *args])})"


# --------------------------------------------------------------------- entry


def emit_expression(node: IRNode | None, ctx: CContext, expected: IRType | None = None) -> Emitted:
    if node is None:
        return Emitted("NULL")
    if isinstance(node, IRUnsupported):
        marker = ctx.record_gap(node)
        return Emitted(
            f"({marker} cw_unsupported({c_string_literal(node.source_kind, trigraphs=True)}), 0)",
            PRIMARY,
        )
    handler = _HANDLERS.get(type(node))
    if handler is None:
        return _unsupported(ctx, node.kind, "no C mapping for this expression", node)
    return handler(node, ctx, expected)


def _truthy(node: IRNode | None, ctx: CContext) -> Emitted:
    node_type = _type(node)
    rendered = emit_expression(node, ctx)
    if (
        _is(node_type, "bool")
        or isinstance(node, IRCompare)
        or (isinstance(node, IRUnary) and node.op is UnaryOp.NOT)
    ):
        return rendered
    if isinstance(node, IRLogical) and not _used_for_value(node):
        return rendered
    if _is(node_type, "int") or _is(node_type, "float"):
        return Emitted(f"{rendered.at_least(EQUALITY + 1)} != 0", EQUALITY)
    if _is(node_type, "str"):
        return Emitted(f"cw_str_truthy({rendered.text})", POSTFIX)
    if _kind(node_type, ctx) is not None:
        return Emitted(f"{rendered.at_least(POSTFIX)}->len != 0", EQUALITY)
    if render_type(node_type, ctx) is not None:
        return Emitted(f"{rendered.at_least(EQUALITY + 1)} != NULL", EQUALITY)
    return _unsupported(
        ctx,
        "Truthiness",
        "the truth value of a value of unknown type has no C spelling",
        node or IRLiteral(None),
    )


def condition(node: IRNode | None, ctx: CContext) -> str:
    return _truthy(node, ctx).text


def _used_for_value(node: IRLogical) -> bool:
    node_type = _type(node)
    return (
        node_type is not None
        and node_type.kind is not TypeKind.UNKNOWN
        and not _is(node_type, "bool")
    )


# ------------------------------------------------------------------ literals


def _literal(node: IRLiteral, ctx: CContext, expected: IRType | None) -> Emitted:
    value = node.value
    if value is None:
        return Emitted("NULL")
    if isinstance(value, bool):
        return Emitted("true" if value else "false")
    if isinstance(value, int):
        if expected is not None and _is(expected, "float"):
            return Emitted(repr(float(value)), UNARY if value < 0 else PRIMARY)
        if -(2**31) <= value < 2**31:
            return Emitted(str(value), UNARY if value < 0 else PRIMARY)
        if -(2**63) < value < 2**63:
            return Emitted(f"INT64_C({value})", POSTFIX)
        return _unsupported(ctx, "Constant", "integer literal does not fit in int64_t", node)
    if isinstance(value, float):
        if value != value:  # noqa: PLR0124 - NaN check
            return Emitted("NAN")
        if value in (float("inf"), float("-inf")):
            return Emitted("INFINITY" if value > 0 else "(-INFINITY)")
        text = repr(value)
        if "." not in text and "e" not in text:
            text += ".0"
        return Emitted(text, UNARY if text.startswith("-") else PRIMARY)
    if isinstance(value, str):
        return Emitted(c_string_literal(value, trigraphs=True))
    return _unsupported(
        ctx, "Constant", f"literal of type {type(value).__name__} has no C spelling", node
    )


def _name(node: IRName, ctx: CContext, expected: IRType | None) -> Emitted:
    name = node.name
    if ctx.receiver and name == ctx.receiver:
        return Emitted("self")
    if name in ctx.locals:
        return Emitted(safe_identifier(name))
    entry = ctx.scope_entry(name)
    if entry is not None:
        if entry.kind in (ScopeKind.FUNCTION, ScopeKind.VARIABLE, ScopeKind.CLASS):
            return Emitted(ctx.symbol(entry.module, entry.name))
        if entry.kind is ScopeKind.EXTERNAL:
            module, _, member = entry.qualified.rpartition(".")
            if module == "math" and member in _MATH_CONSTANTS:
                return Emitted(_MATH_CONSTANTS[member])
            return _unsupported(
                ctx,
                "Name",
                f"{entry.qualified!r} comes from an external module with no C mapping",
                node,
            )
    return _unsupported(
        ctx,
        "Name",
        f"{name!r} is not defined at module level (a nested function or a builtin used as a value)",
        node,
        "move the definition to module level",
    )


# ----------------------------------------------------------------- operators


def _binary(node: IRBinary, ctx: CContext, expected: IRType | None) -> Emitted:
    left_type, right_type = _type(node.left), _type(node.right)
    both_int = _is_int(left_type) and _is_int(right_type)
    op = node.op
    left = emit_expression(node.left, ctx)
    right = emit_expression(node.right, ctx)
    if op is BinaryOp.DIV:
        return Emitted(f"cw_truediv({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.FLOOR_DIV:
        return Emitted(
            f"cw_floordiv_{'i64' if both_int else 'f64'}({left.text}, {right.text})", POSTFIX
        )
    if op is BinaryOp.MOD:
        if _is(left_type, "str"):
            return _unsupported(
                ctx,
                "BinOp",
                "printf-style string formatting is not translated",
                node,
                "use an f-string",
            )
        return Emitted(f"cw_mod_{'i64' if both_int else 'f64'}({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.POW:
        return Emitted(f"{'cw_pow_i64' if both_int else 'pow'}({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MAT_MUL:
        return _unsupported(ctx, "MatMult", "matrix multiplication has no C operator", node)
    if op is BinaryOp.ADD and _is(left_type, "str"):
        return Emitted(f"cw_str_concat({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.ADD and _kind(left_type, ctx):
        return Emitted(
            f"cw_list_{_kind(left_type, ctx)}_concat({left.text}, {right.text})", POSTFIX
        )
    if op is BinaryOp.MUL:
        for sequence, count, sequence_type in ((left, right, left_type), (right, left, right_type)):
            if _is(sequence_type, "str"):
                return Emitted(f"cw_str_repeat({sequence.text}, {count.text})", POSTFIX)
            kind = _kind(sequence_type, ctx)
            if kind:
                return Emitted(f"cw_list_{kind}_repeat({sequence.text}, {count.text})", POSTFIX)
    spelling, precedence = BINARY_SPELLING[op]
    return Emitted(
        f"{left.at_least(precedence)} {spelling} {right.at_least(precedence + 1)}", precedence
    )


def _unary(node: IRUnary, ctx: CContext, expected: IRType | None) -> Emitted:
    if node.op is UnaryOp.NOT:
        return Emitted(f"!{_truthy(node.operand, ctx).at_least(UNARY)}", UNARY)
    spelling = {UnaryOp.NEG: "-", UnaryOp.POS: "+", UnaryOp.BIT_NOT: "~"}[node.op]
    operand = emit_expression(node.operand, ctx).at_least(UNARY)
    if operand.startswith(spelling):
        operand = f"({operand})"
    return Emitted(f"{spelling}{operand}", UNARY)


def _is_none(node: IRNode) -> bool:
    return isinstance(node, IRLiteral) and node.value is None


def contains_text(container: IRNode, item: IRNode, ctx: CContext) -> Emitted:
    container_type = _type(container)
    target = emit_expression(container, ctx).text
    value = emit_expression(item, ctx, element_type(container_type)).text
    if _is(container_type, "str"):
        return Emitted(f"cw_str_contains({target}, {value})", POSTFIX)
    kind = _kind(container_type, ctx)
    if kind is not None:
        return Emitted(f"cw_list_{kind}_contains({target}, {value})", POSTFIX)
    return _unsupported(ctx, "Compare", "'in' is only translated for lists and strings", container)


def _compare(node: IRCompare, ctx: CContext, expected: IRType | None) -> Emitted:
    op = node.op
    if op in (CompareOp.IN, CompareOp.NOT_IN):
        test = contains_text(node.right, node.left, ctx)
        return Emitted(f"!{test.at_least(UNARY)}", UNARY) if op is CompareOp.NOT_IN else test
    if op in (CompareOp.IS, CompareOp.IS_NOT, CompareOp.EQ, CompareOp.NE) and (
        _is_none(node.left) or _is_none(node.right)
    ):
        subject = node.left if _is_none(node.right) else node.right
        positive = op in (CompareOp.IS, CompareOp.EQ)
        rendered = emit_expression(subject, ctx).at_least(EQUALITY + 1)
        return Emitted(f"{rendered} {'==' if positive else '!='} NULL", EQUALITY)
    left_type = _type(node.left)
    spelling, precedence = COMPARE_SPELLING[op]
    if (_is(left_type, "str") or _is(_type(node.right), "str")) and op not in (
        CompareOp.IS,
        CompareOp.IS_NOT,
    ):
        left = emit_expression(node.left, ctx).text
        right = emit_expression(node.right, ctx).text
        if op in (CompareOp.EQ, CompareOp.NE):
            equal = f"cw_str_eq({left}, {right})"
            return Emitted(f"!{equal}", UNARY) if op is CompareOp.NE else Emitted(equal, POSTFIX)
        return Emitted(f"cw_str_cmp({left}, {right}) {spelling} 0", precedence)
    info = ctx.class_of(left_type)
    if info is not None and op in (CompareOp.EQ, CompareOp.NE) and ctx.index is not None:
        if ctx.index.find_method(info, "__eq__"):
            left = emit_expression(node.left, ctx).text
            right = emit_expression(node.right, ctx).text
            equal = method_call_text(info, "__eq__", left, [right], ctx)
            return Emitted(f"!{equal}", UNARY) if op is CompareOp.NE else Emitted(equal, POSTFIX)
    left = emit_expression(node.left, ctx).at_least(precedence)
    right = emit_expression(node.right, ctx, left_type).at_least(precedence + 1)
    return Emitted(f"{left} {spelling} {right}", precedence)


def _pure(node: IRNode | None) -> bool:
    return node is None or not any(
        isinstance(item, IRCall | IRComprehension) for item in walk(node)
    )


def _logical(node: IRLogical, ctx: CContext, expected: IRType | None) -> Emitted:
    is_and = node.op is LogicalOp.AND
    if not node.operands:
        return Emitted("true" if is_and else "false")
    if _used_for_value(node):
        if not all(_pure(operand) for operand in node.operands[:-1]):
            return _unsupported(
                ctx,
                "BoolOp",
                "'and'/'or' used for its value needs a temporary for an operand with side effects",
                node,
                "assign the operand to a variable first",
            )
        result = emit_expression(node.operands[-1], ctx).at_least(CONDITIONAL)
        for operand in reversed(node.operands[:-1]):
            test = _truthy(operand, ctx).at_least(CONDITIONAL + 1)
            value = emit_expression(operand, ctx).at_least(CONDITIONAL)
            result = (
                f"({test} ? {result} : {value})" if is_and else f"({test} ? {value} : {result})"
            )
        return Emitted(result, PRIMARY)
    spelling, precedence = ("&&", LOGICAL_AND) if is_and else ("||", LOGICAL_OR)
    parts = [_truthy(operand, ctx).at_least(precedence + 1) for operand in node.operands]
    return Emitted(f" {spelling} ".join(parts), precedence)


def _conditional(node: IRConditional, ctx: CContext, expected: IRType | None) -> Emitted:
    test = _truthy(node.test, ctx).at_least(CONDITIONAL + 1)
    then_value = emit_expression(node.then_value, ctx, expected).at_least(CONDITIONAL)
    else_value = emit_expression(node.else_value, ctx, expected).at_least(CONDITIONAL)
    return Emitted(f"{test} ? {then_value} : {else_value}", CONDITIONAL)


# -------------------------------------------------------------- member access


def field_home(info: ClassInfo, name: str, ctx: CContext) -> ClassInfo | None:
    """The topmost class of *info*'s ancestry that has field *name*.

    Python lets a subclass assign ``self.name`` for a field its base declared;
    in C there must be exactly one struct member, in the topmost struct.
    """
    if ctx.index is None:
        return None
    home = None
    for klass in ctx.index.ancestors(info):
        if name in klass.fields:
            home = klass
    return home


def _static_field(info: ClassInfo, name: str, ctx: CContext) -> str | None:
    if ctx.index is None:
        return None
    for klass in ctx.index.ancestors(info):
        if name in klass.static_fields:
            return f"{ctx.class_symbol(klass)}_{safe_identifier(name)}"
    return None


def _field_access(info: ClassInfo, name: str, receiver: str, ctx: CContext) -> str:
    static = _static_field(info, name, ctx)
    if static is not None:
        return static
    owner = field_home(info, name, ctx)
    path = member_path(info, owner, ctx) if owner is not None else ""
    return f"{receiver}->{path}{safe_identifier(name)}"


def _attribute(node: IRAttribute, ctx: CContext, expected: IRType | None) -> Emitted:
    target, name = node.target, node.name
    if isinstance(target, IRName) and target.name not in ctx.locals and target.name != ctx.receiver:
        entry = ctx.scope_entry(target.name)
        if entry is not None and entry.kind is ScopeKind.EXTERNAL:
            if entry.qualified == "math" and name in _MATH_CONSTANTS:
                return Emitted(_MATH_CONSTANTS[name])
            return _unsupported(
                ctx, "Attribute", f"'{entry.qualified}.{name}' has no C mapping", node
            )
        if entry is not None and entry.kind is ScopeKind.MODULE:
            return Emitted(ctx.symbol(entry.qualified, name))
    target_type = _type(target)
    module_ref = target_type.meta.get(MODULE_REF) if target_type else None
    if isinstance(module_ref, str):
        return Emitted(ctx.symbol(module_ref, name))
    rendered = emit_expression(target, ctx)
    if _is_gap(rendered):
        return rendered
    is_self = bool(ctx.receiver and isinstance(target, IRName) and target.name == ctx.receiver)
    info = ctx.klass if is_self else ctx.class_of(target_type)
    if info is not None and target_type is not None and target_type.meta.get(CLASS_REF):
        static = _static_field(info, name, ctx)
        if static is not None:
            return Emitted(static)
    if info is not None and not (target_type is not None and target_type.meta.get(CLASS_REF)):
        found = ctx.index.find_method(info, name) if ctx.index is not None else None
        if found is not None and name in found[0].properties:
            return Emitted(
                method_call_text(info, name, rendered.at_least(POSTFIX), [], ctx), POSTFIX
            )
        return Emitted(_field_access(info, name, rendered.at_least(POSTFIX), ctx), POSTFIX)
    return _unsupported(
        ctx, "Attribute", f"attribute {name!r} of a value of unknown type has no C spelling", node
    )


def _index(node: IRIndex, ctx: CContext, expected: IRType | None) -> Emitted:
    container_type = _type(node.target)
    target = emit_expression(node.target, ctx)
    index = emit_expression(node.index, ctx).text
    if _is(container_type, "str"):
        return Emitted(f"cw_str_at({target.text}, {index})", POSTFIX)
    kind = _kind(container_type, ctx)
    if kind is None:
        return _unsupported(
            ctx, "Subscript", "indexing is only translated for lists and strings", node
        )
    if (
        isinstance(node.target, IRName)
        and isinstance(node.index, IRName)
        and (node.target.name, node.index.name) in ctx.unchecked
    ):
        access = f"{target.at_least(POSTFIX)}->items[{index}]"
    else:
        access = f"cw_list_{kind}_get({target.text}, {index})"
    if kind == "ptr":
        element = render_type(element_type(container_type), ctx)
        return Emitted(f"(({element})({access}))", PRIMARY)
    return Emitted(access, POSTFIX)


def emit_store(target_node: IRNode, value: str, ctx: CContext) -> str:
    if isinstance(target_node, IRIndex):
        container_type = _type(target_node.target)
        kind = _kind(container_type, ctx)
        target = emit_expression(target_node.target, ctx)
        index = emit_expression(target_node.index, ctx).text
        if kind is None:
            return _unsupported(
                ctx, "Subscript", "item assignment is only translated for lists", target_node
            ).text
        if (
            isinstance(target_node.target, IRName)
            and isinstance(target_node.index, IRName)
            and (target_node.target.name, target_node.index.name) in ctx.unchecked
        ):
            return f"{target.at_least(POSTFIX)}->items[{index}] = {value}"
        return f"cw_list_{kind}_set({target.text}, {index}, {value})"
    return f"{emit_expression(target_node, ctx).text} = {value}"


def _slice(node: IRSlice, ctx: CContext, expected: IRType | None) -> Emitted:
    kind = _kind(_type(node.target), ctx)
    if kind is None:
        return _unsupported(ctx, "Subscript", "slicing is only translated for lists", node)
    parts = [emit_expression(node.target, ctx).text]
    for bound in (node.lower, node.upper):
        if bound is None:
            parts += ["false", "0"]
        else:
            parts += ["true", emit_expression(bound, ctx).text]
    parts.append(emit_expression(node.step, ctx).text if node.step is not None else "1")
    return Emitted(f"cw_list_{kind}_slice({', '.join(parts)})", POSTFIX)


# --------------------------------------------------------------- containers


def _array(node: IRArrayLiteral, ctx: CContext, expected: IRType | None) -> Emitted:
    target = expected if expected is not None and _is(expected, "list") else _type(node)
    kind = _kind(target, ctx)
    if kind is None:
        return _unsupported(
            ctx, "List", "element type of this list is unknown", node, "annotate the variable"
        )
    element_ir = target.args[0] if target is not None and target.args else None
    if not node.elements:
        return Emitted(f"cw_list_{kind}_new()", POSTFIX)
    c_element = "void*" if kind == "ptr" else render_type(element_ir, ctx)
    items = ", ".join(emit_expression(item, ctx, element_ir).text for item in node.elements)
    return Emitted(f"cw_list_{kind}_of({len(node.elements)}, ({c_element}[]){{{items}}})", POSTFIX)


def _template(node: IRTemplateString, ctx: CContext, expected: IRType | None) -> Emitted:
    parts: list[str] = []
    for part in node.parts:
        if isinstance(part, IRLiteral) and isinstance(part.value, str):
            if part.value:
                parts.append(c_string_literal(part.value, trigraphs=True))
            continue
        converted = string_of(part, ctx)
        if converted is None:
            return _unsupported(
                ctx, "JoinedStr", "an f-string part of unknown type has no C formatting", part
            )
        parts.append(converted)
    if not parts:
        return Emitted('""')
    if len(parts) == 1:
        return Emitted(parts[0], POSTFIX)
    return Emitted(f"cw_cat({len(parts)}, {', '.join(parts)})", POSTFIX)


def _comprehension(node: IRComprehension, ctx: CContext, expected: IRType | None) -> Emitted:
    if node.form != "list":
        return _unsupported(
            ctx,
            "Comprehension",
            f"{node.form} comprehensions need a {node.form} type C does not have",
            node,
        )
    result_type = expected if expected is not None and _is(expected, "list") else _type(node)
    kind = _kind(result_type, ctx)
    element_ir = result_type.args[0] if result_type is not None and result_type.args else None
    if kind is None:
        return _unsupported(ctx, "Comprehension", "comprehension into a list of unknown type", node)
    result = ctx.temp("list")
    index = ctx.temp("i")
    variable = safe_identifier(node.target)
    # The iterable is evaluated once, in the enclosing scope.
    header, binding = _comprehension_loop(node, index, ctx)
    if header is None:
        return _unsupported(
            ctx, "Comprehension", "comprehension over a value that is not a list or range()", node
        )
    outer = ctx.pre
    ctx.pre = []
    saved = set(ctx.locals)
    ctx.locals.add(node.target)
    element = emit_expression(node.element, ctx, element_ir).text
    test = condition(node.condition, ctx) if node.condition is not None else ""
    inner = ctx.pre
    ctx.locals = saved
    ctx.pre = outer
    append = f"cw_list_{kind}_append({result}, {element});"
    body = " ".join(
        [
            f"{binding.format(variable=variable)}",
            *inner,
            f"if ({test}) {append}" if test else append,
        ]
    )
    ctx.pre.append(f"cw_list_{kind}* {result} = cw_list_{kind}_new();")
    ctx.pre.append(f"{header} {{ {body} }}")
    return Emitted(result)


def _comprehension_loop(node: IRComprehension, index: str, ctx: CContext) -> tuple[str | None, str]:
    """The loop header and the statement binding the loop variable."""
    iterable = node.iterable
    if (
        isinstance(iterable, IRCall)
        and isinstance(iterable.callee, IRName)
        and iterable.callee.name == "range"
        and ctx.scope_entry("range") is None
        and 1 <= len(iterable.args) <= 2
        and not iterable.keywords
    ):
        bounds = [emit_expression(item, ctx).text for item in iterable.args]
        start, stop = ("0", bounds[0]) if len(bounds) == 1 else (bounds[0], bounds[1])
        bound = ctx.temp("stop")
        ctx.pre.append(f"const int64_t {bound} = {stop};")
        return (
            f"for (int64_t {index} = {start}; {index} < {bound}; ++{index})",
            f"int64_t {{variable}} = {index};",
        )
    iterable_type = _type(iterable)
    source_kind = _kind(iterable_type, ctx)
    element_c = render_type(element_type(iterable_type), ctx)
    if source_kind is None or element_c is None:
        return None, ""
    source = ctx.temp("source")
    ctx.pre.append(f"cw_list_{source_kind}* {source} = {emit_expression(iterable, ctx).text};")
    access = f"{source}->items[{index}]"
    if source_kind == "ptr":
        access = f"({element_c})({access})"
    return (
        f"for (int64_t {index} = 0; {index} < {source}->len; ++{index})",
        f"{element_c} {{variable}} = {access};",
    )


# --------------------------------------------------------------------- calls


def lower_arguments(
    params: list[IRParam], call: IRCall, ctx: CContext, *, callee_name: str
) -> list[str]:
    """Every parameter's argument, positionally, with defaults filled in."""
    slots: list[str | None] = [None] * len(params)
    for position, argument in enumerate(call.args):
        if position < len(params) and params[position].style == "positional":
            slots[position] = emit_expression(argument, ctx, params[position].param_type).text
        else:
            ctx.warn(
                "CW0684",
                f"extra or variadic argument in call to {callee_name!r} is not translated",
                span=call.span,
            )
    for keyword in call.keywords:
        found = next((i for i, p in enumerate(params) if p.name == keyword.name), None)
        if found is None:
            ctx.warn(
                "CW0685", f"{callee_name!r} has no parameter named {keyword.name!r}", span=call.span
            )
            continue
        slots[found] = emit_expression(keyword.value, ctx, params[found].param_type).text
    rendered: list[str] = []
    for position, param in enumerate(params):
        if param.style in ("var_positional", "var_keyword"):
            ctx.gap(
                "Call",
                f"*{param.name} / **{param.name} parameters are not translated to C",
                call.span,
            )
            continue
        slot = slots[position]
        if slot is None:
            if param.default is None:
                ctx.warn(
                    "CW0686",
                    f"missing argument {param.name!r} in call to {callee_name!r}",
                    span=call.span,
                )
                slot = "0"
            else:
                slot = emit_expression(param.default, ctx, param.param_type).text
        rendered.append(slot)
    return rendered


def _message(args: list[IRExpression], ctx: CContext) -> str:
    if not args:
        return '""'
    converted = string_of(args[0], ctx)
    return converted if converted is not None else '"<message>"'


def _call(node: IRCall, ctx: CContext, expected: IRType | None) -> Emitted:
    callee = node.callee
    if isinstance(callee, IRName) and callee.name not in ctx.locals and callee.name != ctx.receiver:
        entry = ctx.scope_entry(callee.name)
        if entry is None:
            mapped = _builtin(callee.name, node, ctx, expected)
            if mapped is not None:
                return mapped
        elif entry.kind is ScopeKind.CLASS and ctx.index is not None:
            return construct(ctx.index.classes[entry.qualified], node, ctx)
        elif entry.kind is ScopeKind.FUNCTION and ctx.index is not None:
            function = ctx.index.functions[entry.qualified].node
            args = lower_arguments(function.params, node, ctx, callee_name=callee.name)
            return Emitted(f"{ctx.symbol(entry.module, entry.name)}({', '.join(args)})", POSTFIX)
        elif entry.kind is ScopeKind.EXTERNAL:
            return _external_call(entry.qualified, node, ctx)
    if isinstance(callee, IRAttribute):
        method = _method_call(node, callee, ctx)
        if method is not None:
            return method
    if node.meta.get(CONSTRUCTS_CLASS):
        info = ctx.class_of(_type(node))
        if info is not None:
            return construct(info, node, ctx)
    rendered = emit_expression(callee, ctx)
    if _is_gap(rendered):
        return rendered
    return _unsupported(
        ctx, "Call", "call of a value whose type is unknown has no C spelling", node
    )


def construct(info: ClassInfo, node: IRCall, ctx: CContext) -> Emitted:
    if info.is_exception:
        return _unsupported(
            ctx, "Call", "exception objects exist only in a raise statement in C", node
        )
    found = ctx.index.constructor(info) if ctx.index is not None else None
    params = found[1].params if found is not None else []
    args = lower_arguments(params, node, ctx, callee_name=info.name)
    return Emitted(f"{ctx.class_symbol(info)}_new({', '.join(args)})", POSTFIX)


def _signature(info: ClassInfo, name: str, ctx: CContext) -> IRFunction | None:
    found = ctx.index.find_method(info, name) if ctx.index is not None else None
    return found[1] if found else None


def _method_call(node: IRCall, callee: IRAttribute, ctx: CContext) -> Emitted | None:
    target, name = callee.target, callee.name
    target_type = _type(target)
    if (
        isinstance(target, IRCall)
        and isinstance(target.callee, IRName)
        and target.callee.name == "super"
    ):
        base = (
            ctx.index.classes.get(ctx.klass.base)
            if ctx.klass is not None and ctx.klass.base and ctx.index
            else None
        )
        if base is None:
            return _unsupported(
                ctx, "Call", "super() outside a class with a project base class", node
            )
        found = ctx.index.find_method(base, name) if ctx.index is not None else None
        if found is None:
            return _unsupported(ctx, "Call", f"base class has no method {name!r}", node)
        owner, function = found
        args = lower_arguments(function.params, node, ctx, callee_name=name)
        method = "init" if name == "__init__" else safe_identifier(name)
        # super() calls the base implementation directly, never through dispatch.
        return Emitted(
            f"{ctx.class_symbol(owner)}_{method}({', '.join([f'(({ctx.class_symbol(owner)}*)self)', *args])})",
            POSTFIX,
        )
    if isinstance(target, IRName) and target.name not in ctx.locals and target.name != ctx.receiver:
        entry = ctx.scope_entry(target.name)
        if entry is not None and entry.kind is ScopeKind.EXTERNAL:
            return _external_call(f"{entry.qualified}.{name}", node, ctx)
    module_ref = target_type.meta.get(MODULE_REF) if target_type else None
    if isinstance(module_ref, str) and ctx.index is not None:
        entry = ctx.index.lookup(module_ref, name)
        if entry is not None and entry.kind is ScopeKind.CLASS:
            return construct(ctx.index.classes[entry.qualified], node, ctx)
        if entry is not None and entry.kind is ScopeKind.FUNCTION:
            function = ctx.index.functions[entry.qualified].node
            args = lower_arguments(function.params, node, ctx, callee_name=name)
            return Emitted(f"{ctx.symbol(entry.module, entry.name)}({', '.join(args)})", POSTFIX)
        return None
    is_self = bool(ctx.receiver and isinstance(target, IRName) and target.name == ctx.receiver)
    info = ctx.klass if is_self else ctx.class_of(target_type)
    if info is not None:
        signature = _signature(info, name, ctx)
        args = (
            lower_arguments(signature.params, node, ctx, callee_name=name)
            if signature is not None
            else [emit_expression(item, ctx).text for item in node.args]
        )
        receiver = "self" if is_self else emit_expression(target, ctx).at_least(POSTFIX)
        return Emitted(method_call_text(info, name, receiver, args, ctx), POSTFIX)
    if _is(target_type, "str") or _kind(target_type, ctx):
        return _builtin_method(node, callee, ctx)
    return None


def _builtin_method(node: IRCall, callee: IRAttribute, ctx: CContext) -> Emitted:
    name = callee.name
    target_type = _type(callee.target)
    receiver = emit_expression(callee.target, ctx).text
    element = element_type(target_type)
    args = [emit_expression(item, ctx, element).text for item in node.args]
    if _is(target_type, "str"):
        simple = {
            "upper": 0,
            "lower": 0,
            "strip": 0,
            "startswith": 1,
            "endswith": 1,
            "find": 1,
            "replace": 2,
            "split": 1,
        }
        if name in simple and len(args) == simple[name]:
            return Emitted(f"cw_str_{name}({', '.join([receiver, *args])})", POSTFIX)
        if name == "split" and not args:
            return Emitted(f"cw_str_split({receiver}, NULL)", POSTFIX)
        if name == "join" and len(args) == 1:
            return Emitted(f"cw_str_join({receiver}, {args[0]})", POSTFIX)
        return _unsupported(ctx, "Call", f"str.{name}() has no C mapping in this build", node)
    kind = _kind(target_type, ctx)
    if kind == "ptr" and name in ("append", "insert", "remove", "index", "count"):
        args = [f"(void*)({item})" for item in args]
    if name == "append" and len(args) == 1:
        return Emitted(f"cw_list_{kind}_append({receiver}, {args[0]})", POSTFIX)
    if name == "pop" and len(args) <= 1:
        return Emitted(f"cw_list_{kind}_pop({receiver}, {args[0] if args else '-1'})", POSTFIX)
    if name in ("insert", "remove", "index", "count", "extend") and len(args) == (
        2 if name == "insert" else 1
    ):
        return Emitted(f"cw_list_{kind}_{name}({', '.join([receiver, *args])})", POSTFIX)
    if name in ("copy", "reverse") and not args:
        return Emitted(f"cw_list_{kind}_{name}({receiver})", POSTFIX)
    if name == "clear" and not args:
        return Emitted(
            f"({emit_expression(callee.target, ctx).at_least(POSTFIX)}->len = 0)", PRIMARY
        )
    if (
        name == "sort"
        and not args
        and kind in ("i64", "f64", "str")
        and not any(k.name == "key" for k in node.keywords)
    ):
        reverse = next(
            (condition(k.value, ctx) for k in node.keywords if k.name == "reverse"), "false"
        )
        return Emitted(f"cw_list_{kind}_sort({receiver}, {reverse})", POSTFIX)
    return _unsupported(
        ctx, "Call", f"list.{name}() with these arguments has no C mapping in this build", node
    )


def _external_call(qualified: str, node: IRCall, ctx: CContext) -> Emitted:
    module, _, member = qualified.rpartition(".")
    args = [emit_expression(item, ctx).text for item in node.args]
    if module == "math":
        if member == "log":
            if len(args) == 2:
                return Emitted(f"(log({args[0]}) / log({args[1]}))", PRIMARY)
            if len(args) == 1:
                return Emitted(f"log({args[0]})", POSTFIX)
        template = _MATH_FUNCTIONS.get(member)
        if template is not None and template.count("{") == len(args):
            return Emitted(
                template.format(*args), POSTFIX if not template.startswith("(") else UNARY
            )
    return _unsupported(
        ctx, "Call", f"{qualified!r} comes from an external module with no C mapping", node
    )


# ------------------------------------------------------------------ builtins


def _builtin(name: str, node: IRCall, ctx: CContext, expected: IRType | None) -> Emitted | None:
    args = node.args
    types = [_type(item) for item in args]
    rendered = [emit_expression(item, ctx).text for item in args]
    keywords = {keyword.name: keyword.value for keyword in node.keywords}
    if name in BUILTIN_EXCEPTIONS:
        return _unsupported(
            ctx, "Call", "exception objects exist only in a raise statement in C", node
        )
    if name == "print":
        if keywords:
            ctx.warn("CW0687", "print() keyword arguments are not translated", span=node.span)
        parts = []
        for item in args:
            converted = string_of(item, ctx)
            if converted is None:
                return _unsupported(
                    ctx, "Call", "print() of a value whose type has no C formatting", item
                )
            parts.append(converted)
        return Emitted(f"cw_print({', '.join([str(len(parts)), *parts])})", POSTFIX)
    if name == "len" and len(args) == 1:
        if _is(types[0], "str"):
            return Emitted(f"cw_str_len({rendered[0]})", POSTFIX)
        if _kind(types[0], ctx):
            return Emitted(f"{emit_expression(args[0], ctx).at_least(POSTFIX)}->len", POSTFIX)
        info = ctx.class_of(types[0])
        if info is not None and ctx.index is not None and ctx.index.find_method(info, "__len__"):
            return Emitted(method_call_text(info, "__len__", rendered[0], [], ctx), POSTFIX)
        return _unsupported(ctx, "Call", "len() of a value of unknown type", node)
    if name in ("str", "repr") and len(args) == 1:
        if name == "repr" and _is(types[0], "str"):
            return Emitted(f"cw_repr_str({rendered[0]})", POSTFIX)
        converted = string_of(args[0], ctx)
        if converted is None:
            return _unsupported(
                ctx, "Call", f"{name}() of a value whose type has no C formatting", node
            )
        return Emitted(converted, POSTFIX)
    if name == "int" and len(args) == 1:
        if _is(types[0], "str"):
            return Emitted(f"cw_int_from_str({rendered[0]})", POSTFIX)
        if _is_int(types[0]):
            return Emitted(f"(int64_t)({rendered[0]})", UNARY)
        return Emitted(f"(int64_t)({rendered[0]})", UNARY)
    if name == "float" and len(args) == 1:
        if _is(types[0], "str"):
            return Emitted(f"cw_float_from_str({rendered[0]})", POSTFIX)
        return Emitted(f"(double)({rendered[0]})", UNARY)
    if name == "bool" and len(args) == 1:
        return _truthy(args[0], ctx)
    if name == "abs" and len(args) == 1:
        return Emitted(f"{'cw_abs_i64' if _is_int(types[0]) else 'fabs'}({rendered[0]})", POSTFIX)
    if name == "round":
        if len(args) == 1:
            return Emitted(f"cw_round_i64({rendered[0]})", POSTFIX)
        if len(args) == 2:
            return Emitted(f"cw_round_f64({rendered[0]}, {rendered[1]})", POSTFIX)
    if name in ("min", "max"):
        if len(args) == 1 and _kind(types[0], ctx) in ("i64", "f64"):
            return Emitted(f"cw_list_{_kind(types[0], ctx)}_{name}({rendered[0]})", POSTFIX)
        if len(args) == 2:
            if all(_is_int(item) for item in types):
                return Emitted(f"cw_{name}_i64({rendered[0]}, {rendered[1]})", POSTFIX)
            return Emitted(f"f{name}({rendered[0]}, {rendered[1]})", POSTFIX)
    if name == "sum" and len(args) == 1 and _kind(types[0], ctx) in ("i64", "f64"):
        return Emitted(f"cw_list_{_kind(types[0], ctx)}_sum({rendered[0]})", POSTFIX)
    if (
        name == "sorted"
        and len(args) == 1
        and _kind(types[0], ctx) in ("i64", "f64", "str")
        and "key" not in keywords
    ):
        kind = _kind(types[0], ctx)
        result = ctx.temp("sorted")
        reverse = condition(keywords["reverse"], ctx) if "reverse" in keywords else "false"
        ctx.pre.append(f"cw_list_{kind}* {result} = cw_list_{kind}_copy({rendered[0]});")
        ctx.pre.append(f"cw_list_{kind}_sort({result}, {reverse});")
        return Emitted(result)
    if name == "list" and len(args) == 1 and _kind(types[0], ctx):
        return Emitted(f"cw_list_{_kind(types[0], ctx)}_copy({rendered[0]})", POSTFIX)
    if name == "list" and not args:
        kind = _kind(expected if expected is not None else _type(node), ctx)
        if kind:
            return Emitted(f"cw_list_{kind}_new()", POSTFIX)
    if name == "range":
        bounds = (
            rendered
            if len(rendered) == 3
            else (["0", rendered[0], "1"] if len(rendered) == 1 else [*rendered, "1"])
        )
        return Emitted(f"cw_range({', '.join(bounds)})", POSTFIX)
    if name == "ord" and len(args) == 1:
        return Emitted(
            f"(int64_t)(unsigned char)({emit_expression(args[0], ctx).at_least(POSTFIX)}[0])", UNARY
        )
    return _unsupported(
        ctx, "Call", f"builtin {name}() with these arguments has no C mapping in this build", node
    )


_Handler = Callable[[IRNode, CContext, IRType | None], Emitted]

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
    IRTemplateString: _template,  # type: ignore[dict-item]
    IRComprehension: _comprehension,  # type: ignore[dict-item]
}

# Expressions C has no form for at all: reported through the generic path.
_ = (IRLambda, ADDITIVE, RELATIONAL)
