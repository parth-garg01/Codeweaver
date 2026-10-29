"""IR expressions to Go expressions.

Go's operator set is close enough to Python's that most expressions translate
directly.  The interesting cases are the ones that do not, and each is reported
rather than approximated:

* ``**`` has no operator; integer exponentiation becomes a loop a human has to
  write, so it is reported.
* ``in`` has no operator either.  For a map it is a two-value index; for a
  slice there is no expression form at all, so that is reported.
* an f-string becomes ``fmt.Sprintf`` with ``%v`` holes, which formats
  differently from Python's ``str()`` for several types.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from codeweaver.backends.common import escape_double_quoted
from codeweaver.backends.go.context import GoEmitContext
from codeweaver.backends.go.naming import GO_POLICY, exported_name
from codeweaver.backends.go.types import render_type
from codeweaver.ir.base import IRNode, IRUnsupported
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRBinary,
    IRCall,
    IRCompare,
    IRConditional,
    IRIndex,
    IRLiteral,
    IRLogical,
    IRMapLiteral,
    IRName,
    IRSetLiteral,
    IRSlice,
    IRTemplateString,
    IRTupleLiteral,
    IRUnary,
)
from codeweaver.ir.operators import BinaryOp, CompareOp, LogicalOp, UnaryOp
from codeweaver.ir.types import TypeKind

__all__ = ["Emitted", "emit_expression", "emit_params"]

# Go precedence, higher binds tighter.
_PRIMARY = 7
_UNARY = 6
_MUL = 5
_ADD = 4
_COMPARE = 3
_AND = 2
_OR = 1

_BINARY: dict[BinaryOp, tuple[str, int]] = {
    BinaryOp.ADD: ("+", _ADD),
    BinaryOp.SUB: ("-", _ADD),
    BinaryOp.MUL: ("*", _MUL),
    BinaryOp.DIV: ("/", _MUL),
    BinaryOp.FLOOR_DIV: ("/", _MUL),
    BinaryOp.MOD: ("%", _MUL),
    BinaryOp.BIT_AND: ("&", _MUL),
    BinaryOp.BIT_OR: ("|", _ADD),
    BinaryOp.BIT_XOR: ("^", _ADD),
    BinaryOp.SHIFT_LEFT: ("<<", _MUL),
    BinaryOp.SHIFT_RIGHT: (">>", _MUL),
}

_COMPARE_OPS: dict[CompareOp, str] = {
    CompareOp.EQ: "==",
    CompareOp.NE: "!=",
    CompareOp.LT: "<",
    CompareOp.LE: "<=",
    CompareOp.GT: ">",
    CompareOp.GE: ">=",
    CompareOp.IS: "==",
    CompareOp.IS_NOT: "!=",
}

_UNARY_OPS: dict[UnaryOp, str] = {
    UnaryOp.NEG: "-",
    UnaryOp.POS: "+",
    UnaryOp.NOT: "!",
    UnaryOp.BIT_NOT: "^",
}

#: Python builtin -> how Go spells it.  Only the ones with a faithful
#: counterpart; everything else is reported by :func:`_call`.
_BUILTINS: dict[str, str] = {
    "len": "len",
    "abs": "abs",
    "min": "min",
    "max": "max",
}


@dataclass(frozen=True, slots=True)
class Emitted:
    """A rendered expression and the precedence of its outermost operator."""

    text: str
    precedence: int = _PRIMARY

    def at_least(self, needed: int) -> str:
        return f"({self.text})" if self.precedence < needed else self.text


def emit_expression(node: IRNode | None, ctx: GoEmitContext) -> Emitted:
    """Render an IR expression as Go."""
    if node is None:
        return Emitted("nil")
    if isinstance(node, IRUnsupported):
        return Emitted(f"{ctx.record_gap(node)} nil")
    handler = _HANDLERS.get(type(node))
    if handler is None:
        ctx.warn(
            "CW0701",
            f"no Go mapping for IR node {node.kind!r}",
            span=node.span,
            hint="the expression is emitted as a marker",
        )
        return Emitted(f"/* unmapped {node.kind} */ nil")
    return handler(node, ctx)


def _literal(node: IRLiteral, ctx: GoEmitContext) -> Emitted:
    value = node.value
    if value is None:
        return Emitted("nil")
    if isinstance(value, bool):
        return Emitted("true" if value else "false")
    if isinstance(value, int | float):
        return Emitted(node.source_text or repr(value))
    if isinstance(value, str):
        return Emitted(escape_double_quoted(value))
    if isinstance(value, bytes):
        joined = ", ".join(str(byte) for byte in value)
        return Emitted(f"[]byte{{{joined}}}", _PRIMARY)
    ctx.warn(
        "CW0702",
        f"literal of type {type(value).__name__} has no Go spelling",
        span=node.span,
    )
    return Emitted("nil")


def _name(node: IRName, ctx: GoEmitContext) -> Emitted:
    if ctx.receiver and node.name == ctx.receiver:
        return Emitted(ctx.receiver)
    return Emitted(GO_POLICY.identifier(ctx.rename(node.name)))


def _binary(node: IRBinary, ctx: GoEmitContext) -> Emitted:
    if node.op is BinaryOp.POW:
        ctx.warn(
            "CW0703",
            "Go has no exponentiation operator",
            span=node.span,
            hint="use math.Pow for floats, or a loop for integers",
        )
        left = emit_expression(node.left, ctx).text
        right = emit_expression(node.right, ctx).text
        ctx.need("math")
        return Emitted(f"math.Pow(float64({left}), float64({right}))", _PRIMARY)
    if node.op is BinaryOp.MAT_MUL:
        ctx.warn("CW0704", "matrix multiplication has no Go operator", span=node.span)
        return Emitted("nil")
    spelling, precedence = _BINARY[node.op]
    if node.op is BinaryOp.FLOOR_DIV:
        # Go's integer division already truncates toward zero.  Python floors
        # toward negative infinity, so the two disagree on negative operands.
        ctx.warn(
            "CW0705",
            "integer division truncates toward zero in Go but floors in Python",
            span=node.span,
            hint="they disagree when either operand is negative",
        )
    left, right = _coerce_numeric(node, precedence, ctx)
    return Emitted(f"{left} {spelling} {right}", precedence)


def _numeric_kind(expression: Any) -> str:
    """``"int"``, ``"float"`` or ``""`` for an operand whose type is known."""
    value_type = getattr(expression, "value_type", None)
    if value_type is None or value_type.kind is not TypeKind.PRIMITIVE:
        return ""
    return value_type.name if value_type.name in ("int", "float") else ""


def _coerce_numeric(node: IRBinary, precedence: int, ctx: GoEmitContext) -> tuple[str, str]:
    """Render both operands, converting an int to float64 where Python would.

    Python promotes ``int`` to ``float`` in mixed arithmetic.  Go refuses to
    mix them at all, so a faithful translation has to insert the conversion
    that Python performed implicitly.  Only a *known* mismatch is converted:
    guessing from an unknown type would risk converting something that was
    already correct, and the Go compiler reports the mismatch clearly anyway.
    """
    left_kind = _numeric_kind(node.left)
    right_kind = _numeric_kind(node.right)
    left = emit_expression(node.left, ctx)
    right = emit_expression(node.right, ctx)
    if {left_kind, right_kind} == {"int", "float"}:
        ctx.warn(
            "CW0716",
            "mixed int and float arithmetic: Go does not promote, so an "
            "explicit float64 conversion was inserted",
            span=node.span,
            hint="Python performed this conversion implicitly",
        )
        if left_kind == "int":
            return f"float64({left.text})", right.at_least(precedence + 1)
        return left.at_least(precedence), f"float64({right.text})"
    return left.at_least(precedence), right.at_least(precedence + 1)


def _unary(node: IRUnary, ctx: GoEmitContext) -> Emitted:
    operand = emit_expression(node.operand, ctx).at_least(_UNARY)
    return Emitted(f"{_UNARY_OPS[node.op]}{operand}", _UNARY)


def _compare(node: IRCompare, ctx: GoEmitContext) -> Emitted:
    if node.op in (CompareOp.IN, CompareOp.NOT_IN):
        return _membership(node, ctx)
    spelling = _COMPARE_OPS[node.op]
    left = emit_expression(node.left, ctx).at_least(_COMPARE)
    right = emit_expression(node.right, ctx).at_least(_COMPARE + 1)
    return Emitted(f"{left} {spelling} {right}", _COMPARE)


def _membership(node: IRCompare, ctx: GoEmitContext) -> Emitted:
    container_type = getattr(node.right, "value_type", None)
    name = container_type.name if container_type else ""
    container = emit_expression(node.right, ctx).at_least(_PRIMARY)
    needle = emit_expression(node.left, ctx).text
    if name in ("dict", "set"):
        # `_, ok := m[k]` is a statement, not an expression, so the only
        # expression form is the comma-ok index inside a composite literal.
        test = f"func() bool {{ _, ok := {container}[{needle}]; return ok }}()"
        if node.op is CompareOp.NOT_IN:
            return Emitted(f"!{test}", _UNARY)
        return Emitted(test, _PRIMARY)
    if name == "str":
        ctx.need("strings")
        test = f"strings.Contains({container}, {needle})"
        return Emitted(f"!{test}" if node.op is CompareOp.NOT_IN else test, _PRIMARY)
    ctx.need("slices")
    if name != "list":
        ctx.warn(
            "CW0706",
            "membership test assumes a slice container",
            span=node.span,
            hint="annotate the container so the right form can be generated",
        )
    test = f"slices.Contains({container}, {needle})"
    return Emitted(f"!{test}" if node.op is CompareOp.NOT_IN else test, _PRIMARY)


def _logical(node: IRLogical, ctx: GoEmitContext) -> Emitted:
    spelling, precedence = ("&&", _AND) if node.op is LogicalOp.AND else ("||", _OR)
    parts = [emit_expression(item, ctx).at_least(precedence + 1) for item in node.operands]
    if not parts:
        return Emitted("true" if node.op is LogicalOp.AND else "false")
    if len(parts) == 1:
        return Emitted(parts[0], precedence)
    return Emitted(f" {spelling} ".join(parts), precedence)


def _call(node: IRCall, ctx: GoEmitContext) -> Emitted:
    arguments = [emit_expression(item, ctx).text for item in node.args]
    if node.keywords or node.star_args or node.star_keywords:
        ctx.warn(
            "CW0707",
            "Go has no keyword or variadic-spread arguments",
            span=node.span,
            hint="pass a struct, or expand the call explicitly",
        )
    callee = node.callee
    if isinstance(callee, IRName):
        name = callee.name
        if name == "print":
            ctx.need("fmt")
            return Emitted(f"fmt.Println({', '.join(arguments)})", _PRIMARY)
        if name in _BUILTINS:
            return Emitted(f"{_BUILTINS[name]}({', '.join(arguments)})", _PRIMARY)
        if name in ("str", "int", "float", "bool"):
            return _conversion(name, arguments, node, ctx)
        if node.meta.get("constructsClass"):
            # Python calls a class to construct it.  Go's convention is a
            # package-level NewT returning a pointer, which is what the module
            # emitter generates for every struct — including one whose Python
            # class had no __init__.
            return Emitted(f"New{exported_name(name)}({', '.join(arguments)})", _PRIMARY)
    rendered_callee = emit_expression(callee, ctx).at_least(_PRIMARY)
    return Emitted(f"{rendered_callee}({', '.join(arguments)})", _PRIMARY)


def _conversion(name: str, arguments: list[str], node: IRCall, ctx: GoEmitContext) -> Emitted:
    if len(arguments) != 1:
        ctx.warn(
            "CW0708", f"{name}() with {len(arguments)} arguments is not mapped", span=node.span
        )
        return Emitted("nil")
    value = arguments[0]
    if name == "str":
        ctx.need("fmt")
        return Emitted(f'fmt.Sprintf("%v", {value})', _PRIMARY)
    if name == "int":
        ctx.warn(
            "CW0709",
            "int() becomes a numeric conversion, which does not parse a string",
            span=node.span,
            hint="use strconv.Atoi when the argument is text",
        )
        return Emitted(f"int({value})", _PRIMARY)
    if name == "float":
        return Emitted(f"float64({value})", _PRIMARY)
    return Emitted(f"bool({value})", _PRIMARY)


def _attribute(node: IRAttribute, ctx: GoEmitContext) -> Emitted:
    target = emit_expression(node.target, ctx).at_least(_PRIMARY)
    return Emitted(f"{target}.{exported_name(node.name)}", _PRIMARY)


def _index(node: IRIndex, ctx: GoEmitContext) -> Emitted:
    target = emit_expression(node.target, ctx).at_least(_PRIMARY)
    index = emit_expression(node.index, ctx).text
    return Emitted(f"{target}[{index}]", _PRIMARY)


def _slice(node: IRSlice, ctx: GoEmitContext) -> Emitted:
    target = emit_expression(node.target, ctx).at_least(_PRIMARY)
    if node.step is not None:
        ctx.warn("CW0710", "a stepped slice has no Go equivalent", span=node.span)
        return Emitted(f"/* stepped slice unsupported */ {target}", _PRIMARY)
    lower = emit_expression(node.lower, ctx).text if node.lower is not None else ""
    upper = emit_expression(node.upper, ctx).text if node.upper is not None else ""
    return Emitted(f"{target}[{lower}:{upper}]", _PRIMARY)


def _array(node: IRArrayLiteral, ctx: GoEmitContext) -> Emitted:
    items = ", ".join(emit_expression(item, ctx).text for item in node.elements)
    element = "any"
    if node.value_type.kind is TypeKind.COLLECTION and node.value_type.args:
        element = render_type(node.value_type.args[0])
    return Emitted(f"[]{element}{{{items}}}", _PRIMARY)


def _tuple(node: IRTupleLiteral, ctx: GoEmitContext) -> Emitted:
    ctx.warn(
        "CW0711",
        "Go has no tuple type; the literal becomes a slice of any",
        span=node.span,
        hint="the element types are lost",
    )
    items = ", ".join(emit_expression(item, ctx).text for item in node.elements)
    return Emitted(f"[]any{{{items}}}", _PRIMARY)


def _set(node: IRSetLiteral, ctx: GoEmitContext) -> Emitted:
    element = "any"
    if node.value_type.kind is TypeKind.COLLECTION and node.value_type.args:
        element = render_type(node.value_type.args[0])
    entries = ", ".join(f"{emit_expression(item, ctx).text}: {{}}" for item in node.elements)
    return Emitted(f"map[{element}]struct{{}}{{{entries}}}", _PRIMARY)


def _map(node: IRMapLiteral, ctx: GoEmitContext) -> Emitted:
    key_type, value_type = "any", "any"
    if node.value_type.kind is TypeKind.COLLECTION and len(node.value_type.args) == 2:
        key_type = render_type(node.value_type.args[0])
        value_type = render_type(node.value_type.args[1])
    entries = ", ".join(
        f"{emit_expression(entry.key, ctx).text}: {emit_expression(entry.value, ctx).text}"
        for entry in node.entries
    )
    return Emitted(f"map[{key_type}]{value_type}{{{entries}}}", _PRIMARY)


def _template(node: IRTemplateString, ctx: GoEmitContext) -> Emitted:
    ctx.need("fmt")
    pattern: list[str] = []
    arguments: list[str] = []
    for part in node.parts:
        if isinstance(part, IRLiteral) and isinstance(part.value, str):
            pattern.append(part.value.replace("%", "%%"))
        else:
            pattern.append("%v")
            arguments.append(emit_expression(part, ctx).text)
    ctx.warn(
        "CW0712",
        "an f-string becomes fmt.Sprintf with %v holes, which formats some "
        "values differently from Python's str()",
        span=node.span,
    )
    joined = "".join(pattern)
    if not arguments:
        return Emitted(escape_double_quoted(joined))
    return Emitted(f"fmt.Sprintf({escape_double_quoted(joined)}, {', '.join(arguments)})", _PRIMARY)


def _conditional(node: IRConditional, ctx: GoEmitContext) -> Emitted:
    ctx.warn(
        "CW0713",
        "Go has no conditional expression; it becomes an immediately called function",
        span=node.span,
        hint="rewrite as an if statement for idiomatic Go",
    )
    test = emit_expression(node.test, ctx).text
    then_value = emit_expression(node.then_value, ctx).text
    else_value = emit_expression(node.else_value, ctx).text
    return Emitted(
        f"func() any {{ if {test} {{ return {then_value} }}; return {else_value} }}()",
        _PRIMARY,
    )


def emit_params(params: list[Any], ctx: GoEmitContext) -> str:
    """Render a parameter list.  Go has no defaults, so they are reported."""
    rendered: list[str] = []
    for param in params:
        name = GO_POLICY.identifier(param.name)
        if param.style in ("var_positional", "var_keyword"):
            ctx.warn(
                "CW0714",
                f"parameter {param.name!r} is variadic or keyword-collecting; "
                "Go supports only a trailing variadic slice",
                span=param.span,
            )
            rendered.append(f"{name} ...any")
            continue
        if param.default is not None:
            ctx.warn(
                "CW0715",
                f"parameter {param.name!r} has a default value, which Go does not support",
                span=param.span,
                hint="callers must pass the value explicitly",
            )
        rendered.append(f"{name} {render_type(param.param_type)}")
    return ", ".join(rendered)


_Handler = Callable[[Any, GoEmitContext], Emitted]

_HANDLERS: dict[type[IRNode], _Handler] = {
    IRLiteral: _literal,
    IRName: _name,
    IRBinary: _binary,
    IRUnary: _unary,
    IRCompare: _compare,
    IRLogical: _logical,
    IRCall: _call,
    IRAttribute: _attribute,
    IRIndex: _index,
    IRSlice: _slice,
    IRArrayLiteral: _array,
    IRTupleLiteral: _tuple,
    IRSetLiteral: _set,
    IRMapLiteral: _map,
    IRTemplateString: _template,
    IRConditional: _conditional,
}
