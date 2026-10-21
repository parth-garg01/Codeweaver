"""IR expressions to TypeScript expressions.

Precedence is tracked explicitly rather than by parenthesising everything: the
generated file is meant to be read and reviewed next to the original, and
``a + b * c`` wrapped as ``(a) + ((b) * (c))`` is much harder to check than the
expression it came from.

Where Python and TypeScript genuinely differ, the backend emits the honest
form and says so.  ``a // b`` becomes ``Math.floor(a / b)``; ``x in xs``
becomes an ``includes`` call with a warning when the container's type is
unknown, because the right answer depends on whether it is an array or an
object.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from codeweaver.backends.typescript.builtins import map_builtin_call
from codeweaver.backends.typescript.context import EmitContext
from codeweaver.backends.typescript.naming import (
    escape_string,
    safe_identifier,
    safe_property,
)
from codeweaver.ir.base import CONSTRUCTS_CLASS, IRNode, IRUnsupported
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRAwait,
    IRBinary,
    IRCall,
    IRCompare,
    IRComprehension,
    IRConditional,
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

__all__ = ["Emitted", "emit_expression", "emit_params"]

#: JavaScript operator precedence, higher binds tighter.
_PRIMARY = 18
_MEMBER = 17
_UNARY = 14
_EXPONENT = 13
_MULTIPLICATIVE = 12
_ADDITIVE = 11
_SHIFT = 10
_RELATIONAL = 9
_EQUALITY = 8
_BIT_AND = 7
_BIT_XOR = 6
_BIT_OR = 5
_LOGICAL_AND = 4
_LOGICAL_OR = 3
_CONDITIONAL = 2
_ARROW = 1

_BINARY_SPELLING: dict[BinaryOp, tuple[str, int]] = {
    BinaryOp.ADD: ("+", _ADDITIVE),
    BinaryOp.SUB: ("-", _ADDITIVE),
    BinaryOp.MUL: ("*", _MULTIPLICATIVE),
    BinaryOp.DIV: ("/", _MULTIPLICATIVE),
    BinaryOp.MOD: ("%", _MULTIPLICATIVE),
    BinaryOp.POW: ("**", _EXPONENT),
    BinaryOp.BIT_AND: ("&", _BIT_AND),
    BinaryOp.BIT_OR: ("|", _BIT_OR),
    BinaryOp.BIT_XOR: ("^", _BIT_XOR),
    BinaryOp.SHIFT_LEFT: ("<<", _SHIFT),
    BinaryOp.SHIFT_RIGHT: (">>", _SHIFT),
}

_COMPARE_SPELLING: dict[CompareOp, tuple[str, int]] = {
    CompareOp.EQ: ("===", _EQUALITY),
    CompareOp.NE: ("!==", _EQUALITY),
    CompareOp.LT: ("<", _RELATIONAL),
    CompareOp.LE: ("<=", _RELATIONAL),
    CompareOp.GT: (">", _RELATIONAL),
    CompareOp.GE: (">=", _RELATIONAL),
    CompareOp.IS: ("===", _EQUALITY),
    CompareOp.IS_NOT: ("!==", _EQUALITY),
}

_UNARY_SPELLING: dict[UnaryOp, str] = {
    UnaryOp.NEG: "-",
    UnaryOp.POS: "+",
    UnaryOp.NOT: "!",
    UnaryOp.BIT_NOT: "~",
}


@dataclass(frozen=True, slots=True)
class Emitted:
    """A rendered expression and the precedence of its outermost operator."""

    text: str
    precedence: int = _PRIMARY

    def at_least(self, needed: int) -> str:
        """Render, parenthesising only when the operator would re-associate."""
        return f"({self.text})" if self.precedence < needed else self.text

    def __str__(self) -> str:
        return self.text


#: Signature every expression emitter shares.
_Handler = Callable[[Any, EmitContext], "Emitted"]


def emit_expression(node: IRNode | None, ctx: EmitContext) -> Emitted:
    """Render an IR expression as TypeScript."""
    if node is None:
        return Emitted("undefined")
    if isinstance(node, IRUnsupported):
        return Emitted(f"{ctx.record_gap(node)} undefined")
    handler = _HANDLERS.get(type(node))
    if handler is None:
        ctx.warn(
            "CW0601",
            f"no TypeScript mapping for IR node {node.kind!r}",
            span=node.span,
            hint="the expression is emitted as a marker",
        )
        return Emitted(f"/* unmapped {node.kind} */ undefined")
    return handler(node, ctx)


def _literal(node: IRLiteral, ctx: EmitContext) -> Emitted:
    value = node.value
    if node.meta.get("pythonEllipsis"):
        return Emitted("undefined")
    if value is None:
        return Emitted("null")
    if isinstance(value, bool):
        return Emitted("true" if value else "false")
    if isinstance(value, int | float):
        return Emitted(node.source_text or repr(value))
    if isinstance(value, str):
        return Emitted(escape_string(value))
    if isinstance(value, bytes):
        joined = ", ".join(str(byte) for byte in value)
        return Emitted(f"new Uint8Array([{joined}])", _MEMBER)
    ctx.warn(
        "CW0602",
        f"literal of type {type(value).__name__} has no TypeScript spelling",
        span=node.span,
    )
    return Emitted("undefined")


def _name(node: IRName, ctx: EmitContext) -> Emitted:
    if ctx.receiver and node.name == ctx.receiver:
        return Emitted("this")
    return Emitted(safe_identifier(node.name))


def _is_super(node: IRNode) -> bool:
    """True for a bare ``super()`` call, which TypeScript spells ``super``."""
    return (
        isinstance(node, IRCall)
        and isinstance(node.callee, IRName)
        and node.callee.name == "super"
        and not node.args
        and not node.keywords
    )


def _binary(node: IRBinary, ctx: EmitContext) -> Emitted:
    if node.op is BinaryOp.FLOOR_DIV:
        left = emit_expression(node.left, ctx).at_least(_MULTIPLICATIVE)
        right = emit_expression(node.right, ctx).at_least(_MULTIPLICATIVE + 1)
        return Emitted(f"Math.floor({left} / {right})", _MEMBER)
    if node.op is BinaryOp.MAT_MUL:
        ctx.warn(
            "CW0603",
            "matrix multiplication has no TypeScript operator",
            span=node.span,
            hint="implement the operation explicitly",
        )
        return Emitted("undefined")
    spelling, precedence = _BINARY_SPELLING[node.op]
    if node.op is BinaryOp.POW:
        # ** is right-associative in both languages.
        left = emit_expression(node.left, ctx).at_least(precedence + 1)
        right = emit_expression(node.right, ctx).at_least(precedence)
    else:
        left = emit_expression(node.left, ctx).at_least(precedence)
        right = emit_expression(node.right, ctx).at_least(precedence + 1)
    return Emitted(f"{left} {spelling} {right}", precedence)


def _unary(node: IRUnary, ctx: EmitContext) -> Emitted:
    spelling = _UNARY_SPELLING[node.op]
    operand = emit_expression(node.operand, ctx).at_least(_UNARY)
    return Emitted(f"{spelling}{operand}", _UNARY)


def _compare(node: IRCompare, ctx: EmitContext) -> Emitted:
    if node.op in (CompareOp.IN, CompareOp.NOT_IN):
        return _membership(node, ctx)
    spelling, precedence = _COMPARE_SPELLING[node.op]
    left = emit_expression(node.left, ctx).at_least(precedence)
    right = emit_expression(node.right, ctx).at_least(precedence + 1)
    return Emitted(f"{left} {spelling} {right}", precedence)


def _membership(node: IRCompare, ctx: EmitContext) -> Emitted:
    container = node.right
    container_type = getattr(container, "value_type", None)
    rendered_container = emit_expression(container, ctx).at_least(_MEMBER)
    needle = emit_expression(node.left, ctx)
    name = container_type.name if container_type else ""
    if name == "dict":
        test = f"Object.hasOwn({rendered_container}, {needle.text} as PropertyKey)"
    elif name == "set":
        test = f"{rendered_container}.has({needle.text})"
    else:
        if name not in ("list", "str"):
            ctx.warn(
                "CW0604",
                "membership test assumes an array or string container",
                span=node.span,
                hint="annotate the container so the check can be generated precisely",
            )
        test = f"{rendered_container}.includes({needle.text})"
    if node.op is CompareOp.NOT_IN:
        return Emitted(f"!{Emitted(test, _MEMBER).at_least(_UNARY)}", _UNARY)
    return Emitted(test, _MEMBER)


def _logical(node: IRLogical, ctx: EmitContext) -> Emitted:
    spelling, precedence = ("&&", _LOGICAL_AND) if node.op is LogicalOp.AND else ("||", _LOGICAL_OR)
    parts = [emit_expression(operand, ctx).at_least(precedence + 1) for operand in node.operands]
    if not parts:
        return Emitted("true" if node.op is LogicalOp.AND else "false")
    if len(parts) == 1:
        return Emitted(parts[0], precedence)
    return Emitted(f" {spelling} ".join(parts), precedence)


def _call(node: IRCall, ctx: EmitContext) -> Emitted:
    # `super().__init__(a)` is `super(a)` in TypeScript, not a method call.
    if (
        isinstance(node.callee, IRAttribute)
        and node.callee.name == "__init__"
        and _is_super(node.callee.target)
    ):
        super_arguments = ", ".join(emit_expression(item, ctx).text for item in node.args)
        return Emitted(f"super({super_arguments})", _MEMBER)
    arguments = [emit_expression(argument, ctx).text for argument in node.args]
    builtin = map_builtin_call(node, arguments, ctx)
    if builtin is not None:
        return Emitted(builtin, _MEMBER)
    callee = emit_expression(node.callee, ctx).at_least(_MEMBER)
    if node.meta.get(CONSTRUCTS_CLASS):
        # Python calls a class to construct it; TypeScript needs `new`.
        callee = f"new {callee}"
    arguments.extend(f"...{emit_expression(item, ctx).at_least(_UNARY)}" for item in node.star_args)
    if node.keywords:
        entries = ", ".join(
            f"{safe_property(keyword.name)}: {emit_expression(keyword.value, ctx).text}"
            for keyword in node.keywords
        )
        arguments.append(f"{{ {entries} }}")
        ctx.warn(
            "CW0605",
            "keyword arguments are passed as a trailing options object",
            span=node.span,
            hint="the called function must accept an options object",
        )
    for item in node.star_keywords:
        arguments.append(f"...{emit_expression(item, ctx).at_least(_UNARY)}")
        ctx.warn(
            "CW0606",
            "double-star keyword spread is emitted as an object spread",
            span=node.span,
        )
    return Emitted(f"{callee}({', '.join(arguments)})", _MEMBER)


def _dotted_chain(node: IRNode) -> list[str] | None:
    """The dotted name an expression spells, or ``None`` if it is not one.

    ``pkg.util.helper`` becomes ``["pkg", "util", "helper"]``; anything with a
    call, an index or a non-name root returns ``None``.
    """
    parts: list[str] = []
    current = node
    while isinstance(current, IRAttribute):
        parts.append(current.name)
        current = current.target
    if not isinstance(current, IRName):
        return None
    parts.append(current.name)
    parts.reverse()
    return parts


def _namespace_reference(node: IRAttribute, ctx: EmitContext) -> str | None:
    """Rewrite a reference that reaches through a namespace import.

    ``import pkg.util`` binds one name in the generated module, so
    ``pkg.util.helper`` has to become ``<binding>.helper``.  Emitting the
    original chain produced a reference to ``pkg``, which was never imported.

    The longest matching dotted prefix wins, so ``a.b`` is preferred over ``a``
    when both were imported.
    """
    if not ctx.namespace_imports:
        return None
    parts = _dotted_chain(node)
    if parts is None or len(parts) < 2:
        return None
    for length in range(len(parts) - 1, 0, -1):
        prefix = ".".join(parts[:length])
        binding = ctx.namespace_imports.get(prefix)
        if binding is None:
            continue
        rest = parts[length:]
        rendered = safe_identifier(binding)
        for name in rest:
            rendered = (
                f"{rendered}.{name}"
                if name.isidentifier() and safe_property(name) == name
                else f"{rendered}[{escape_string(name)}]"
            )
        return rendered
    return None


def _attribute(node: IRAttribute, ctx: EmitContext) -> Emitted:
    rewritten = _namespace_reference(node, ctx)
    if rewritten is not None:
        return Emitted(rewritten, _MEMBER)
    if _is_super(node.target):
        target = "super"
    else:
        target = emit_expression(node.target, ctx).at_least(_MEMBER)
    name = node.name
    if name.isidentifier() and safe_property(name) == name:
        return Emitted(f"{target}.{name}", _MEMBER)
    return Emitted(f"{target}[{escape_string(name)}]", _MEMBER)


def _index(node: IRIndex, ctx: EmitContext) -> Emitted:
    target = emit_expression(node.target, ctx).at_least(_MEMBER)
    index = emit_expression(node.index, ctx)
    return Emitted(f"{target}[{index.text}]", _MEMBER)


def _slice(node: IRSlice, ctx: EmitContext) -> Emitted:
    target = emit_expression(node.target, ctx).at_least(_MEMBER)
    if node.step is not None:
        ctx.warn(
            "CW0607",
            "sliced step has no direct TypeScript equivalent",
            span=node.span,
            hint="use an explicit loop for stepped slices",
        )
        return Emitted(f"/* stepped slice unsupported */ {target}", _MEMBER)
    lower = emit_expression(node.lower, ctx).text if node.lower is not None else "0"
    if node.upper is None:
        return Emitted(f"{target}.slice({lower})", _MEMBER)
    upper = emit_expression(node.upper, ctx).text
    return Emitted(f"{target}.slice({lower}, {upper})", _MEMBER)


def _array(node: IRArrayLiteral, ctx: EmitContext) -> Emitted:
    items = ", ".join(emit_expression(element, ctx).text for element in node.elements)
    return Emitted(f"[{items}]")


def _tuple(node: IRTupleLiteral, ctx: EmitContext) -> Emitted:
    items = ", ".join(emit_expression(element, ctx).text for element in node.elements)
    return Emitted(f"[{items}]")


def _set(node: IRSetLiteral, ctx: EmitContext) -> Emitted:
    items = ", ".join(emit_expression(element, ctx).text for element in node.elements)
    return Emitted(f"new Set([{items}])", _MEMBER)


def _map(node: IRMapLiteral, ctx: EmitContext) -> Emitted:
    if not node.entries:
        return Emitted("{}")
    parts: list[str] = []
    for entry in node.entries:
        key = entry.key
        if isinstance(key, IRLiteral) and isinstance(key.value, str):
            rendered_key = safe_property(key.value)
        else:
            # A computed key keeps its expression, which also covers the
            # unsupported-marker case without losing the record of the gap.
            rendered_key = f"[{emit_expression(key, ctx).text}]"
        parts.append(f"{rendered_key}: {emit_expression(entry.value, ctx).text}")
    return Emitted("{ " + ", ".join(parts) + " }")


def _template(node: IRTemplateString, ctx: EmitContext) -> Emitted:
    pieces: list[str] = []
    for part in node.parts:
        if isinstance(part, IRLiteral) and isinstance(part.value, str):
            pieces.append(
                part.value.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
            )
        else:
            pieces.append(f"${{{emit_expression(part, ctx).text}}}")
    return Emitted("`" + "".join(pieces) + "`")


def _lambda(node: IRLambda, ctx: EmitContext) -> Emitted:
    params = emit_params(node.params, ctx)
    body = emit_expression(node.body, ctx).at_least(_ARROW + 1)
    return Emitted(f"({params}) => {body}", _ARROW)


def _conditional(node: IRConditional, ctx: EmitContext) -> Emitted:
    test = emit_expression(node.test, ctx).at_least(_CONDITIONAL + 1)
    then_value = emit_expression(node.then_value, ctx).at_least(_CONDITIONAL)
    else_value = emit_expression(node.else_value, ctx).at_least(_CONDITIONAL)
    return Emitted(f"{test} ? {then_value} : {else_value}", _CONDITIONAL)


def _await(node: IRAwait, ctx: EmitContext) -> Emitted:
    operand = emit_expression(node.operand, ctx).at_least(_UNARY)
    return Emitted(f"await {operand}", _UNARY)


def _comprehension(node: IRComprehension, ctx: EmitContext) -> Emitted:
    """Generate a comprehension as an array method chain.

    ``[f(x) for x in xs if p(x)]`` becomes ``xs.filter((x) => p(x)).map((x) =>
    f(x))``.  The callback parameters are left unannotated so that TypeScript's
    contextual typing infers them from the container, which gives a better type
    than the ``unknown`` the IR would otherwise supply.

    One difference is reported rather than hidden: the chain allocates an
    intermediate array for the filter step, so a comprehension over a very
    large sequence does more work than the original.
    """
    variable = safe_identifier(node.target)
    chain = emit_expression(node.iterable, ctx).at_least(_MEMBER)
    if node.condition is not None:
        condition = emit_expression(node.condition, ctx).text
        chain = f"{chain}.filter(({variable}) => {condition})"
    if node.form == "dict":
        key = emit_expression(node.key, ctx).text if node.key is not None else "undefined"
        value = emit_expression(node.element, ctx).text
        pairs = f"{chain}.map(({variable}) => [{key}, {value}])"
        return Emitted(f"Object.fromEntries({pairs})", _MEMBER)
    element = emit_expression(node.element, ctx)
    identity = isinstance(node.element, IRName) and node.element.name == node.target
    if identity and node.condition is None:
        mapped = f"[...{chain}]"
    elif identity:
        mapped = chain
    else:
        mapped = f"{chain}.map(({variable}) => {element.text})"
    if node.form == "set":
        return Emitted(f"new Set({mapped})", _MEMBER)
    return Emitted(mapped, _MEMBER)


def emit_params(params: list[IRParam], ctx: EmitContext) -> str:
    """Render a parameter list, including types and defaults."""
    from codeweaver.backends.typescript.types import render_type

    rendered: list[str] = []
    for param in params:
        name = safe_identifier(param.name)
        if param.style == "var_positional":
            rendered.append(f"...{name}: unknown[]")
            continue
        if param.style == "var_keyword":
            rendered.append(f"{name}: Record<string, unknown> = {{}}")
            ctx.warn(
                "CW0608",
                f"**{param.name} is emitted as a trailing options object",
                span=param.span,
            )
            continue
        annotation = render_type(param.param_type)
        if param.default is not None:
            default = emit_expression(param.default, ctx).text
            rendered.append(f"{name}: {annotation} = {default}")
        else:
            rendered.append(f"{name}: {annotation}")
    return ", ".join(rendered)


_HANDLERS: dict[type[IRNode], _Handler] = {
    IRLiteral: _literal,
    IRName: _name,
    IRBinary: _binary,
    IRUnary: _unary,
    IRCompare: _compare,
    IRComprehension: _comprehension,
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
    IRLambda: _lambda,
    IRConditional: _conditional,
    IRAwait: _await,
}
