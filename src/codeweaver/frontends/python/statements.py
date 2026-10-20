"""Python statements to IR statements.

Two decisions here carry most of the weight:

* a plain ``x = 1`` becomes an :class:`IRVarDecl` the first time a name is
  bound in a scope and an :class:`IRAssign` afterwards, because targets that
  need ``let``/``const`` cannot recover that distinction later;
* anything without a mapping becomes an :class:`IRUnsupported` statement, so a
  generated file is visibly incomplete rather than quietly wrong.
"""

from __future__ import annotations

import ast

from codeweaver.frontends.python.annotations import annotation_to_type
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import BINARY_OPS, convert_expression
from codeweaver.ir.base import IRNode
from codeweaver.ir.expressions import IRName
from codeweaver.ir.statements import (
    IRAssert,
    IRAssign,
    IRAugAssign,
    IRBlock,
    IRBreak,
    IRCatch,
    IRContinue,
    IRDelete,
    IRExpressionStatement,
    IRForEach,
    IRIf,
    IRNoOp,
    IRReturn,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)
from codeweaver.ir.types import t_unknown

__all__ = ["convert_block", "convert_statement", "convert_statements"]


def convert_block(body: list[ast.stmt], ctx: ConversionContext) -> IRBlock:
    """Convert a statement list into an :class:`IRBlock`."""
    return IRBlock(convert_statements(body, ctx))


def convert_statements(body: list[ast.stmt], ctx: ConversionContext) -> list[IRNode]:
    converted: list[IRNode] = []
    for statement in body:
        converted.append(convert_statement(statement, ctx))
    return converted


def convert_statement(node: ast.stmt, ctx: ConversionContext) -> IRNode:
    handler = _HANDLERS.get(type(node))
    if handler is None:
        return ctx.unsupported(node, "statement form has no IR mapping")
    return handler(node, ctx)


def _expr(node: ast.Expr, ctx: ConversionContext) -> IRNode:
    return IRExpressionStatement(convert_expression(node.value, ctx), span=ctx.span(node))


def _assign(node: ast.Assign, ctx: ConversionContext) -> IRNode:
    if len(node.targets) != 1:
        return ctx.unsupported(
            node,
            "chained assignment to several targets",
            hint="assign to one target per statement",
        )
    target = node.targets[0]
    value = convert_expression(node.value, ctx)
    if isinstance(target, ast.Tuple | ast.List):
        return ctx.unsupported(
            node,
            "destructuring assignment has no IR mapping",
            hint="assign each element on its own line",
        )
    if isinstance(target, ast.Name):
        return _bind_name(target.id, t_unknown(), value, node, ctx)
    converted_target = convert_expression(target, ctx)
    return IRAssign(converted_target, value, span=ctx.span(node))


def _ann_assign(node: ast.AnnAssign, ctx: ConversionContext) -> IRNode:
    declared = annotation_to_type(node.annotation)
    value = convert_expression(node.value, ctx) if node.value is not None else None
    if isinstance(node.target, ast.Name):
        return _bind_name(node.target.id, declared, value, node, ctx, force_declaration=True)
    if value is None:
        return ctx.unsupported(
            node,
            "annotation-only declaration of a non-name target",
            hint="annotate a plain variable instead",
        )
    return IRAssign(convert_expression(node.target, ctx), value, span=ctx.span(node))


def _bind_name(
    name: str,
    declared_type,
    value,
    node: ast.stmt,
    ctx: ConversionContext,
    *,
    force_declaration: bool = False,
) -> IRNode:
    is_new = force_declaration or name not in ctx.bound_names
    ctx.bound_names.add(name)
    if is_new:
        return IRVarDecl(name, declared_type, value, span=ctx.span(node))
    return IRAssign(IRName(name, span=ctx.span(node)), value, span=ctx.span(node))


def _aug_assign(node: ast.AugAssign, ctx: ConversionContext) -> IRNode:
    op = BINARY_OPS.get(type(node.op))
    if op is None:  # pragma: no cover - every operator is mapped
        return ctx.unsupported(node, "augmented operator has no IR mapping")
    return IRAugAssign(
        op,
        convert_expression(node.target, ctx),
        convert_expression(node.value, ctx),
        span=ctx.span(node),
    )


def _return(node: ast.Return, ctx: ConversionContext) -> IRNode:
    value = convert_expression(node.value, ctx) if node.value is not None else None
    return IRReturn(value, span=ctx.span(node))


def _if(node: ast.If, ctx: ConversionContext) -> IRNode:
    return IRIf(
        convert_expression(node.test, ctx),
        convert_block(node.body, ctx),
        convert_block(node.orelse, ctx) if node.orelse else None,
        span=ctx.span(node),
    )


def _while(node: ast.While, ctx: ConversionContext) -> IRNode:
    return IRWhile(
        convert_expression(node.test, ctx),
        convert_block(node.body, ctx),
        convert_block(node.orelse, ctx) if node.orelse else None,
        span=ctx.span(node),
    )


