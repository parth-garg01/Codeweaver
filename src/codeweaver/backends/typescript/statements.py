"""IR statements to TypeScript statements.

Three places where the languages differ are handled explicitly rather than
approximated, and each one reports what it did:

* ``except A: ... except B: ...`` becomes a single ``catch`` with an
  ``instanceof`` chain that re-throws anything unmatched, because JavaScript
  has no typed catch clauses;
* ``try/else`` moves into the ``try`` block, which means an exception raised in
  the else clause becomes catchable — reported as a behaviour change;
* ``while/else`` and ``for/else`` have no equivalent and are reported rather
  than dropped.
"""

from __future__ import annotations

from codeweaver.backends.typescript.context import EmitContext
from codeweaver.backends.typescript.expressions import emit_expression
from codeweaver.backends.typescript.naming import escape_string, safe_identifier
from codeweaver.backends.typescript.types import render_type
from codeweaver.backends.typescript.writer import CodeWriter
from codeweaver.ir.base import IRNode, IRUnsupported
from codeweaver.ir.expressions import IRAttribute, IRIndex, IRName
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.statements import (
    IRAssert,
    IRAssign,
    IRAugAssign,
    IRBlock,
    IRBreak,
    IRCatch,
    IRComment,
    IRContinue,
    IRDelete,
    IRExpressionStatement,
    IRForEach,
    IRForRange,
    IRIf,
    IRNoOp,
    IRReturn,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)
from codeweaver.ir.types import TypeKind

__all__ = ["emit_block", "emit_statement"]

_AUG_SPELLING: dict[BinaryOp, str] = {
    BinaryOp.ADD: "+=",
    BinaryOp.SUB: "-=",
    BinaryOp.MUL: "*=",
    BinaryOp.DIV: "/=",
    BinaryOp.MOD: "%=",
    BinaryOp.POW: "**=",
    BinaryOp.BIT_AND: "&=",
    BinaryOp.BIT_OR: "|=",
    BinaryOp.BIT_XOR: "^=",
    BinaryOp.SHIFT_LEFT: "<<=",
    BinaryOp.SHIFT_RIGHT: ">>=",
}

#: Name bound by a generated catch clause.
_CAUGHT = "error"


def emit_block(block: IRBlock | None, writer: CodeWriter, ctx: EmitContext) -> None:
    """Write the statements of *block* at the writer's current indentation."""
    if block is None:
        return
    for statement in block.statements:
        emit_statement(statement, writer, ctx)


def emit_statement(node: IRNode, writer: CodeWriter, ctx: EmitContext) -> None:
    """Write one statement."""
    if isinstance(node, IRUnsupported):
        writer.line(ctx.record_gap(node), node.span)
        return
    handler = _HANDLERS.get(type(node))
    if handler is None:
        ctx.warn(
            "CW0620",
            f"no TypeScript mapping for IR statement {node.kind!r}",
            span=node.span,
        )
        writer.line(f"// unmapped statement: {node.kind}", node.span)
        return
    handler(node, writer, ctx)


def _expression_statement(
    node: IRExpressionStatement, writer: CodeWriter, ctx: EmitContext
) -> None:
    writer.line(f"{emit_expression(node.expression, ctx).text};", node.span)


def _var_decl(node: IRVarDecl, writer: CodeWriter, ctx: EmitContext) -> None:
    keyword = "const" if node.is_const else "let"
    name = safe_identifier(node.name)
    annotation = (
        f": {render_type(node.var_type)}" if node.var_type.kind is not TypeKind.UNKNOWN else ""
    )
    if node.value is None:
        # `let x: T;` is valid; `const x;` is not, so an uninitialised constant
        # is demoted and reported rather than emitted as invalid source.
        if node.is_const:
            ctx.warn(
                "CW0621",
                f"constant {node.name!r} has no initialiser and is emitted as 'let'",
                span=node.span,
            )
        writer.line(f"let {name}{annotation};", node.span)
        return
    value = emit_expression(node.value, ctx).text
    writer.line(f"{keyword} {name}{annotation} = {value};", node.span)


def _assign(node: IRAssign, writer: CodeWriter, ctx: EmitContext) -> None:
    target = emit_expression(node.target, ctx).text
    value = emit_expression(node.value, ctx).text
    writer.line(f"{target} = {value};", node.span)


