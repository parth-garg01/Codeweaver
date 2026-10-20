"""Python expressions to IR expressions.

The converter is total: every ``ast`` expression either maps onto an IR node or
becomes an :class:`IRUnsupported` node with a reason.  There is no fall-through
that returns ``None``, because a missing expression would be a silent change in
behaviour rather than a reported gap.
"""

from __future__ import annotations

import ast

from codeweaver.frontends.python.annotations import infer_literal_type
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.ir.base import IRUnsupported
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRAwait,
    IRBinary,
    IRCall,
    IRCompare,
    IRConditional,
    IRExpression,
    IRIndex,
    IRKeyword,
    IRLambda,
    IRLiteral,
    IRLogical,
    IRMapEntry,
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
from codeweaver.ir.types import t_bool, t_dict, t_list, t_set, t_str, t_unknown

__all__ = [
    "BINARY_OPS",
    "COMPARE_OPS",
    "UNARY_OPS",
    "convert_expression",
    "convert_params",
]

BINARY_OPS: dict[type[ast.operator], BinaryOp] = {
    ast.Add: BinaryOp.ADD,
    ast.Sub: BinaryOp.SUB,
    ast.Mult: BinaryOp.MUL,
    ast.Div: BinaryOp.DIV,
    ast.FloorDiv: BinaryOp.FLOOR_DIV,
    ast.Mod: BinaryOp.MOD,
    ast.Pow: BinaryOp.POW,
    ast.BitAnd: BinaryOp.BIT_AND,
    ast.BitOr: BinaryOp.BIT_OR,
    ast.BitXor: BinaryOp.BIT_XOR,
    ast.LShift: BinaryOp.SHIFT_LEFT,
    ast.RShift: BinaryOp.SHIFT_RIGHT,
    ast.MatMult: BinaryOp.MAT_MUL,
}

UNARY_OPS: dict[type[ast.unaryop], UnaryOp] = {
    ast.UAdd: UnaryOp.POS,
    ast.USub: UnaryOp.NEG,
    ast.Not: UnaryOp.NOT,
    ast.Invert: UnaryOp.BIT_NOT,
}

COMPARE_OPS: dict[type[ast.cmpop], CompareOp] = {
    ast.Eq: CompareOp.EQ,
    ast.NotEq: CompareOp.NE,
    ast.Lt: CompareOp.LT,
    ast.LtE: CompareOp.LE,
    ast.Gt: CompareOp.GT,
    ast.GtE: CompareOp.GE,
    ast.Is: CompareOp.IS,
    ast.IsNot: CompareOp.IS_NOT,
    ast.In: CompareOp.IN,
    ast.NotIn: CompareOp.NOT_IN,
}


def convert_expression(node: ast.expr, ctx: ConversionContext) -> IRExpression | IRUnsupported:
    """Convert one Python expression."""
    handler = _HANDLERS.get(type(node))
    if handler is None:
        return ctx.unsupported(node, "expression form has no IR mapping")
    return handler(node, ctx)


def _constant(node: ast.Constant, ctx: ConversionContext) -> IRExpression:
    if node.value is Ellipsis:
        return IRLiteral(
            None,
            source_text="...",
            value_type=t_unknown("Ellipsis"),
            span=ctx.span(node),
            meta={"pythonEllipsis": True},
        )
    return IRLiteral(
        node.value,
        source_text=ctx.source_of(node),
        value_type=infer_literal_type(node.value),
        span=ctx.span(node),
    )


def _name(node: ast.Name, ctx: ConversionContext) -> IRExpression:
    if node.id in ("True", "False"):  # pragma: no cover - parsed as Constant in py3
        return IRLiteral(node.id == "True", value_type=t_bool(), span=ctx.span(node))
    return IRName(node.id, span=ctx.span(node))


def _binop(node: ast.BinOp, ctx: ConversionContext) -> IRExpression | IRUnsupported:
    op = BINARY_OPS.get(type(node.op))
    if op is None:
        return ctx.unsupported(node, f"binary operator {type(node.op).__name__} has no IR mapping")
    return IRBinary(
        op,
        convert_expression(node.left, ctx),
        convert_expression(node.right, ctx),
        span=ctx.span(node),
    )


def _unaryop(node: ast.UnaryOp, ctx: ConversionContext) -> IRExpression | IRUnsupported:
    op = UNARY_OPS.get(type(node.op))
    if op is None:  # pragma: no cover - every unaryop is mapped
        return ctx.unsupported(node, "unary operator has no IR mapping")
    value_type = t_bool() if op is UnaryOp.NOT else t_unknown()
    return IRUnary(
        op, convert_expression(node.operand, ctx), value_type=value_type, span=ctx.span(node)
    )


def _boolop(node: ast.BoolOp, ctx: ConversionContext) -> IRExpression:
    op = LogicalOp.AND if isinstance(node.op, ast.And) else LogicalOp.OR
    return IRLogical(
        op,
        [convert_expression(value, ctx) for value in node.values],
        span=ctx.span(node),
    )