def _for(node: ast.For, ctx: ConversionContext) -> IRNode:
    if isinstance(node.target, ast.Tuple | ast.List):
        return ctx.unsupported(
            node,
            "tuple unpacking in a for target has no IR mapping",
            hint="iterate over the sequence and index the parts inside the loop",
        )
    if isinstance(node.target, ast.Name):
        ctx.bound_names.add(node.target.id)
    return IRForEach(
        convert_expression(node.target, ctx),
        convert_expression(node.iter, ctx),
        convert_block(node.body, ctx),
        convert_block(node.orelse, ctx) if node.orelse else None,
        span=ctx.span(node),
    )


def _async_for(node: ast.AsyncFor, ctx: ConversionContext) -> IRNode:
    return ctx.unsupported(
        node,
        "asynchronous iteration has no IR mapping",
        hint="collect the values first, then iterate",
    )


def _try(node: ast.Try, ctx: ConversionContext) -> IRNode:
    handlers: list[IRCatch] = []
    for handler in node.handlers:
        types: list = []
        if isinstance(handler.type, ast.Tuple):
            types = [convert_expression(element, ctx) for element in handler.type.elts]
        elif handler.type is not None:
            types = [convert_expression(handler.type, ctx)]
        if handler.name:
            ctx.bound_names.add(handler.name)
        handlers.append(
            IRCatch(
                types,
                handler.name or "",
                convert_block(handler.body, ctx),
                span=ctx.span(handler),
            )
        )
    return IRTry(
        convert_block(node.body, ctx),
        handlers,
        convert_block(node.orelse, ctx) if node.orelse else None,
        convert_block(node.finalbody, ctx) if node.finalbody else None,
        span=ctx.span(node),
    )


def _raise(node: ast.Raise, ctx: ConversionContext) -> IRNode:
    return IRThrow(
        convert_expression(node.exc, ctx) if node.exc is not None else None,
        convert_expression(node.cause, ctx) if node.cause is not None else None,
        span=ctx.span(node),
    )


def _assert(node: ast.Assert, ctx: ConversionContext) -> IRNode:
    return IRAssert(
        convert_expression(node.test, ctx),
        convert_expression(node.msg, ctx) if node.msg is not None else None,
        span=ctx.span(node),
    )


def _delete(node: ast.Delete, ctx: ConversionContext) -> IRNode:
    return IRDelete(
        [convert_expression(target, ctx) for target in node.targets], span=ctx.span(node)
    )


def _pass(node: ast.Pass, ctx: ConversionContext) -> IRNode:
    return IRNoOp(span=ctx.span(node))


def _break(node: ast.Break, ctx: ConversionContext) -> IRNode:
    return IRBreak(span=ctx.span(node))


def _continue(node: ast.Continue, ctx: ConversionContext) -> IRNode:
    return IRContinue(span=ctx.span(node))


def _with(node: ast.stmt, ctx: ConversionContext) -> IRNode:
    return ctx.unsupported(
        node,
        "context managers have no IR mapping",
        hint="use an explicit try/finally",
    )


def _scope_declaration(node: ast.stmt, ctx: ConversionContext) -> IRNode:
    return ctx.unsupported(
        node,
        "global and nonlocal declarations have no IR mapping",
        hint="pass the value explicitly instead of rebinding an outer name",
    )


def _match(node: ast.stmt, ctx: ConversionContext) -> IRNode:
    return ctx.unsupported(
        node,
        "structural pattern matching has no IR mapping",
        hint="rewrite as an if/elif chain",
    )


def _nested_definition(node: ast.stmt, ctx: ConversionContext) -> IRNode:
    return ctx.unsupported(
        node,
        f"nested {type(node).__name__} is not lifted by this build",
        hint="move the definition to module level",
    )


def _nested_import(node: ast.stmt, ctx: ConversionContext) -> IRNode:
    return ctx.unsupported(
        node,
        "imports inside a function body are not hoisted by this build",
        hint="move the import to module level",
    )


_HANDLERS: dict[type[ast.AST], object] = {
    ast.Expr: _expr,
    ast.Assign: _assign,
    ast.AnnAssign: _ann_assign,
    ast.AugAssign: _aug_assign,
    ast.Return: _return,
    ast.If: _if,
    ast.While: _while,
    ast.For: _for,
    ast.AsyncFor: _async_for,
    ast.Try: _try,
    ast.Raise: _raise,
    ast.Assert: _assert,
    ast.Delete: _delete,
    ast.Pass: _pass,
    ast.Break: _break,
    ast.Continue: _continue,
    ast.With: _with,
    ast.AsyncWith: _with,
    ast.Global: _scope_declaration,
    ast.Nonlocal: _scope_declaration,
    ast.Match: _match,
    ast.FunctionDef: _nested_definition,
    ast.AsyncFunctionDef: _nested_definition,
    ast.ClassDef: _nested_definition,
    ast.Import: _nested_import,
    ast.ImportFrom: _nested_import,
}
