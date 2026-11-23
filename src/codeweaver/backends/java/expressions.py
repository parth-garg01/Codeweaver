"""IR expressions to Java expressions.

The inferred operand types decide every spelling:

* ``//`` and ``%`` on ints are ``Math.floorDiv`` and ``Math.floorMod``, which
  round toward negative infinity as Python does; ``/`` always produces a
  ``double`` and raises ``ArithmeticException`` on zero, like
  ``ZeroDivisionError``;
* ``==`` is ``==`` on primitives and ``Objects.equals`` on everything else,
  so strings and boxed numbers compare by value;
* indexing is checked and accepts negative indices, except inside a loop that
  provably stays in bounds, where it is a raw ``get``;
* keyword arguments are matched to the callee's signature, and omitted
  defaults are filled in at the call site, so no overloads are needed;
* a lambda that would capture a variable Java does not consider effectively
  final captures a copy made just before the statement instead.
"""

from __future__ import annotations

from collections.abc import Callable

from codeweaver.backends.common.clike import (
    ADDITIVE,
    BINARY_SPELLING,
    COMPARE_SPELLING,
    CONDITIONAL,
    EQUALITY,
    LAMBDA,
    LOGICAL_AND,
    LOGICAL_OR,
    POSTFIX,
    PRIMARY,
    RELATIONAL,
    UNARY,
    Emitted,
)
from codeweaver.backends.common.context import UNSUPPORTED_MARKER
from codeweaver.backends.java.context import JavaContext
from codeweaver.backends.java.naming import safe_identifier
from codeweaver.backends.java.types import EXCEPTIONS, render_type
from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS, ClassInfo, ScopeKind
from codeweaver.ir.base import CONSTRUCTS_CLASS, IRNode, IRUnsupported, walk
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

__all__ = [
    "condition",
    "emit_expression",
    "emit_store",
    "is_super_call",
    "java_string",
    "lower_arguments",
]

UNMAPPED_BUILTINS: dict[str, str] = {
    "enumerate": "iterate with an index loop",
    "zip": "index both sequences explicitly",
    "map": "use a comprehension",
    "filter": "use a comprehension with a condition",
    "reversed": "copy and reverse the list explicitly",
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
    "hash": "use Objects.hashCode explicitly",
    "open": "file I/O is not translated; use java.nio.file.Files",
    "input": "console input is not translated; use a Scanner",
    "format": "use an f-string",
}