def _compare(node: ast.Compare, ctx: ConversionContext) -> IRExpression | IRUnsupported:
    """Convert a comparison, expanding Python's chained form.

    ``a < b < c`` means ``a < b and b < c`` with ``b`` evaluated once.  The IR
    has no chaining, so the frontend expands it and records that the middle
    operand is now evaluated twice — which is only safe when it has no side
    effects, so an expanded chain over a call is reported.
    """
    operands = [node.left, *node.comparators]
    parts: list[IRExpression] = []
    for index, operator in enumerate(node.ops):
        op = COMPARE_OPS.get(type(operator))
        if op is None:  # pragma: no cover - every cmpop is mapped
            return ctx.unsupported(node, "comparison operator has no IR mapping")
        parts.append(
            IRCompare(
                op,
                convert_expression(operands[index], ctx),
                convert_expression(operands[index + 1], ctx),
                value_type=t_bool(),
                span=ctx.span(node),
            )
        )
    if len(parts) == 1:
        return parts[0]
    repeated = operands[1:-1]
    if any(not isinstance(operand, ast.Name | ast.Constant) for operand in repeated):
        ctx.diagnostics.warning(
            "CW0401",
            "chained comparison expands a non-trivial middle operand, which will "
            "be evaluated twice in the generated code",
            stage="frontend/python",
            span=ctx.span(node),
            hint="assign the middle operand to a variable before comparing",
        )
    return IRLogical(LogicalOp.AND, parts, value_type=t_bool(), span=ctx.span(node))


def _call(node: ast.Call, ctx: ConversionContext) -> IRExpression:
    args: list[IRExpression] = []
    star_args: list[IRExpression] = []
    for argument in node.args:
        if isinstance(argument, ast.Starred):
            star_args.append(convert_expression(argument.value, ctx))
        else:
            args.append(convert_expression(argument, ctx))
    keywords: list[IRKeyword] = []
    star_keywords: list[IRExpression] = []
    for keyword in node.keywords:
        value = convert_expression(keyword.value, ctx)
        if keyword.arg is None:
            star_keywords.append(value)
        else:
            keywords.append(IRKeyword(keyword.arg, value, span=ctx.span(keyword.value)))
    return IRCall(
        convert_expression(node.func, ctx),
        args=args,
        keywords=keywords,
        star_args=star_args,
        star_keywords=star_keywords,
        span=ctx.span(node),
    )


def _attribute(node: ast.Attribute, ctx: ConversionContext) -> IRExpression:
    return IRAttribute(convert_expression(node.value, ctx), node.attr, span=ctx.span(node))


def _subscript(node: ast.Subscript, ctx: ConversionContext) -> IRExpression:
    target = convert_expression(node.value, ctx)
    if isinstance(node.slice, ast.Slice):
        sliced = node.slice
        return IRSlice(
            target,
            lower=convert_expression(sliced.lower, ctx) if sliced.lower else None,
            upper=convert_expression(sliced.upper, ctx) if sliced.upper else None,
            step=convert_expression(sliced.step, ctx) if sliced.step else None,
            span=ctx.span(node),
        )
    return IRIndex(target, convert_expression(node.slice, ctx), span=ctx.span(node))


def _list(node: ast.List, ctx: ConversionContext) -> IRExpression:
    return IRArrayLiteral(_elements(node.elts, ctx), value_type=t_list(), span=ctx.span(node))


def _tuple(node: ast.Tuple, ctx: ConversionContext) -> IRExpression:
    return IRTupleLiteral(_elements(node.elts, ctx), span=ctx.span(node))


def _set(node: ast.Set, ctx: ConversionContext) -> IRExpression:
    return IRSetLiteral(_elements(node.elts, ctx), value_type=t_set(), span=ctx.span(node))


def _dict(node: ast.Dict, ctx: ConversionContext) -> IRExpression:
    entries: list[IRMapEntry] = []
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            # `{**other}`: a spread, not a key/value pair.
            entries.append(
                IRMapEntry(
                    ctx.unsupported(value, "dictionary unpacking inside a literal"),
                    convert_expression(value, ctx),
                    span=ctx.span(value),
                )
            )
            continue
        entries.append(
            IRMapEntry(
                convert_expression(key, ctx),
                convert_expression(value, ctx),
                span=ctx.span(key),
            )
        )
    return IRMapLiteral(entries, value_type=t_dict(), span=ctx.span(node))


def _joined_str(node: ast.JoinedStr, ctx: ConversionContext) -> IRExpression:
    parts: list[IRExpression] = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(IRLiteral(value.value, value_type=t_str(), span=ctx.span(value)))
        elif isinstance(value, ast.FormattedValue):
            parts.append(_formatted_value(value, ctx))
        else:  # pragma: no cover - JoinedStr holds only those two forms
            parts.append(ctx.unsupported(value, "unexpected f-string fragment"))
    return IRTemplateString(parts, value_type=t_str(), span=ctx.span(node))


