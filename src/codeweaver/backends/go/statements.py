"""IR statements to Go statements.

The hard case is exceptions.  Go has no exception mechanism: errors are values
returned alongside results, and ``panic``/``recover`` exists for genuinely
unrecoverable faults rather than for control flow.  Translating
``try``/``except`` into ``defer``/``recover`` would turn ordinary control flow
into panic handling and change where the program resumes, so CodeWeaver does
not do it.  ``raise`` becomes ``panic`` — a documented difference — and
``try`` is reported as unsupported with a hint pointing at error returns.

Two smaller points, both reported:

* Go rejects an unused variable, so a declaration whose value is never read
  would make the file fail to build.  A declaration with no initialiser is
  emitted with ``var`` plus a zero value, which is always legal.
* ``while/else`` and ``for/else`` have no equivalent, as in every other target.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from codeweaver.backends.common import CodeWriter, escape_double_quoted
from codeweaver.backends.go.context import GoEmitContext
from codeweaver.backends.go.exceptions import is_builtin_exception
from codeweaver.backends.go.expressions import emit_expression
from codeweaver.backends.go.naming import GO_POLICY
from codeweaver.backends.go.types import render_type
from codeweaver.ir.base import IRNode, IRUnsupported
from codeweaver.ir.expressions import IRCall, IRName
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.statements import (
    IRAssert,
    IRAssign,
    IRAugAssign,
    IRBlock,
    IRBreak,
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

_AUG: dict[BinaryOp, str] = {
    BinaryOp.ADD: "+=",
    BinaryOp.SUB: "-=",
    BinaryOp.MUL: "*=",
    BinaryOp.DIV: "/=",
    BinaryOp.FLOOR_DIV: "/=",
    BinaryOp.MOD: "%=",
    BinaryOp.BIT_AND: "&=",
    BinaryOp.BIT_OR: "|=",
    BinaryOp.BIT_XOR: "^=",
    BinaryOp.SHIFT_LEFT: "<<=",
    BinaryOp.SHIFT_RIGHT: ">>=",
}


def emit_block(block: IRBlock | None, writer: CodeWriter, ctx: GoEmitContext) -> None:
    if block is None:
        return
    for statement in block.statements:
        emit_statement(statement, writer, ctx)


def emit_statement(node: IRNode, writer: CodeWriter, ctx: GoEmitContext) -> None:
    if isinstance(node, IRUnsupported):
        writer.line(ctx.record_gap(node), node.span)
        return
    handler = _HANDLERS.get(type(node))
    if handler is None:
        ctx.warn("CW0720", f"no Go mapping for IR statement {node.kind!r}", span=node.span)
        writer.line(f"// unmapped statement: {node.kind}", node.span)
        return
    handler(node, writer, ctx)


def _expression_statement(
    node: IRExpressionStatement, writer: CodeWriter, ctx: GoEmitContext
) -> None:
    writer.line(emit_expression(node.expression, ctx).text, node.span)


def _var_decl(node: IRVarDecl, writer: CodeWriter, ctx: GoEmitContext) -> None:
    name = GO_POLICY.identifier(node.name)
    if node.value is None:
        # `var x T` is legal with no initialiser and gets the zero value;
        # `x := ...` is not, so an uninitialised declaration must use `var`.
        rendered = (
            render_type(node.var_type) if node.var_type.kind is not TypeKind.UNKNOWN else "any"
        )
        writer.line(f"var {name} {rendered}", node.span)
        return
    value = emit_expression(node.value, ctx).text
    if node.var_type.kind is not TypeKind.UNKNOWN:
        writer.line(f"var {name} {render_type(node.var_type)} = {value}", node.span)
        return
    writer.line(f"{name} := {value}", node.span)


def _assign(node: IRAssign, writer: CodeWriter, ctx: GoEmitContext) -> None:
    target = emit_expression(node.target, ctx).text
    value = emit_expression(node.value, ctx).text
    writer.line(f"{target} = {value}", node.span)


def _aug_assign(node: IRAugAssign, writer: CodeWriter, ctx: GoEmitContext) -> None:
    target = emit_expression(node.target, ctx).text
    value = emit_expression(node.value, ctx)
    spelling = _AUG.get(node.op)
    if spelling is None:
        ctx.warn(
            "CW0721",
            f"augmented operator {node.op.value!r} has no Go form",
            span=node.span,
        )
        writer.line(f"// unsupported augmented assignment: {node.op.value}", node.span)
        return
    if node.op is BinaryOp.FLOOR_DIV:
        ctx.warn(
            "CW0705",
            "integer division truncates toward zero in Go but floors in Python",
            span=node.span,
        )
    writer.line(f"{target} {spelling} {value.text}", node.span)


def _return(node: IRReturn, writer: CodeWriter, ctx: GoEmitContext) -> None:
    if node.value is None:
        writer.line("return", node.span)
        return
    writer.line(f"return {emit_expression(node.value, ctx).text}", node.span)


def _if(node: IRIf, writer: CodeWriter, ctx: GoEmitContext) -> None:
    writer.line(f"if {emit_expression(node.test, ctx).text} {{", node.span)
    writer.indent()
    emit_block(node.then_body, writer, ctx)
    writer.dedent()
    else_body = node.else_body
    while else_body is not None:
        if len(else_body.statements) == 1 and isinstance(else_body.statements[0], IRIf):
            inner = else_body.statements[0]
            writer.line(f"}} else if {emit_expression(inner.test, ctx).text} {{", inner.span)
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


def _while(node: IRWhile, writer: CodeWriter, ctx: GoEmitContext) -> None:
    _report_loop_else(node.else_body, ctx, node, "while")
    writer.line(f"for {emit_expression(node.test, ctx).text} {{", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _for_each(node: IRForEach, writer: CodeWriter, ctx: GoEmitContext) -> None:
    _report_loop_else(node.else_body, ctx, node, "for")
    target = emit_expression(node.target, ctx).text
    iterable = emit_expression(node.iterable, ctx).at_least(7)
    writer.line(f"for _, {target} := range {iterable} {{", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _for_range(node: IRForRange, writer: CodeWriter, ctx: GoEmitContext) -> None:
    name = GO_POLICY.identifier(node.variable)
    start = emit_expression(node.start, ctx).text
    stop = emit_expression(node.stop, ctx).text
    if node.step is None:
        header = f"for {name} := {start}; {name} < {stop}; {name}++"
    else:
        step = emit_expression(node.step, ctx).text
        # A negative step counts down, so the comparison flips with it.
        comparison = f"({step} > 0 && {name} < {stop}) || ({step} < 0 && {name} > {stop})"
        header = f"for {name} := {start}; {comparison}; {name} += {step}"
    writer.line(f"{header} {{", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _report_loop_else(
    else_body: IRBlock | None, ctx: GoEmitContext, node: IRNode, keyword: str
) -> None:
    if else_body is not None and not else_body.is_empty:
        ctx.warn(
            "CW0722",
            f"{keyword}/else has no Go equivalent and its body is not emitted",
            span=node.span,
            hint="move the else body after the loop and guard it with a flag",
        )


def _break(node: IRBreak, writer: CodeWriter, ctx: GoEmitContext) -> None:  # noqa: ARG001
    writer.line("break", node.span)


def _continue(node: IRContinue, writer: CodeWriter, ctx: GoEmitContext) -> None:  # noqa: ARG001
    writer.line("continue", node.span)


def _noop(node: IRNoOp, writer: CodeWriter, ctx: GoEmitContext) -> None:  # noqa: ARG001
    writer.line("// pass", node.span)


def _throw(node: IRThrow, writer: CodeWriter, ctx: GoEmitContext) -> None:
    ctx.warn(
        "CW0723",
        "raise becomes panic, which is not how Go reports an expected failure",
        span=node.span,
        hint="idiomatic Go returns an error value; a panic unwinds the goroutine",
    )
    if node.value is None:
        writer.line("panic(recover())", node.span)
        return
    writer.line(f"panic({_thrown_value(node.value, ctx)})", node.span)


def _thrown_value(value: IRNode, ctx: GoEmitContext) -> str:
    """Render a thrown value.

    A builtin Python exception has no Go type, so it becomes
    ``errors.New(message)`` and the lost type is reported — a translated
    program can no longer tell ``ValueError`` from ``KeyError``.  A project
    exception class is a generated struct and keeps its identity.
    """
    if isinstance(value, IRCall) and isinstance(value.callee, IRName):
        name = value.callee.name
        if is_builtin_exception(name):
            ctx.warn(
                "CW0726",
                f"{name} becomes a plain error value, so its type is lost",
                span=value.span,
                hint="define a project exception class if the distinction matters",
            )
            ctx.need("errors")
            if value.args:
                return f"errors.New({emit_expression(value.args[0], ctx).text})"
            return f"errors.New({escape_double_quoted(name)})"
    if isinstance(value, IRName) and is_builtin_exception(value.name):
        ctx.warn(
            "CW0726",
            f"{value.name} becomes a plain error value, so its type is lost",
            span=value.span,
        )
        ctx.need("errors")
        return f"errors.New({escape_double_quoted(value.name)})"
    return emit_expression(value, ctx).text


def _try(node: IRTry, writer: CodeWriter, ctx: GoEmitContext) -> None:
    ctx.warn(
        "CW0724",
        "try/except has no Go equivalent and is not translated",
        span=node.span,
        hint="Go returns errors as values; defer/recover would turn ordinary "
        "control flow into panic handling and resume somewhere else",
    )
    writer.line("// CODEWEAVER-UNSUPPORTED: try/except is not translated to Go", node.span)
    writer.line("// the body below runs unguarded:")
    emit_block(node.body, writer, ctx)
    if node.finally_body is not None and not node.finally_body.is_empty:
        writer.line("// finally body, emitted inline:")
        emit_block(node.finally_body, writer, ctx)


def _delete(node: IRDelete, writer: CodeWriter, ctx: GoEmitContext) -> None:
    for target in node.targets:
        ctx.warn(
            "CW0725",
            "deleting a binding has no Go equivalent",
            span=node.span,
            hint="use delete(m, k) for a map entry",
        )
        writer.line(f"// unsupported: del {emit_expression(target, ctx).text}", node.span)


def _assert(node: IRAssert, writer: CodeWriter, ctx: GoEmitContext) -> None:
    test = emit_expression(node.test, ctx)
    message = (
        emit_expression(node.message, ctx).text
        if node.message is not None
        else '"assertion failed"'
    )
    writer.line(f"if !{test.at_least(6)} {{", node.span)
    writer.indent()
    writer.line(f"panic({message})")
    writer.dedent()
    writer.line("}")


def _comment(node: IRComment, writer: CodeWriter, ctx: GoEmitContext) -> None:  # noqa: ARG001
    for line in node.text.split("\n"):
        writer.line(f"// {line}".rstrip(), node.span)


_Handler = Callable[[Any, CodeWriter, GoEmitContext], None]

_HANDLERS: dict[type[IRNode], _Handler] = {
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