_MATH_FUNCTIONS: dict[str, str] = {
    "sqrt": "Math.sqrt({0})",
    "exp": "Math.exp({0})",
    "log10": "Math.log10({0})",
    "log2": "(Math.log({0}) / Math.log(2.0))",
    "sin": "Math.sin({0})",
    "cos": "Math.cos({0})",
    "tan": "Math.tan({0})",
    "asin": "Math.asin({0})",
    "acos": "Math.acos({0})",
    "atan": "Math.atan({0})",
    "atan2": "Math.atan2({0}, {1})",
    "hypot": "Math.hypot({0}, {1})",
    "fabs": "Math.abs((double) {0})",
    "pow": "Math.pow({0}, {1})",
    "floor": "(long) Math.floor({0})",
    "ceil": "(long) Math.ceil({0})",
    "trunc": "(long) ({0})",
    "isnan": "Double.isNaN({0})",
    "isinf": "Double.isInfinite({0})",
    "isfinite": "Double.isFinite({0})",
    "gcd": "Py.gcd({0}, {1})",
    "copysign": "Math.copySign((double) {0}, (double) {1})",
}
_MATH_CONSTANTS: dict[str, str] = {
    "pi": "Math.PI",
    "e": "Math.E",
    "tau": "(2 * Math.PI)",
    "inf": "Double.POSITIVE_INFINITY",
    "nan": "Double.NaN",
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


def _is_number(node_type: IRType | None) -> bool:
    return _is_int(node_type) or _is(node_type, "float")


def _primitive(node_type: IRType | None) -> bool:
    """A value held in a Java primitive (never null)."""
    return bool(
        node_type
        and node_type.kind is TypeKind.PRIMITIVE
        and node_type.name in ("int", "float", "bool")
    )


def is_super_call(node: IRNode | None) -> bool:
    return (
        isinstance(node, IRCall)
        and isinstance(node.callee, IRName)
        and node.callee.name == "super"
        and not node.args
    )


def java_string(value: str) -> str:
    """A Java string literal; control characters use octal escapes.

    ``\\uXXXX`` escapes are avoided for control characters because Java
    translates them before lexing, so ``\\u000a`` would end the line.
    """
    out = ['"']
    for char in value:
        code = ord(char)
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif char == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\{code:03o}")
        elif code > 0xFFFF:
            encoded = char.encode("utf-16-be")
            high, low = int.from_bytes(encoded[:2], "big"), int.from_bytes(encoded[2:], "big")
            out.append(f"\\u{high:04x}\\u{low:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _unsupported(ctx: JavaContext, kind: str, reason: str, node: IRNode, hint: str = "") -> Emitted:
    marker = ctx.gap(kind, reason, node.span, hint)
    return Emitted(f"{marker} Py.unsupported({java_string(kind)})", POSTFIX)


# --------------------------------------------------------------------- entry


def emit_expression(
    node: IRNode | None, ctx: JavaContext, expected: IRType | None = None
) -> Emitted:
    if node is None:
        return Emitted("null")
    if isinstance(node, IRUnsupported):
        marker = ctx.record_gap(node)
        return Emitted(f"{marker} Py.unsupported({java_string(node.source_kind)})", POSTFIX)
    handler = _HANDLERS.get(type(node))
    if handler is None:
        return _unsupported(ctx, node.kind, "no Java mapping for this expression", node)
    return handler(node, ctx, expected)


def condition(node: IRNode | None, ctx: JavaContext) -> str:
    """Render *node* where Java needs a ``boolean``, applying Python truthiness."""
    return _condition(node, ctx).text


def _condition(node: IRNode | None, ctx: JavaContext) -> Emitted:
    node_type = _type(node)
    if isinstance(node, IRLogical) and not _used_for_value(node):
        return emit_expression(node, ctx)
    rendered = emit_expression(node, ctx)
    if (
        _is(node_type, "bool")
        or isinstance(node, IRCompare)
        or (isinstance(node, IRUnary) and node.op is UnaryOp.NOT)
    ):
        return rendered
    if node_type is not None and node_type.kind is TypeKind.PRIMITIVE:
        if node_type.name == "int":
            return Emitted(f"{rendered.at_least(EQUALITY + 1)} != 0", EQUALITY)
        if node_type.name == "float":
            return Emitted(f"{rendered.at_least(EQUALITY + 1)} != 0.0", EQUALITY)
        if node_type.name == "str":
            return Emitted(f"!{rendered.at_least(POSTFIX)}.isEmpty()", UNARY)
    if (
        node_type is not None
        and node_type.kind is TypeKind.COLLECTION
        and node_type.name
        in (
            "list",
            "set",
            "dict",
        )
    ):
        return Emitted(f"!{rendered.at_least(POSTFIX)}.isEmpty()", UNARY)
    info = ctx.class_of(node_type)
    if info is not None and ctx.index is not None:
        if ctx.index.find_method(info, "__bool__"):
            return Emitted(f"{rendered.at_least(POSTFIX)}.bool_()", POSTFIX)
        if ctx.index.find_method(info, "__len__"):
            return Emitted(f"{rendered.at_least(POSTFIX)}.len_() != 0", EQUALITY)
        return Emitted(f"{rendered.at_least(EQUALITY + 1)} != null", EQUALITY)
    if node_type is not None and node_type.kind is TypeKind.OPTIONAL:
        return Emitted(f"Py.truthy({rendered.text})", POSTFIX)
    return Emitted(f"Py.truthy({rendered.text})", POSTFIX)


def _used_for_value(node: IRLogical) -> bool:
    node_type = _type(node)
    return (
        node_type is not None
        and node_type.kind is not TypeKind.UNKNOWN
        and not _is(node_type, "bool")
    )


# ------------------------------------------------------------------ literals


def _literal(node: IRLiteral, ctx: JavaContext, expected: IRType | None) -> Emitted:
    value = node.value
    if node.meta.get("pythonEllipsis"):
        return Emitted('Py.unsupported("...")', POSTFIX)
    if value is None:
        return Emitted("null")
    if isinstance(value, bool):
        return Emitted("true" if value else "false")
    if isinstance(value, int):
        if expected is not None and _is(expected, "float"):
            return Emitted(f"{float(value)!r}", UNARY if value < 0 else PRIMARY)
        if not -(2**63) <= value < 2**63:
            return _unsupported(
                ctx, "Constant", "integer literal does not fit in a Java long", node
            )
        if value == -(2**63):
            return Emitted("Long.MIN_VALUE")
        return Emitted(f"{value}L", UNARY if value < 0 else PRIMARY)
    if isinstance(value, float):
        if value != value:  # noqa: PLR0124 - NaN check
            return Emitted("Double.NaN")
        if value in (float("inf"), float("-inf")):
            return Emitted("Double.POSITIVE_INFINITY" if value > 0 else "Double.NEGATIVE_INFINITY")
        text = repr(value)
        if "." not in text and "e" not in text:
            text += ".0"
        return Emitted(text, UNARY if text.startswith("-") else PRIMARY)
    if isinstance(value, str):
        return Emitted(java_string(value))
    return _unsupported(
        ctx, "Constant", f"literal of type {type(value).__name__} has no Java spelling", node
    )


# --------------------------------------------------------------------- names


def _name(node: IRName, ctx: JavaContext, expected: IRType | None) -> Emitted:
    name = node.name
    if ctx.receiver and name == ctx.receiver:
        return Emitted("this")
    if ctx.class_receiver and name == ctx.class_receiver and ctx.klass is not None:
        return Emitted(ctx.class_spelling(ctx.klass))
    if name in ctx.locals:
        return Emitted(ctx.local(name))
    entry = ctx.scope_entry(name)
    if entry is not None and ctx.index is not None:
        if entry.kind is ScopeKind.CLASS:
            return Emitted(ctx.class_spelling(ctx.index.classes[entry.qualified]))
        if entry.kind is ScopeKind.FUNCTION:
            owner = ctx.module_class(entry.module)
            return Emitted(f"{owner}::{safe_identifier(entry.name)}", POSTFIX)
        if entry.kind is ScopeKind.VARIABLE:
            return Emitted(ctx.member_spelling(entry), POSTFIX)
        if entry.kind is ScopeKind.MODULE:
            return Emitted(ctx.module_class(entry.qualified))
        if entry.kind is ScopeKind.EXTERNAL:
            return _external_name(entry.qualified, node, ctx)
    if name in BUILTIN_EXCEPTIONS:
        return Emitted(EXCEPTIONS.get(name, "RuntimeException"))
    return _unsupported(
        ctx,
        "Name",
        f"{name!r} is not defined at module level (a nested function or a builtin used as a value)",
        node,
        "move the definition to module level",
    )


def _is_gap(rendered: Emitted) -> bool:
    return UNSUPPORTED_MARKER in rendered.text


def _external_name(qualified: str, node: IRNode, ctx: JavaContext) -> Emitted:
    module, _, member = qualified.rpartition(".")
    if module == "math" and member in _MATH_CONSTANTS:
        return Emitted(_MATH_CONSTANTS[member])
    return _unsupported(
        ctx,
        "Name",
        f"{qualified!r} comes from an external module that has no Java mapping",
        node,
        "re-implement it, or depend on a Java library that provides it",
    )


# ----------------------------------------------------------------- operators


def _binary(node: IRBinary, ctx: JavaContext, expected: IRType | None) -> Emitted:
    left_type, right_type = _type(node.left), _type(node.right)
    both_int = _is_int(left_type) and _is_int(right_type)
    op = node.op
    left = emit_expression(node.left, ctx)
    right = emit_expression(node.right, ctx)
    if op is BinaryOp.DIV:
        return Emitted(f"Py.div({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.FLOOR_DIV:
        function = "Math.floorDiv" if both_int else "Py.floorDiv"
        return Emitted(f"{function}({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MOD:
        if _is(left_type, "str"):
            return _unsupported(
                ctx,
                "BinOp",
                "printf-style string formatting has no Java mapping",
                node,
                "use an f-string",
            )
        function = "Math.floorMod" if both_int else "Py.mod"
        return Emitted(f"{function}({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.POW:
        function = "Py.pow" if both_int else "Math.pow"
        return Emitted(f"{function}({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MAT_MUL:
        return _unsupported(ctx, "MatMult", "matrix multiplication has no Java operator", node)
    if op is BinaryOp.ADD and _is(left_type, "list"):
        return Emitted(f"Py.concat({left.text}, {right.text})", POSTFIX)
    if op is BinaryOp.MUL:
        for sequence, count, sequence_type in ((left, right, left_type), (right, left, right_type)):
            if _is(sequence_type, "str"):
                return Emitted(
                    f"{sequence.at_least(POSTFIX)}.repeat((int) Math.max(0, {count.text}))", POSTFIX
                )
            if _is(sequence_type, "list"):
                return Emitted(f"Py.repeat({sequence.text}, {count.text})", POSTFIX)
    spelling, precedence = BINARY_SPELLING[op]
    if op is BinaryOp.SHIFT_RIGHT:
        spelling = ">>"
    return Emitted(
        f"{left.at_least(precedence)} {spelling} {right.at_least(precedence + 1)}", precedence
    )


def _unary(node: IRUnary, ctx: JavaContext, expected: IRType | None) -> Emitted:
    if node.op is UnaryOp.NOT:
        inner = _condition(node.operand, ctx)
        if (
            inner.precedence == UNARY
            and inner.text.startswith("!")
            and not inner.text.startswith("!(")
        ):
            return Emitted(inner.text[1:], POSTFIX)  # not `!x.isEmpty()` -> `x.isEmpty()`
        return Emitted(f"!{inner.at_least(UNARY)}", UNARY)
    operand_node = node.operand
    if (
        node.op is UnaryOp.NEG
        and isinstance(operand_node, IRLiteral)
        and isinstance(operand_node.value, int | float)
        and not isinstance(operand_node.value, bool)
    ):
        negated = IRLiteral(
            -operand_node.value, value_type=operand_node.value_type, span=operand_node.span
        )
        return _literal(negated, ctx, expected)
    spelling = {UnaryOp.NEG: "-", UnaryOp.POS: "+", UnaryOp.BIT_NOT: "~"}[node.op]
    operand = emit_expression(operand_node, ctx).at_least(UNARY)
    if operand.startswith(spelling):
        operand = f"({operand})"
    return Emitted(f"{spelling}{operand}", UNARY)


def _is_none(node: IRNode) -> bool:
    return (
        isinstance(node, IRLiteral) and node.value is None and not node.meta.get("pythonEllipsis")
    )


def _has_method(info: ClassInfo | None, name: str, ctx: JavaContext) -> bool:
    return bool(info is not None and ctx.index is not None and ctx.index.find_method(info, name))


def contains(container: IRNode, item: IRNode, ctx: JavaContext) -> Emitted:
    container_type = _type(container)
    target = emit_expression(container, ctx).at_least(POSTFIX)
    element = emit_expression(item, ctx, element_type(container_type)).text
    if _is(container_type, "dict"):
        return Emitted(f"{target}.containsKey({element})", POSTFIX)
    if _is(container_type, "str") or _is(container_type, "list") or _is(container_type, "set"):
        return Emitted(f"{target}.contains({element})", POSTFIX)
    info = ctx.class_of(container_type)
    if _has_method(info, "__contains__", ctx):
        return Emitted(f"{target}.contains_({element})", POSTFIX)
    return Emitted(f"Py.contains({target}, {element})", POSTFIX)


def _compare(node: IRCompare, ctx: JavaContext, expected: IRType | None) -> Emitted:
    op = node.op
    if op in (CompareOp.IN, CompareOp.NOT_IN):
        test = contains(node.right, node.left, ctx)
        return Emitted(f"!{test.at_least(UNARY)}", UNARY) if op is CompareOp.NOT_IN else test
    if op in (CompareOp.IS, CompareOp.IS_NOT, CompareOp.EQ, CompareOp.NE) and (
        _is_none(node.right) or _is_none(node.left)
    ):
        subject = node.left if _is_none(node.right) else node.right
        rendered = emit_expression(subject, ctx).at_least(EQUALITY + 1)
        positive = op in (CompareOp.IS, CompareOp.EQ)
        return Emitted(f"{rendered} {'==' if positive else '!='} null", EQUALITY)
    left_type, right_type = _type(node.left), _type(node.right)
    if op in (CompareOp.IS, CompareOp.IS_NOT):
        spelling = "==" if op is CompareOp.IS else "!="
        left = emit_expression(node.left, ctx).at_least(EQUALITY)
        right = emit_expression(node.right, ctx).at_least(EQUALITY + 1)
        return Emitted(f"{left} {spelling} {right}", EQUALITY)
    info = ctx.class_of(left_type)
    dunder = {
        CompareOp.EQ: ("__eq__", "eq_"),
        CompareOp.NE: ("__ne__", "ne_"),
        CompareOp.LT: ("__lt__", "lt_"),
        CompareOp.LE: ("__le__", "le_"),
        CompareOp.GT: ("__gt__", "gt_"),
        CompareOp.GE: ("__ge__", "ge_"),
    }.get(op)
    if info is not None and dunder and _has_method(info, dunder[0], ctx):
        left = emit_expression(node.left, ctx).at_least(POSTFIX)
        right = emit_expression(node.right, ctx).text
        return Emitted(f"{left}.{dunder[1]}({right})", POSTFIX)
    if op is CompareOp.NE and info is not None and _has_method(info, "__eq__", ctx):
        left = emit_expression(node.left, ctx).at_least(POSTFIX)
        right = emit_expression(node.right, ctx).text
        return Emitted(f"!{left}.eq_({right})", UNARY)
    numeric = _primitive(left_type) and _primitive(right_type)
    if op in (CompareOp.EQ, CompareOp.NE) and not numeric:
        left = emit_expression(node.left, ctx).text
        right = emit_expression(node.right, ctx, left_type).text
        equality = f"Objects.equals({left}, {right})"
        return Emitted(f"!{equality}", UNARY) if op is CompareOp.NE else Emitted(equality, POSTFIX)
    spelling, precedence = COMPARE_SPELLING[op]
    if _is(left_type, "str") or _is(right_type, "str"):
        left = emit_expression(node.left, ctx).at_least(POSTFIX)
        right = emit_expression(node.right, ctx).text
        return Emitted(f"{left}.compareTo({right}) {spelling} 0", precedence)
    left = emit_expression(node.left, ctx).at_least(precedence)
    right = emit_expression(node.right, ctx, left_type).at_least(precedence + 1)
    return Emitted(f"{left} {spelling} {right}", precedence)


def _pure(node: IRNode | None) -> bool:
    """No calls anywhere: evaluating it twice is unobservable."""
    if node is None:
        return True
    return not any(isinstance(item, IRCall | IRLambda | IRComprehension) for item in walk(node))


def _logical(node: IRLogical, ctx: JavaContext, expected: IRType | None) -> Emitted:
    is_and = node.op is LogicalOp.AND
    if not node.operands:
        return Emitted("true" if is_and else "false")
    if _used_for_value(node):
        # Python returns an operand, not a bool.  Without statement
        # expressions an operand is only safe to evaluate twice if pure.
        if not all(_pure(operand) for operand in node.operands[:-1]):
            return _unsupported(
                ctx,
                "BoolOp",
                "'and'/'or' used for its value needs a temporary for an operand with side effects",
                node,
                "assign the operand to a variable first",
            )
        target = expected or _type(node)
        result = emit_expression(node.operands[-1], ctx, target).at_least(CONDITIONAL)
        for operand in reversed(node.operands[:-1]):
            test = _condition(operand, ctx).at_least(CONDITIONAL + 1)
            value = emit_expression(operand, ctx, target).at_least(CONDITIONAL)
            result = f"{test} ? {result} : {value}" if is_and else f"{test} ? {value} : {result}"
            result = f"({result})"
        return Emitted(result, PRIMARY)
    spelling, precedence = ("&&", LOGICAL_AND) if is_and else ("||", LOGICAL_OR)
    parts = [_condition(operand, ctx).at_least(precedence + 1) for operand in node.operands]
    return Emitted(f" {spelling} ".join(parts), precedence)


def _conditional(node: IRConditional, ctx: JavaContext, expected: IRType | None) -> Emitted:
    test = _condition(node.test, ctx).at_least(CONDITIONAL + 1)
    target = expected if expected is not None else _type(node)
    then_value = emit_expression(node.then_value, ctx, target).at_least(CONDITIONAL)
    else_value = emit_expression(node.else_value, ctx, target).at_least(CONDITIONAL)
    return Emitted(f"{test} ? {then_value} : {else_value}", CONDITIONAL)


# -------------------------------------------------------------- member access


def _is_property(info: ClassInfo | None, name: str, ctx: JavaContext) -> bool:
    if info is None or ctx.index is None:
        return False
    found = ctx.index.find_method(info, name)
    return bool(found and name in found[0].properties)


def _attribute(node: IRAttribute, ctx: JavaContext, expected: IRType | None) -> Emitted:
    target = node.target
    name = node.name
    member = safe_identifier(name)
    if is_super_call(target):
        return Emitted(f"super.{member}", POSTFIX)
    if isinstance(target, IRName) and target.name not in ctx.locals and target.name != ctx.receiver:
        entry = ctx.scope_entry(target.name)
        if entry is not None and entry.kind is ScopeKind.EXTERNAL:
            if entry.qualified == "math" and name in _MATH_CONSTANTS:
                return Emitted(_MATH_CONSTANTS[name])
            return _external_name(f"{entry.qualified}.{name}", node, ctx)
        if entry is not None and entry.kind is ScopeKind.MODULE:
            return Emitted(f"{ctx.module_class(entry.qualified)}.{member}", POSTFIX)
    target_type = _type(target)
    module_ref = target_type.meta.get(MODULE_REF) if target_type else None
    if (
        isinstance(module_ref, str)
        and ctx.layout is not None
        and module_ref in ctx.layout.module_classes
    ):
        return Emitted(f"{ctx.module_class(module_ref)}.{member}", POSTFIX)
    if ctx.receiver and isinstance(target, IRName) and target.name == ctx.receiver:
        text = f"this.{member}"
        return Emitted(f"{text}()" if _is_property(ctx.klass, name, ctx) else text, POSTFIX)
    target_rendered = emit_expression(target, ctx)
    if _is_gap(target_rendered):
        return target_rendered
    rendered = target_rendered.at_least(POSTFIX)
    if target_type is not None and target_type.meta.get(CLASS_REF):
        return Emitted(f"{rendered}.{member}", POSTFIX)
    info = ctx.class_of(target_type)
    if info is not None:
        if info.is_exception and name == "args":
            return Emitted(f"Py.list(Py.str({rendered}))", POSTFIX)
        text = f"{rendered}.{member}"
        return Emitted(f"{text}()" if _is_property(info, name, ctx) else text, POSTFIX)
    if target_type is None or target_type.kind is TypeKind.UNKNOWN:
        ctx.warn(
            "CW0681",
            f"type of the object whose attribute {name!r} is read is unknown",
            span=node.span,
            hint="annotate the variable so the access can be checked",
        )
    return Emitted(f"{rendered}.{member}", POSTFIX)


def _index_parts(node: IRIndex, ctx: JavaContext) -> tuple[str, str, IRType | None]:
    container_type = _type(node.target)
    key_type = (
        element_type(container_type)
        if not _is(container_type, "dict")
        else (container_type.args[0] if container_type and container_type.args else None)
    )
    target = emit_expression(node.target, ctx).at_least(POSTFIX)
    index = emit_expression(node.index, ctx, key_type).text
    return target, index, container_type


def _unchecked(node: IRIndex, ctx: JavaContext) -> bool:
    return (
        isinstance(node.target, IRName)
        and isinstance(node.index, IRName)
        and (node.target.name, node.index.name) in ctx.unchecked
        and _is(_type(node.target), "list")
    )


def _index(node: IRIndex, ctx: JavaContext, expected: IRType | None) -> Emitted:
    container_type = _type(node.target)
    if _is(container_type, "tuple"):
        index = node.index
        if (
            isinstance(index, IRLiteral)
            and isinstance(index.value, int)
            and container_type is not None
        ):
            position = index.value + (len(container_type.args) if index.value < 0 else 0)
            target = emit_expression(node.target, ctx).at_least(POSTFIX)
            return Emitted(f"{target}._{position}()", POSTFIX)
        return _unsupported(
            ctx, "Subscript", "a tuple can only be indexed by a constant in Java", node
        )
    target, key, _ = _index_parts(node, ctx)
    if _is(container_type, "dict"):
        return Emitted(f"Py.getKey({target}, {key})", POSTFIX)
    if _is(container_type, "str"):
        return Emitted(f"Py.charAt({target}, {key})", POSTFIX)
    if _unchecked(node, ctx):
        return Emitted(f"{target}.get((int) {key})", POSTFIX)
    info = ctx.class_of(container_type)
    if _has_method(info, "__getitem__", ctx):
        return Emitted(f"{target}.getItem_({key})", POSTFIX)
    return Emitted(f"Py.get({target}, {key})", POSTFIX)


def emit_store(target_node: IRNode, value: str, ctx: JavaContext) -> str:
    """The statement that stores *value* into *target_node* (without ``;``)."""
    if isinstance(target_node, IRIndex):
        container_type = _type(target_node.target)
        target, index, _ = _index_parts(target_node, ctx)
        if _is(container_type, "dict"):
            return f"{target}.put({index}, {value})"
        if _unchecked(target_node, ctx):
            return f"{target}.set((int) {index}, {value})"
        info = ctx.class_of(container_type)
        if _has_method(info, "__setitem__", ctx):
            return f"{target}.setItem_({index}, {value})"
        return f"Py.setItem({target}, {index}, {value})"
    return f"{emit_expression(target_node, ctx).text} = {value}"


def _slice(node: IRSlice, ctx: JavaContext, expected: IRType | None) -> Emitted:
    target = emit_expression(node.target, ctx).text
    bounds = [
        emit_expression(item, ctx).text if item is not None else "null"
        for item in (node.lower, node.upper, node.step)
    ]
    return Emitted(f"Py.slice({target}, {', '.join(bounds)})", POSTFIX)


# --------------------------------------------------------------- containers


def _element_of(container: IRType | None, position: int = 0) -> IRType | None:
    if (
        container is None
        or container.kind is not TypeKind.COLLECTION
        or len(container.args) <= position
    ):
        return None
    return container.args[position]


def _explicit(container: IRType | None, ctx: JavaContext, count: int) -> str:
    """``<Long>`` / ``<String, Long>`` type witnesses for a factory call."""
    if container is None or container.kind is not TypeKind.COLLECTION:
        return ""
    rendered = [render_type(arg, ctx, boxed=True) for arg in container.args[:count]]
    if len(rendered) != count or any(item is None for item in rendered):
        return ""
    return f"<{', '.join(item for item in rendered if item)}>"


def _target_collection(node: IRExpression, expected: IRType | None, name: str) -> IRType | None:
    if expected is not None and _is(expected, name):
        return expected
    own = _type(node)
    return own if _is(own, name) else None


def _array(node: IRArrayLiteral, ctx: JavaContext, expected: IRType | None) -> Emitted:
    container = _target_collection(node, expected, "list")
    element = _element_of(container)
    items = ", ".join(emit_expression(item, ctx, element).text for item in node.elements)
    return Emitted(f"Py.{_explicit(container, ctx, 1)}list({items})", POSTFIX)


def _set(node: IRSetLiteral, ctx: JavaContext, expected: IRType | None) -> Emitted:
    container = _target_collection(node, expected, "set")
    element = _element_of(container)
    items = ", ".join(emit_expression(item, ctx, element).text for item in node.elements)
    return Emitted(f"Py.{_explicit(container, ctx, 1)}set({items})", POSTFIX)


def _tuple(node: IRTupleLiteral, ctx: JavaContext, expected: IRType | None) -> Emitted:
    count = len(node.elements)
    if not 2 <= count <= 4:
        return _unsupported(
            ctx, "Tuple", f"a {count}-element tuple has no Java mapping", node, "use a class"
        )
    container = _target_collection(node, expected, "tuple")
    items = [
        emit_expression(item, ctx, _element_of(container, i)).text
        for i, item in enumerate(node.elements)
    ]
    return Emitted(f"new Py.Tuple{count}<>({', '.join(items)})", POSTFIX)


def _map(node: IRMapLiteral, ctx: JavaContext, expected: IRType | None) -> Emitted:
    container = _target_collection(node, expected, "dict")
    key_type, value_type = _element_of(container, 0), _element_of(container, 1)
    entries = ", ".join(
        f"Py.entry({emit_expression(entry.key, ctx, key_type).text}, {emit_expression(entry.value, ctx, value_type).text})"
        for entry in node.entries
    )
    return Emitted(f"Py.{_explicit(container, ctx, 2)}dict({entries})", POSTFIX)


def _template(node: IRTemplateString, ctx: JavaContext, expected: IRType | None) -> Emitted:
    parts: list[str] = []
    for part in node.parts:
        if isinstance(part, IRLiteral) and isinstance(part.value, str):
            if part.value:
                parts.append(java_string(part.value))
        elif _is(_type(part), "str"):
            parts.append(emit_expression(part, ctx).at_least(ADDITIVE + 1))
        else:
            parts.append(f"Py.str({emit_expression(part, ctx).text})")
    if not parts:
        return Emitted('""')
    if len(parts) == 1 and not parts[0].startswith(('"', "Py.str(")):
        parts.insert(0, '""')
    return Emitted(" + ".join(parts), ADDITIVE)


# --------------------------------------------------------------- functions


def _captures(body: IRNode, bound: set[str], ctx: JavaContext) -> dict[str, str]:
    """Copies for locals a lambda reads that Java does not see as final."""
    renames: dict[str, str] = {}
    for item in walk(body):
        if (
            isinstance(item, IRName)
            and item.name not in bound
            and item.name in ctx.locals
            and item.name in ctx.reassigned
            and item.name not in renames
            and item.name not in ctx.renames
        ):
            copy = ctx.temp("cap")
            ctx.pre.append(f"final var {copy} = {ctx.local(item.name)};")
            renames[item.name] = copy
    return renames


def _with_lambda_scope(
    bound: list[str], body: IRNode, ctx: JavaContext, render: Callable[[], str]
) -> str:
    renames = _captures(body, set(bound), ctx)
    saved_locals, saved_renames = set(ctx.locals), dict(ctx.renames)
    ctx.locals.update(bound)
    ctx.renames.update(renames)
    for name in bound:
        ctx.renames.pop(name, None)
    try:
        return render()
    finally:
        ctx.locals, ctx.renames = saved_locals, saved_renames


def _lambda(node: IRLambda, ctx: JavaContext, expected: IRType | None) -> Emitted:
    names = [param.name for param in node.params]
    params = ", ".join(safe_identifier(name) for name in names)
    head = f"({params})" if len(names) != 1 else params
    body = _with_lambda_scope(
        names, node.body or IRLiteral(None), ctx, lambda: emit_expression(node.body, ctx).text
    )
    return Emitted(f"{head} -> {body}", LAMBDA)


def _await(node: IRAwait, ctx: JavaContext, expected: IRType | None) -> Emitted:
    return _unsupported(
        ctx,
        "Await",
        "async/await is not translated to Java",
        node,
        "use CompletableFuture explicitly",
    )


def _iterable(node: IRNode, ctx: JavaContext) -> str:
    """An expression Java's for-each and the comprehension helpers can iterate."""
    node_type = _type(node)
    rendered = emit_expression(node, ctx).at_least(POSTFIX)
    if _is(node_type, "dict"):
        return f"{rendered}.keySet()"
    if _is(node_type, "str"):
        return f"Py.chars({rendered})"
    return rendered


def _comprehension(node: IRComprehension, ctx: JavaContext, expected: IRType | None) -> Emitted:
    variable = safe_identifier(node.target)
    iterable = _iterable(node.iterable, ctx)
    result_type = expected if expected is not None and _is(expected, node.form) else _type(node)
    element_ir = _element_of(result_type, 1 if node.form == "dict" else 0)
    bodies: list[IRNode] = [
        item for item in (node.element, node.key, node.condition) if item is not None
    ]
    holder = IRTupleLiteral(list(bodies))  # type: ignore[arg-type]

    def render() -> str:
        pieces = [iterable]
        if node.condition is not None:
            pieces.append(f"{variable} -> {condition(node.condition, ctx)}")
        if node.form == "dict":
            pieces.append(
                f"{variable} -> {emit_expression(node.key, ctx, _element_of(result_type, 0)).text}"
            )
        pieces.append(f"{variable} -> {emit_expression(node.element, ctx, element_ir).text}")
        return ", ".join(pieces)

    arguments = _with_lambda_scope([node.target], holder, ctx, render)
    base = {"list": "listComp", "set": "setComp", "dict": "dictComp"}[node.form]
    name = f"{base}If" if node.condition is not None else base
    count = 2 if node.form == "dict" else 1
    # Witness only the result element type(s); the iterated type is inferred.
    witness = _explicit(result_type, ctx, count)
    if witness:
        source = render_type(element_type(_type(node.iterable)), ctx, boxed=True)
        if _is(_type(node.iterable), "dict"):
            source = render_type(_element_of(_type(node.iterable), 0), ctx, boxed=True)
        elif _is(_type(node.iterable), "str"):
            source = "String"
        witness = f"<{source}, {witness[1:-1]}>" if source else ""
    return Emitted(f"Py.{witness}{name}({arguments})", POSTFIX)


# --------------------------------------------------------------------- calls


def _argument(node: IRExpression, ctx: JavaContext, expected: IRType | None = None) -> str:
    return emit_expression(node, ctx, expected).text


def lower_arguments(
    params: list[IRParam], call: IRCall, ctx: JavaContext, *, callee_name: str
) -> list[str]:
    """Match *call*'s arguments to *params* and render every one positionally."""
    slots: list[str | None] = [None] * len(params)
    variadic = next((i for i, p in enumerate(params) if p.style == "var_positional"), None)
    extra: list[str] = []
    for position, argument in enumerate(call.args):
        if variadic is not None and position >= variadic:
            extra.append(_argument(argument, ctx, params[variadic].param_type))
        elif position < len(params):
            slots[position] = _argument(argument, ctx, params[position].param_type)
        else:
            ctx.warn("CW0684", f"too many arguments in call to {callee_name!r}", span=call.span)
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
    rendered: list[str] = []
    for position, param in enumerate(params):
        if param.style == "var_keyword":
            continue
        if position == variadic:
            witness = render_type(param.param_type, ctx, boxed=True)
            rendered.append(f"Py.{f'<{witness}>' if witness else ''}list({', '.join(extra)})")
            continue
        slot = slots[position]
        if slot is None:
            if param.default is None:
                ctx.warn(
                    "CW0686",
                    f"missing argument {param.name!r} in call to {callee_name!r}",
                    span=call.span,
                )
                slot = 'Py.unsupported("missing argument")'
            else:
                slot = emit_expression(param.default, ctx, param.param_type).text
        rendered.append(slot)
    return rendered


def _exception_message(args: list[IRExpression], ctx: JavaContext) -> list[str]:
    """Java exceptions take one String message."""
    if not args:
        return []
    first = args[0]
    text = emit_expression(first, ctx).text
    return [text if _is(_type(first), "str") else f"Py.str({text})"]


def _call(node: IRCall, ctx: JavaContext, expected: IRType | None) -> Emitted:
    callee = node.callee
    if (
        isinstance(callee, IRAttribute)
        and callee.name == "__init__"
        and is_super_call(callee.target)
    ):
        return _unsupported(
            ctx,
            "Call",
            "super().__init__() must be the first statement of a constructor in Java",
            node,
        )
    if isinstance(callee, IRName) and callee.name not in ctx.locals and callee.name != ctx.receiver:
        entry = ctx.scope_entry(callee.name)
        if entry is None:
            if callee.name in BUILTIN_EXCEPTIONS:
                exception = EXCEPTIONS.get(callee.name, "RuntimeException")
                return Emitted(
                    f"new {exception}({', '.join(_exception_message(node.args, ctx))})", POSTFIX
                )
            mapped = _builtin(callee.name, node, ctx, expected)
            if mapped is not None:
                return mapped
        elif entry.kind is ScopeKind.CLASS and ctx.index is not None:
            return _construct(ctx.index.classes[entry.qualified], node, ctx)
        elif entry.kind is ScopeKind.FUNCTION and ctx.index is not None:
            function = ctx.index.functions[entry.qualified].node
            args = lower_arguments(function.params, node, ctx, callee_name=callee.name)
            return Emitted(f"{ctx.member_spelling(entry)}({', '.join(args)})", POSTFIX)
        elif entry.kind is ScopeKind.EXTERNAL:
            return _external_call(entry.qualified, node, ctx)
    if (
        ctx.class_receiver
        and isinstance(callee, IRName)
        and callee.name == ctx.class_receiver
        and ctx.klass
    ):
        return _construct(ctx.klass, node, ctx)
    if isinstance(callee, IRAttribute):
        method = _method_call(node, callee, ctx)
        if method is not None:
            return method
    callee_type = _type(callee)
    if node.meta.get(CONSTRUCTS_CLASS):
        info = ctx.class_of(_type(node))
        if info is not None:
            return _construct(info, node, ctx)
    callee_rendered = emit_expression(callee, ctx)
    if _is_gap(callee_rendered):
        return callee_rendered
    args = [_argument(item, ctx) for item in node.args]
    if node.keywords:
        ctx.warn(
            "CW0685",
            "keyword arguments to a callee with no known signature are dropped",
            span=node.span,
        )
    rendered = callee_rendered.at_least(POSTFIX)
    if callee_type is not None and callee_type.kind is TypeKind.CALLABLE and callee_type.args:
        void = callee_type.args[-1].kind is TypeKind.NONE
        arity = len(callee_type.args) - 1
        invoke = {
            0: "run" if void else "get",
            1: "accept" if void else "apply",
            2: "accept" if void else "apply",
        }.get(arity, "apply")
        return Emitted(f"{rendered}.{invoke}({', '.join(args)})", POSTFIX)
    ctx.warn(
        "CW0699",
        "call of a value whose type is unknown",
        span=node.span,
        hint="annotate the callable",
    )
    return Emitted(f"{rendered}({', '.join(args)})", POSTFIX)


def _construct(info: ClassInfo, node: IRCall, ctx: JavaContext) -> Emitted:
    spelled = ctx.class_spelling(info)
    found = ctx.index.constructor(info) if ctx.index is not None else None
    if found is None and (info.is_exception or info.builtin_base):
        args = _exception_message(node.args, ctx)
    else:
        params = found[1].params if found is not None else []
        args = lower_arguments(params, node, ctx, callee_name=info.name)
    return Emitted(f"new {spelled}({', '.join(args)})", POSTFIX)


def _method_signature(info: ClassInfo | None, name: str, ctx: JavaContext) -> IRFunction | None:
    if info is None or ctx.index is None:
        return None
    found = ctx.index.find_method(info, name)
    return found[1] if found else None


def _method_args(info: ClassInfo | None, name: str, node: IRCall, ctx: JavaContext) -> list[str]:
    method = _method_signature(info, name, ctx)
    if method is None:
        return [_argument(item, ctx) for item in node.args]
    return lower_arguments(method.params, node, ctx, callee_name=name)


def _method_call(node: IRCall, callee: IRAttribute, ctx: JavaContext) -> Emitted | None:
    target = callee.target
    name = callee.name
    member = safe_identifier(name)
    target_type = _type(target)
    if is_super_call(target) and ctx.klass is not None and ctx.index is not None:
        base = ctx.index.classes.get(ctx.klass.base) if ctx.klass.base else None
        return Emitted(f"super.{member}({', '.join(_method_args(base, name, node, ctx))})", POSTFIX)
    if isinstance(target, IRName) and target.name not in ctx.locals and target.name != ctx.receiver:
        entry = ctx.scope_entry(target.name)
        if entry is not None and entry.kind is ScopeKind.EXTERNAL:
            return _external_call(f"{entry.qualified}.{name}", node, ctx)
    module_ref = target_type.meta.get(MODULE_REF) if target_type else None
    if isinstance(module_ref, str) and ctx.index is not None:
        entry = ctx.index.lookup(module_ref, name)
        if entry is not None and entry.kind is ScopeKind.CLASS:
            return _construct(ctx.index.classes[entry.qualified], node, ctx)
        if entry is not None and entry.kind is ScopeKind.FUNCTION:
            function = ctx.index.functions[entry.qualified].node
            args = lower_arguments(function.params, node, ctx, callee_name=name)
            return Emitted(f"{ctx.module_class(entry.module)}.{member}({', '.join(args)})", POSTFIX)
        return None
    if target_type is not None and target_type.meta.get(CLASS_REF):
        info = ctx.class_of(target_type)
        if info is not None:
            args = _method_args(info, name, node, ctx)
            return Emitted(f"{ctx.class_spelling(info)}.{member}({', '.join(args)})", POSTFIX)
    is_self = bool(ctx.receiver and isinstance(target, IRName) and target.name == ctx.receiver)
    info = ctx.klass if is_self else ctx.class_of(target_type)
    if info is not None:
        args = _method_args(info, name, node, ctx)
        receiver = "this" if is_self else emit_expression(target, ctx).at_least(POSTFIX)
        method = _method_signature(info, name, ctx)
        if method is not None and (method.is_static or method.meta.get("pythonClassMethod")):
            receiver = ctx.class_spelling(info)
        return Emitted(f"{receiver}.{member}({', '.join(args)})", POSTFIX)
    kind = _builtin_kind(target_type)
    if kind is not None:
        return _builtin_method(kind, node, callee, ctx)
    return None


def _builtin_kind(node_type: IRType | None) -> str | None:
    if node_type is None:
        return None
    if node_type.kind is TypeKind.PRIMITIVE and node_type.name == "str":
        return "str"
    if node_type.kind is TypeKind.COLLECTION and node_type.name in ("list", "dict", "set"):
        return node_type.name
    return None


def _builtin_method(kind: str, node: IRCall, callee: IRAttribute, ctx: JavaContext) -> Emitted:
    name = callee.name
    receiver_type = _type(callee.target)
    receiver = emit_expression(callee.target, ctx).at_least(POSTFIX)
    element = element_type(receiver_type)
    key_type = _element_of(receiver_type, 0)
    value_type = _element_of(receiver_type, 1)
    typed_arg = {"append", "add", "remove", "discard", "index", "count", "insert"}
    args = [
        _argument(item, ctx, element if name in typed_arg and kind != "dict" else None)
        for item in node.args
    ]
    keywords = {keyword.name: keyword.value for keyword in node.keywords}
    joined = ", ".join(args)

    def call(text: str) -> Emitted:
        return Emitted(text, POSTFIX)

    if kind == "str":
        simple = {
            "upper": f"{receiver}.toUpperCase()",
            "lower": f"{receiver}.toLowerCase()",
            "isdigit": f"Py.isdigit({receiver})",
            "isalpha": f"Py.isalpha({receiver})",
            "isalnum": f"Py.isalnum({receiver})",
            "isspace": f"Py.isspace({receiver})",
            "isupper": f"Py.isupper({receiver})",
            "islower": f"Py.islower({receiver})",
            "capitalize": f"Py.capitalize({receiver})",
            "title": f"Py.title({receiver})",
        }
        if name in simple and not args:
            return call(simple[name])
        if name in ("strip", "lstrip", "rstrip") and not args:
            return call(
                f"{receiver}.{ {'strip': 'strip', 'lstrip': 'stripLeading', 'rstrip': 'stripTrailing'}[name] }()"
            )
        if name in ("startswith", "endswith") and len(args) == 1:
            return call(
                f"{receiver}.{'startsWith' if name == 'startswith' else 'endsWith'}({joined})"
            )
        if name == "find" and len(args) == 1:
            return call(f"(long) {receiver}.indexOf({joined})")
        if name == "rfind" and len(args) == 1:
            return call(f"(long) {receiver}.lastIndexOf({joined})")
        if name == "replace" and len(args) == 2:
            return call(f"{receiver}.replace({joined})")
        if name in ("split", "index", "count", "zfill") and len(args) <= 1:
            return call(f"Py.{name}({', '.join([receiver, *args])})")
        if name == "join" and len(args) == 1:
            return call(f"String.join({receiver}, {joined})")
    if kind == "list":
        if name == "append" and len(args) == 1:
            return call(f"{receiver}.add({joined})")
        if name == "extend" and len(args) == 1:
            return call(f"{receiver}.addAll({joined})")
        if name in ("insert", "pop", "remove", "index", "count"):
            return call(f"Py.{name}({', '.join([receiver, *args])})")
        if name == "clear" and not args:
            return call(f"{receiver}.clear()")
        if name == "copy" and not args:
            return call(f"new ArrayList<>({receiver})")
        if name == "reverse" and not args:
            return call(f"Collections.reverse({receiver})")
        if name == "sort" and not args:
            reverse = condition(keywords["reverse"], ctx) if "reverse" in keywords else "false"
            if "key" in keywords:
                return call(f"Py.sortBy({receiver}, {_argument(keywords['key'], ctx)}, {reverse})")
            return call(f"Py.sort({receiver}, {reverse})")
    if kind == "dict":
        key_args = [emit_expression(item, ctx, key_type).text for item in node.args[:1]]
        if name == "get" and len(args) == 1:
            return call(f"{receiver}.get({key_args[0]})")
        if name == "get" and len(args) == 2:
            fallback = emit_expression(node.args[1], ctx, value_type).text
            return call(f"{receiver}.getOrDefault({key_args[0]}, {fallback})")
        if name == "keys" and not args:
            return call(f"new ArrayList<>({receiver}.keySet())")
        if name == "values" and not args:
            return call(f"new ArrayList<>({receiver}.values())")
        if name == "items" and not args:
            return call(f"Py.items({receiver})")
        if name == "pop" and len(args) == 1:
            return call(f"Py.pop({receiver}, {key_args[0]})")
        if name == "pop" and len(args) == 2:
            fallback = emit_expression(node.args[1], ctx, value_type).text
            return call(
                f"({receiver}.containsKey({key_args[0]}) ? {receiver}.remove({key_args[0]}) : {fallback})"
            )
        if name == "setdefault" and len(args) == 2:
            fallback = emit_expression(node.args[1], ctx, value_type).text
            return call(f"Py.setdefault({receiver}, {key_args[0]}, {fallback})")
        if name == "update" and len(args) == 1:
            return call(f"{receiver}.putAll({joined})")
        if name == "clear" and not args:
            return call(f"{receiver}.clear()")
        if name == "copy" and not args:
            return call(f"new LinkedHashMap<>({receiver})")
    if kind == "set":
        if name == "add" and len(args) == 1:
            return call(f"{receiver}.add({joined})")
        if name == "remove" and len(args) == 1:
            return call(f"Py.remove({receiver}, {joined})")
        if name == "discard" and len(args) == 1:
            return call(f"{receiver}.remove({joined})")
        if name == "update" and len(args) == 1:
            return call(f"{receiver}.addAll({joined})")
        if name in ("union", "intersection", "difference") and len(args) == 1:
            return call(f"Py.{name}({receiver}, {joined})")
        if name == "clear" and not args:
            return call(f"{receiver}.clear()")
        if name == "copy" and not args:
            return call(f"new LinkedHashSet<>({receiver})")
    return _unsupported(
        ctx, "Call", f"{kind}.{name}() with these arguments has no Java mapping in this build", node
    )


def _external_call(qualified: str, node: IRCall, ctx: JavaContext) -> Emitted:
    module, _, member = qualified.rpartition(".")
    args = [emit_expression(item, ctx).text for item in node.args]
    if module == "math":
        if member == "log":
            if len(args) == 2:
                return Emitted(f"(Math.log({args[0]}) / Math.log({args[1]}))", PRIMARY)
            if len(args) == 1:
                return Emitted(f"Math.log({args[0]})", POSTFIX)
        template = _MATH_FUNCTIONS.get(member)
        if template is not None and template.count("{") == len(args):
            return Emitted(
                template.format(*args), POSTFIX if not template.startswith("(") else PRIMARY
            )
    return _external_name(qualified, node, ctx)


# ------------------------------------------------------------------ builtins


def _builtin(name: str, node: IRCall, ctx: JavaContext, expected: IRType | None) -> Emitted | None:
    args = node.args
    arg_types = [_type(item) for item in args]
    rendered = [_argument(item, ctx) for item in args]
    joined = ", ".join(rendered)
    keywords = {keyword.name: keyword.value for keyword in node.keywords}

    def call(text: str, precedence: int = POSTFIX) -> Emitted:
        return Emitted(text, precedence)

    if name in UNMAPPED_BUILTINS:
        return _unsupported(
            ctx, "Call", f"builtin {name}() has no Java mapping", node, UNMAPPED_BUILTINS[name]
        )
    if name == "print":
        if keywords:
            ctx.warn(
                "CW0687",
                "print() keyword arguments (sep, end, file) are not translated",
                span=node.span,
            )
        return call(f"Py.print({joined})")
    if name == "len" and len(args) == 1:
        subject = emit_expression(args[0], ctx).at_least(POSTFIX)
        if _is(arg_types[0], "str"):
            return call(f"(long) {subject}.length()", UNARY)
        if _builtin_kind(arg_types[0]) in ("list", "set", "dict"):
            return call(f"(long) {subject}.size()", UNARY)
        info = ctx.class_of(arg_types[0])
        if _has_method(info, "__len__", ctx):
            return call(f"{subject}.len_()")
        return call(f"Py.len({joined})")
    if name == "str":
        return call(f"Py.str({joined})") if args else Emitted('""')
    if name == "repr" and len(args) == 1:
        return call(f"Py.repr({joined})")
    if name == "abs" and len(args) == 1:
        return call(f"Math.abs({joined})")
    if name == "ord" and len(args) == 1:
        return call(f"(long) {emit_expression(args[0], ctx).at_least(POSTFIX)}.charAt(0)", UNARY)
    if name == "chr" and len(args) == 1:
        return call(f"String.valueOf((char) {emit_expression(args[0], ctx).at_least(UNARY)})")
    if name in ("any", "all") and len(args) == 1:
        return call(f"Py.{name}({_iterable(args[0], ctx)})")
    if name == "pow" and len(args) == 2:
        function = "Py.pow" if all(_is_int(item) for item in arg_types) else "Math.pow"
        return call(f"{function}({joined})")
    if name == "int":
        if not args:
            return Emitted("0L")
        if len(args) > 1:
            return _unsupported(ctx, "Call", "int() with a base is not translated", node)
        subject = emit_expression(args[0], ctx).at_least(UNARY)
        if _is(arg_types[0], "str"):
            return call(f"Py.toInt({rendered[0]})")
        if _is(arg_types[0], "bool"):
            return call(f"({subject} ? 1L : 0L)", PRIMARY)
        if _is(arg_types[0], "int"):
            return Emitted(rendered[0], PRIMARY)
        return call(f"(long) {subject}", UNARY)
    if name == "float":
        if not args:
            return Emitted("0.0")
        if _is(arg_types[0], "str"):
            return call(f"Py.toFloat({rendered[0]})")
        return call(f"(double) {emit_expression(args[0], ctx).at_least(UNARY)}", UNARY)
    if name == "bool":
        return Emitted(condition(args[0], ctx), POSTFIX) if args else Emitted("false")
    if name == "round":
        if len(args) == 1:
            return call(f"(long) Math.rint({joined})", UNARY)
        return call(f"Py.round({joined})")
    if name in ("min", "max"):
        if keywords:
            ctx.warn("CW0687", f"{name}() keyword arguments are not translated", span=node.span)
        if len(args) == 1:
            return call(f"Py.{name}({_iterable(args[0], ctx)})")
        if len(args) >= 2 and all(_is_number(item) for item in arg_types):
            result = rendered[0]
            for item in rendered[1:]:
                result = f"Math.{name}({result}, {item})"
            return call(result)
        return call(f"Py.{name}(Py.list({joined}))")
    if name == "sum" and args:
        element = element_type(arg_types[0])
        function = "Py.sumDouble" if _is(element, "float") else "Py.sumLong"
        total = f"{function}({_iterable(args[0], ctx)})"
        if len(args) == 2:
            return call(f"({total} + {rendered[1]})", PRIMARY)
        return call(total)
    if name == "sorted" and args:
        reverse = condition(keywords["reverse"], ctx) if "reverse" in keywords else "false"
        if "key" in keywords:
            return call(
                f"Py.sortedBy({_iterable(args[0], ctx)}, {_argument(keywords['key'], ctx)}, {reverse})"
            )
        return call(f"Py.sorted({_iterable(args[0], ctx)}, {reverse})")
    if name in ("list", "set", "dict", "tuple"):
        target = expected if expected is not None and _is(expected, name) else _type(node)
        if not args:
            factory = {"list": "list", "set": "set", "dict": "dict"}.get(name)
            if factory is None:
                return _unsupported(ctx, "Call", "an empty tuple has no Java mapping", node)
            return call(f"Py.{_explicit(target, ctx, 2 if name == 'dict' else 1)}{factory}()")
        if name == "list":
            if _is(arg_types[0], "str"):
                return call(f"Py.chars({rendered[0]})")
            return call(f"new ArrayList<>({_iterable(args[0], ctx)})")
        if name == "set":
            return call(f"new LinkedHashSet<>({_iterable(args[0], ctx)})")
        if name == "dict":
            return call(f"new LinkedHashMap<>({rendered[0]})")
        return _unsupported(ctx, "Call", "tuple() of an iterable has no Java mapping", node)
    if name == "range":
        return call(f"Py.range({joined})")
    if name == "divmod" and len(args) == 2:
        if all(_is_int(item) for item in arg_types):
            return call(f"new Py.Tuple2<>(Math.floorDiv({joined}), Math.floorMod({joined}))")
        return call(f"new Py.Tuple2<>(Py.floorDiv({joined}), Py.mod({joined}))")
    if name == "isinstance" and len(args) == 2:
        return _isinstance(node, ctx)
    return None


def _isinstance(node: IRCall, ctx: JavaContext) -> Emitted:
    subject, kinds = node.args
    candidates = kinds.elements if isinstance(kinds, IRTupleLiteral) else [kinds]
    subject_text = emit_expression(subject, ctx).at_least(POSTFIX)
    tests: list[str] = []
    boxes = {
        "int": "Long",
        "float": "Double",
        "str": "String",
        "bool": "Boolean",
        "list": "List",
        "dict": "Map",
        "set": "Set",
    }
    for candidate in candidates:
        if not isinstance(candidate, IRName):
            tests.append("false")
            continue
        info = ctx.class_of(candidate.value_type)
        if info is not None:
            tests.append(f"{subject_text} instanceof {ctx.class_spelling(info)}")
        elif candidate.name in boxes:
            tests.append(f"((Object) {subject_text}) instanceof {boxes[candidate.name]}")
        elif candidate.name in BUILTIN_EXCEPTIONS:
            tests.append(
                f"{subject_text} instanceof {EXCEPTIONS.get(candidate.name, 'RuntimeException')}"
            )
        else:
            ctx.warn(
                "CW0688",
                f"isinstance check against {candidate.name!r} has no Java mapping",
                span=node.span,
            )
            tests.append("false")
    if len(tests) == 1:
        return Emitted(tests[0], RELATIONAL)
    return Emitted(" || ".join(f"({test})" for test in tests), LOGICAL_OR)


_Handler = Callable[[IRNode, JavaContext, IRType | None], Emitted]

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