def _aug_assign(node: IRAugAssign, writer: CodeWriter, ctx: EmitContext) -> None:
    target = emit_expression(node.target, ctx).text
    value = emit_expression(node.value, ctx)
    if node.op is BinaryOp.FLOOR_DIV:
        writer.line(f"{target} = Math.floor({target} / {value.at_least(13)});", node.span)
        return
    spelling = _AUG_SPELLING.get(node.op)
    if spelling is None:
        ctx.warn(
            "CW0622",
            f"augmented operator {node.op.value!r} has no TypeScript spelling",
            span=node.span,
        )
        writer.line(f"// unsupported augmented assignment: {node.op.value}", node.span)
        return
    writer.line(f"{target} {spelling} {value.text};", node.span)


def _return(node: IRReturn, writer: CodeWriter, ctx: EmitContext) -> None:
    if node.value is None:
        writer.line("return;", node.span)
        return
    writer.line(f"return {emit_expression(node.value, ctx).text};", node.span)


def _if(node: IRIf, writer: CodeWriter, ctx: EmitContext) -> None:
    test = emit_expression(node.test, ctx).text
    writer.line(f"if ({test}) {{", node.span)
    writer.indent()
    emit_block(node.then_body, writer, ctx)
    writer.dedent()
    else_body = node.else_body
    while else_body is not None:
        # `else: if ...` with nothing else in the block is `else if`, which
        # keeps the generated nesting as shallow as the original.
        if len(else_body.statements) == 1 and isinstance(else_body.statements[0], IRIf):
            inner = else_body.statements[0]
            inner_test = emit_expression(inner.test, ctx).text
            writer.line(f"}} else if ({inner_test}) {{", inner.span)
            writer.indent()
            emit_block(inner.then_body, writer, ctx)
            writer.dedent()
            else_body = inner.else_body
            continue
        writer.line("} else {")
        writer.indent()
        emit_block(else_body, writer, ctx)
        writer.dedent()
        break
    writer.line("}")


