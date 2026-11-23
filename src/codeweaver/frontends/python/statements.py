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
from dataclasses import replace
from typing import cast

from codeweaver.frontends.python.annotations import annotation_to_type
from codeweaver.frontends.python.context import ConversionContext
from codeweaver.frontends.python.expressions import BINARY_OPS, convert_expression
from codeweaver.ir.base import IRNode
from codeweaver.ir.expressions import IRExpression, IRIndex, IRLiteral, IRName
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
    IRStatement,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)
from codeweaver.ir.types import t_unknown

__all__ = ["DESTRUCTURED", "convert_block", "convert_statement", "convert_statements"]

#: Meta flag on the IRBlock a destructuring assignment lowers to; the block is
#: spliced into the enclosing statement list.
DESTRUCTURED = "pythonDestructured"


def convert_block(body: list[ast.stmt], ctx: ConversionContext) -> IRBlock:
    """Convert a statement list into an :class:`IRBlock`."""
    return IRBlock(convert_statements(body, ctx))


def convert_statements(body: list[ast.stmt], ctx: ConversionContext) -> list[IRNode]:
    converted: list[IRNode] = []
    for statement in body:
        result = convert_statement(statement, ctx)
        if isinstance(result, IRBlock) and result.meta.get(DESTRUCTURED):
            converted.extend(result.statements)
        else:
            converted.append(result)
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
    if isinstance(target, ast.Tuple | ast.List):
        return _destructure(node, target, ctx)
    value = convert_expression(node.value, ctx)
    if isinstance(target, ast.Name):
        return _bind_name(target.id, t_unknown(), value, node, ctx)
    converted_target = convert_expression(target, ctx)
    return IRAssign(converted_target, value, span=ctx.span(node))