def _formatted_value(node: ast.FormattedValue, ctx: ConversionContext) -> IRExpression:
    if node.format_spec is not None or node.conversion not in (-1, None):
        return ctx.unsupported(
            node,
            "f-string conversion or format specification has no IR mapping",
            hint="format the value explicitly before interpolating it",
        )
    return convert_expression(node.value, ctx)


def _lambda(node: ast.Lambda, ctx: ConversionContext) -> IRExpression:
    return IRLambda(
        convert_params(node.args, ctx),
        convert_expression(node.body, ctx),
        span=ctx.span(node),
    )


def _ifexp(node: ast.IfExp, ctx: ConversionContext) -> IRExpression:
    return IRConditional(
        convert_expression(node.test, ctx),
        convert_expression(node.body, ctx),
        convert_expression(node.orelse, ctx),
        span=ctx.span(node),
    )


def _await(node: ast.Await, ctx: ConversionContext) -> IRExpression:
    return IRAwait(convert_expression(node.value, ctx), span=ctx.span(node))


def _starred(node: ast.Starred, ctx: ConversionContext) -> IRUnsupported:
    return ctx.unsupported(
        node,
        "spread outside a call or assignment target",
        hint="rewrite the expression without unpacking",
    )


def _comprehension(node: ast.expr, ctx: ConversionContext) -> IRUnsupported:
    kind = type(node).__name__
    return ctx.unsupported(
        node,
        f"{kind} is not lowered by this build",
        hint="rewrite as an explicit loop, or wait for comprehension lowering",
    )


def _yield(node: ast.expr, ctx: ConversionContext) -> IRUnsupported:
    return ctx.unsupported(
        node,
        "generators have no IR mapping",
        hint="return a list, or keep the generator in the source project",
    )


def _named_expr(node: ast.NamedExpr, ctx: ConversionContext) -> IRUnsupported:
    return ctx.unsupported(
        node,
        "assignment expressions have no IR mapping",
        hint="assign to a variable on its own line",
    )


def _elements(nodes: list[ast.expr], ctx: ConversionContext) -> list[IRExpression]:
    converted: list[IRExpression] = []
    for element in nodes:
        if isinstance(element, ast.Starred):
            converted.append(ctx.unsupported(element, "spread inside a collection literal"))
        else:
            converted.append(convert_expression(element, ctx))
    return converted


def convert_params(args: ast.arguments, ctx: ConversionContext) -> list[IRParam]:
    """Convert a parameter list, preserving order, styles and defaults."""
    from codeweaver.frontends.python.annotations import annotation_to_type

    params: list[IRParam] = []
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults))
    defaults.extend(args.defaults)
    for argument, default in zip(positional, defaults, strict=True):
        params.append(
            IRParam(
                argument.arg,
                annotation_to_type(argument.annotation),
                convert_expression(default, ctx) if default is not None else None,
                "positional",
                span=ctx.span(argument),
            )
        )
    if args.vararg is not None:
        params.append(
            IRParam(
                args.vararg.arg,
                annotation_to_type(args.vararg.annotation),
                None,
                "var_positional",
                span=ctx.span(args.vararg),
            )
        )
    for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        params.append(
            IRParam(
                argument.arg,
                annotation_to_type(argument.annotation),
                convert_expression(default, ctx) if default is not None else None,
                "keyword",
                span=ctx.span(argument),
            )
        )
    if args.kwarg is not None:
        params.append(
            IRParam(
                args.kwarg.arg,
                annotation_to_type(args.kwarg.annotation),
                None,
                "var_keyword",
                span=ctx.span(args.kwarg),
            )
        )
    return params


_HANDLERS: dict[type[ast.AST], object] = {
    ast.Constant: _constant,
    ast.Name: _name,
    ast.BinOp: _binop,
    ast.UnaryOp: _unaryop,
    ast.BoolOp: _boolop,
    ast.Compare: _compare,
    ast.Call: _call,
    ast.Attribute: _attribute,
    ast.Subscript: _subscript,
    ast.List: _list,
    ast.Tuple: _tuple,
    ast.Set: _set,
    ast.Dict: _dict,
    ast.JoinedStr: _joined_str,
    ast.FormattedValue: _formatted_value,
    ast.Lambda: _lambda,
    ast.IfExp: _ifexp,
    ast.Await: _await,
    ast.Starred: _starred,
    ast.ListComp: _comprehension,
    ast.SetComp: _comprehension,
    ast.DictComp: _comprehension,
    ast.GeneratorExp: _comprehension,
    ast.Yield: _yield,
    ast.YieldFrom: _yield,
    ast.NamedExpr: _named_expr,
}