def _while(node: IRWhile, writer: CodeWriter, ctx: EmitContext) -> None:
    _report_loop_else(node.else_body, ctx, node, "while")
    writer.line(f"while ({emit_expression(node.test, ctx).text}) {{", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _for_each(node: IRForEach, writer: CodeWriter, ctx: EmitContext) -> None:
    _report_loop_else(node.else_body, ctx, node, "for")
    target = emit_expression(node.target, ctx).text
    iterable = emit_expression(node.iterable, ctx).at_least(17)
    writer.line(f"for (const {target} of {iterable}) {{", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _for_range(node: IRForRange, writer: CodeWriter, ctx: EmitContext) -> None:
    name = safe_identifier(node.variable)
    start = emit_expression(node.start, ctx).text
    stop = emit_expression(node.stop, ctx).text
    step = emit_expression(node.step, ctx).text if node.step is not None else "1"
    if step == "1":
        header = f"for (let {name} = {start}; {name} < {stop}; {name} += 1)"
    else:
        # A negative step counts down, and the comparison has to flip with it.
        comparison = f"({step} > 0 ? {name} < {stop} : {name} > {stop})"
        header = f"for (let {name} = {start}; {comparison}; {name} += {step})"
    writer.line(f"{header} {{", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _report_loop_else(
    else_body: IRBlock | None, ctx: EmitContext, node: IRNode, keyword: str
) -> None:
    if else_body is not None and not else_body.is_empty:
        ctx.warn(
            "CW0623",
            f"{keyword}/else has no TypeScript equivalent and its body is not emitted",
            span=node.span,
            hint="move the else body after the loop and guard it with a flag",
        )


def _break(node: IRBreak, writer: CodeWriter, ctx: EmitContext) -> None:  # noqa: ARG001
    writer.line("break;", node.span)


def _continue(node: IRContinue, writer: CodeWriter, ctx: EmitContext) -> None:  # noqa: ARG001
    writer.line("continue;", node.span)


def _noop(node: IRNoOp, writer: CodeWriter, ctx: EmitContext) -> None:  # noqa: ARG001
    writer.line("// pass", node.span)


def _throw(node: IRThrow, writer: CodeWriter, ctx: EmitContext) -> None:
    if node.value is None:
        ctx.warn(
            "CW0624",
            "bare raise has no TypeScript equivalent outside a catch binding",
            span=node.span,
            hint=f"re-throw the caught value explicitly, e.g. throw {_CAUGHT};",
        )
        writer.line(f"throw {_CAUGHT};", node.span)
        return
    if node.cause is not None:
        ctx.warn(
            "CW0625",
            "exception chaining ('raise ... from ...') is emitted via the cause option",
            span=node.span,
        )
        value = emit_expression(node.value, ctx).text
        cause = emit_expression(node.cause, ctx).text
        writer.line(f"throw Object.assign({value}, {{ cause: {cause} }});", node.span)
        return
    writer.line(f"throw {emit_expression(node.value, ctx).text};", node.span)


def _try(node: IRTry, writer: CodeWriter, ctx: EmitContext) -> None:
    writer.line("try {", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    if node.else_body is not None and not node.else_body.is_empty:
        ctx.warn(
            "CW0626",
            "try/else body is emitted inside the try block, so exceptions it "
            "raises become catchable",
            span=node.span,
            hint="move the else body after the try statement if that matters",
        )
        writer.line("// try/else body")
        emit_block(node.else_body, writer, ctx)
    writer.dedent()
    if node.handlers:
        writer.line(f"}} catch ({_CAUGHT}) {{")
        writer.indent()
        _emit_handlers(node.handlers, writer, ctx)
        writer.dedent()
    if node.finally_body is not None and not node.finally_body.is_empty:
        writer.line("} finally {")
        writer.indent()
        emit_block(node.finally_body, writer, ctx)
        writer.dedent()
    writer.line("}")


def _emit_handlers(handlers: list[IRCatch], writer: CodeWriter, ctx: EmitContext) -> None:
    typed = [handler for handler in handlers if handler.exception_types]
    catch_all = next((handler for handler in handlers if not handler.exception_types), None)
    for position, handler in enumerate(typed):
        tests = " || ".join(
            f"{_CAUGHT} instanceof {emit_expression(item, ctx).text}"
            for item in handler.exception_types
        )
        keyword = "if" if position == 0 else "} else if"
        writer.line(f"{keyword} ({tests}) {{")
        writer.indent()
        _bind_caught(handler, writer, ctx)
        emit_block(handler.body, writer, ctx)
        writer.dedent()
    if catch_all is not None:
        if typed:
            writer.line("} else {")
            writer.indent()
        _bind_caught(catch_all, writer, ctx)
        emit_block(catch_all.body, writer, ctx)
        if typed:
            writer.dedent()
            writer.line("}")
    elif typed:
        # Nothing matched: re-throw so the exception is not swallowed, which is
        # what Python would do.
        writer.line("} else {")
        writer.indent()
        writer.line(f"throw {_CAUGHT};")
        writer.dedent()
        writer.line("}")


def _bind_caught(handler: IRCatch, writer: CodeWriter, ctx: EmitContext) -> None:
    if handler.name and handler.name != _CAUGHT:
        writer.line(f"const {safe_identifier(handler.name)} = {_CAUGHT};")
    elif not handler.name:
        ctx.warn(
            "CW0627",
            f"handler does not name the exception; it is available as {_CAUGHT!r}",
            span=handler.span,
        )


def _delete(node: IRDelete, writer: CodeWriter, ctx: EmitContext) -> None:
    for target in node.targets:
        if isinstance(target, IRIndex | IRAttribute):
            writer.line(f"delete {emit_expression(target, ctx).text};", node.span)
            continue
        name = target.name if isinstance(target, IRName) else "<expression>"
        ctx.warn(
            "CW0628",
            f"deleting the binding {name!r} has no TypeScript equivalent",
            span=node.span,
            hint="assign undefined, or restructure so the binding goes out of scope",
        )
        writer.line(f"// unsupported: del {name}", node.span)


def _assert(node: IRAssert, writer: CodeWriter, ctx: EmitContext) -> None:
    test = emit_expression(node.test, ctx)
    message = (
        emit_expression(node.message, ctx).text
        if node.message is not None
        else escape_string("assertion failed")
    )
    writer.line(f"if (!{test.at_least(14)}) {{", node.span)
    writer.indent()
    writer.line(f"throw new Error({message});")
    writer.dedent()
    writer.line("}")


def _comment(node: IRComment, writer: CodeWriter, ctx: EmitContext) -> None:  # noqa: ARG001
    for line in node.text.split("\n"):
        writer.line(f"// {line}".rstrip(), node.span)


_HANDLERS: dict[type[IRNode], object] = {
    IRExpressionStatement: _expression_statement,
    IRVarDecl: _var_decl,
    IRAssign: _assign,
    IRAugAssign: _aug_assign,
    IRReturn: _return,
    IRIf: _if,
    IRWhile: _while,
    IRForEach: _for_each,
    IRForRange: _for_range,
    IRBreak: _break,
    IRContinue: _continue,
    IRNoOp: _noop,
    IRThrow: _throw,
    IRTry: _try,
    IRDelete: _delete,
    IRAssert: _assert,
    IRComment: _comment,
}