def _names_in(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def _destructure(node: ast.Assign, target: ast.Tuple | ast.List, ctx: ConversionContext) -> IRNode:
    """Lower ``a, b = x, y`` to element-wise bindings.

    Python evaluates the whole right-hand side before binding any target, so
    ``a, b = b, a`` swaps.  Each element goes into a temporary first unless no
    target is read on the right-hand side, in which case the bindings are
    direct.  A non-literal right-hand side (``q, r = divmod(a, b)``) is bound
    to one temporary and indexed by constant position.
    """
    targets = target.elts
    if any(isinstance(item, ast.Starred | ast.Tuple | ast.List) for item in targets):
        return ctx.unsupported(
            node,
            "starred or nested destructuring has no IR mapping",
            hint="assign each element on its own line",
        )
    span = ctx.span(node)
    # IRBlock holds statements; _bind_name and IRUnsupported targets are typed
    # as IRNode, and both are valid in a statement position.
    statements: list[IRStatement] = []

    def temp(value) -> IRName:
        ctx.temp_counter += 1
        name = f"cw_t{ctx.temp_counter}"
        statements.append(IRVarDecl(name, t_unknown(), value, span=span))
        return IRName(name, span=span)

    def bind(item: ast.expr, value) -> None:
        if isinstance(item, ast.Name):
            statements.append(cast(IRStatement, _bind_name(item.id, t_unknown(), value, node, ctx)))
        else:
            assigned = cast(IRExpression, convert_expression(item, ctx))
            statements.append(IRAssign(assigned, value, span=ctx.span(item)))

    source = node.value
    if isinstance(source, ast.Tuple | ast.List) and not any(
        isinstance(e, ast.Starred) for e in source.elts
    ):
        if len(source.elts) != len(targets):
            return ctx.unsupported(node, "destructuring with mismatched element counts")
        written = set().union(*(_names_in(item) for item in targets))
        read = set().union(*(_names_in(item) for item in source.elts))
        values = [convert_expression(item, ctx) for item in source.elts]
        if written & read:
            values = [temp(value) for value in values]
        for item, value in zip(targets, values, strict=True):
            bind(item, value)
    else:
        holder = temp(convert_expression(source, ctx))
        for position, item in enumerate(targets):
            index = IRIndex(
                IRName(holder.name, span=span), IRLiteral(position, span=span), span=span
            )
            bind(item, index)
    block = IRBlock(statements, span=span)
    block.meta[DESTRUCTURED] = True
    return block


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
    if declared.is_known and isinstance(value, IRExpression):
        # ``self.items: dict[str, int] = {}`` — the annotation is the only
        # place the element types appear, so it must survive onto the value
        # for field inference (and typed backends) to see it.
        value = replace(value, value_type=declared)
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


def _bind_target(target: ast.expr, value: ast.expr, ctx: ConversionContext) -> list[ast.stmt]:
    """Python statements that bind *target* (possibly nested) from *value*."""
    if not isinstance(target, ast.Tuple | ast.List):
        return [ast.Assign(targets=[target], value=value)]
    if isinstance(value, ast.Tuple) and len(value.elts) == len(target.elts):
        pairs = list(zip(target.elts, value.elts, strict=True))
        return [stmt for item, part in pairs for stmt in _bind_target(item, part, ctx)]
    holder: ast.expr = value
    statements: list[ast.stmt] = []
    if not isinstance(value, ast.Name):
        ctx.temp_counter += 1
        name = f"cw_t{ctx.temp_counter}"
        statements.append(ast.Assign(targets=[ast.Name(name, ast.Store())], value=value))
        holder = ast.Name(name, ast.Load())
    for position, item in enumerate(target.elts):
        part = ast.Subscript(value=holder, slice=ast.Constant(position), ctx=ast.Load())
        statements.extend(_bind_target(item, part, ctx))
    return statements


def _lower_for(node: ast.For, ctx: ConversionContext) -> list[ast.stmt] | None:
    """Rewrite ``for`` loops with a tuple target or over ``enumerate()``.

    ``for i, x in enumerate(xs, s)`` becomes a hidden counter and a loop over
    ``xs``, so no backend needs an enumerate object; a tuple target becomes a
    plain loop variable unpacked at the top of the body.  The counter is
    bumped before the body runs, so ``continue`` cannot skip it.
    """
    iterable = node.iter
    is_enumerate = (
        isinstance(iterable, ast.Call)
        and isinstance(iterable.func, ast.Name)
        and iterable.func.id == "enumerate"
        and iterable.func.id not in ctx.bound_names
        and 1 <= len(iterable.args) <= 2
        and not iterable.keywords
        and not any(isinstance(arg, ast.Starred) for arg in iterable.args)
        and isinstance(node.target, ast.Tuple | ast.List)
        and len(node.target.elts) == 2
    )
    if not is_enumerate and not isinstance(node.target, ast.Tuple | ast.List):
        return None
    if any(isinstance(inner, ast.Starred) for inner in ast.walk(node.target)):
        return None
    ctx.temp_counter += 1
    item = f"cw_e{ctx.temp_counter}"
    prelude: list[ast.stmt] = []
    head: list[ast.stmt] = []
    if is_enumerate:
        call = cast(ast.Call, iterable)
        counter = f"cw_n{ctx.temp_counter}"
        start = call.args[1] if len(call.args) == 2 else ast.Constant(0)
        prelude.append(ast.Assign(targets=[ast.Name(counter, ast.Store())], value=start))
        index_target, element_target = cast(ast.Tuple, node.target).elts
        head += _bind_target(index_target, ast.Name(counter, ast.Load()), ctx)
        head.append(
            ast.AugAssign(
                target=ast.Name(counter, ast.Store()), op=ast.Add(), value=ast.Constant(1)
            )
        )
        iterable = call.args[0]
        if isinstance(element_target, ast.Name):
            item = element_target.id
        else:
            head += _bind_target(element_target, ast.Name(item, ast.Load()), ctx)
    else:
        head += _bind_target(node.target, ast.Name(item, ast.Load()), ctx)
    loop = ast.For(
        target=ast.Name(item, ast.Store()),
        iter=iterable,
        body=[*head, *node.body],
        orelse=node.orelse,
        type_comment=None,
    )
    rewritten = [*prelude, loop]
    for statement in rewritten:
        ast.copy_location(statement, node)
        for inner in ast.walk(statement):
            if not hasattr(inner, "lineno") and isinstance(inner, ast.expr | ast.stmt):
                ast.copy_location(inner, node)
    return rewritten


def _for(node: ast.For, ctx: ConversionContext) -> IRNode:
    lowered = _lower_for(node, ctx)
    if lowered is not None:
        span = ctx.span(node)
        statements = [cast(IRStatement, convert_statement(stmt, ctx)) for stmt in lowered]
        if len(statements) == 1:
            return statements[0]
        block = IRBlock(statements, span=span)
        block.meta[DESTRUCTURED] = True
        return block
    if isinstance(node.target, ast.Tuple | ast.List):
        return ctx.unsupported(
            node,
            "starred unpacking in a for target has no IR mapping",
            hint="iterate over the sequence and slice the parts inside the loop",
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
