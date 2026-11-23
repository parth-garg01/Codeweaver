"""IR statements to C++ statements.

Python control flow that C++ lacks is lowered exactly rather than approximated:

* ``for``/``while`` with ``else`` use a break flag, so the ``else`` body runs
  only when the loop was not broken out of;
* ``try``/``else`` uses a completion flag, so exceptions raised by the ``else``
  body are *not* caught by the handlers, as in Python;
* ``finally`` becomes a scope guard, so it runs on normal exit, on ``return``
  and while an exception unwinds;
* ``except (A, B) as e`` becomes one ``catch`` per type with the same body;
* a bare ``raise`` is C++'s ``throw;``, which rethrows the active exception.
"""

from __future__ import annotations

from collections.abc import Callable

from codeweaver.backends.common.analysis import unchecked_index_loops
from codeweaver.backends.common.writer import CodeWriter
from codeweaver.backends.cpp.context import CppContext, LoopFrame
from codeweaver.backends.cpp.expressions import (
    condition,
    emit_expression,
    emit_store_target,
    is_int_arithmetic,
)
from codeweaver.backends.cpp.naming import safe_identifier
from codeweaver.backends.cpp.types import render_type
from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS
from codeweaver.ir.base import IRNode, IRUnsupported, walk
from codeweaver.ir.expressions import IRAttribute, IRCall, IRExpression, IRIndex, IRLiteral, IRName
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
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.scoping import HOISTED

__all__ = ["emit_block", "emit_statement"]

_AUG_SPELLING: dict[BinaryOp, str] = {
    BinaryOp.ADD: "+=",
    BinaryOp.SUB: "-=",
    BinaryOp.MUL: "*=",
    BinaryOp.BIT_AND: "&=",
    BinaryOp.BIT_OR: "|=",
    BinaryOp.BIT_XOR: "^=",
    BinaryOp.SHIFT_LEFT: "<<=",
    BinaryOp.SHIFT_RIGHT: ">>=",
}
_AUG_RUNTIME: dict[BinaryOp, str] = {
    BinaryOp.DIV: "cw::truediv",
    BinaryOp.FLOOR_DIV: "cw::floordiv",
    BinaryOp.MOD: "cw::mod",
    BinaryOp.POW: "cw::pow",
}


def emit_block(block: IRBlock | None, writer: CodeWriter, ctx: CppContext) -> None:
    if block is None:
        return
    for statement in block.statements:
        emit_statement(statement, writer, ctx)


def emit_statement(node: IRNode, writer: CodeWriter, ctx: CppContext) -> None:
    if isinstance(node, IRUnsupported):
        writer.line(ctx.record_gap(node), node.span)
        return
    handler = _HANDLERS.get(type(node))
    if handler is None:
        writer.line(ctx.gap(node.kind, "no C++ mapping for this statement", node.span), node.span)
        return
    handler(node, writer, ctx)


def _function_return_type(ctx: CppContext) -> IRType | None:
    return ctx.function.return_type if ctx.function is not None else None


def declared_type(name: str, var_type: IRType, ctx: CppContext, *, span=None) -> str:
    rendered = render_type(var_type, ctx)
    if rendered is None:
        if var_type.kind is not TypeKind.UNKNOWN:
            ctx.warn("CW0689", f"type of {name!r} has no C++ spelling; 'auto' is used", span=span)
        return "auto"
    return rendered


def _expression_statement(node: IRExpressionStatement, writer: CodeWriter, ctx: CppContext) -> None:
    expression = node.expression
    if isinstance(expression, IRLiteral) and isinstance(expression.value, str):
        # A bare string statement is a comment in all but name.
        writer.comment(expression.value, node.span)
        return
    writer.line(f"{emit_expression(expression, ctx).text};", node.span)


def _var_decl(node: IRVarDecl, writer: CodeWriter, ctx: CppContext) -> None:
    ctx.locals.add(node.name)
    name = safe_identifier(node.name)
    if node.value is None:
        spelled = render_type(node.var_type, ctx)
        if spelled is None:
            marker = ctx.gap(
                "Assign",
                f"{node.name!r} is declared before it is assigned but its type is unknown",
                node.span,
                "annotate the variable",
            )
            writer.line(f'{marker} auto {name} = cw::unsupported("declaration");', node.span)
            return
        if node.meta.get(HOISTED):
            writer.line(f"{spelled} {name}{{}};", node.span)
        else:
            writer.line(f"{spelled} {name}{{}};", node.span)
        return
    spelled = declared_type(node.name, node.var_type, ctx, span=node.span)
    value = emit_expression(node.value, ctx, node.var_type).text
    prefix = "const " if node.is_const and spelled != "auto" else ""
    writer.line(f"{prefix}{spelled} {name} = {value};", node.span)


def _assign(node: IRAssign, writer: CodeWriter, ctx: CppContext) -> None:
    target = node.target
    if isinstance(target, IRName) and target.name not in ctx.locals and ctx.function is not None:
        # The frontend declares on first binding, so this only happens for a
        # name bound by an outer construct.  Declare it here.
        ctx.locals.add(target.name)
        spelled = declared_type(target.name, node.value.value_type, ctx, span=node.span)
        value = emit_expression(node.value, ctx).text
        writer.line(f"{spelled} {safe_identifier(target.name)} = {value};", node.span)
        return
    expected = getattr(target, "value_type", None)
    rendered_target = emit_store_target(target, ctx)
    value = emit_expression(node.value, ctx, expected).text
    writer.line(f"{rendered_target} = {value};", node.span)


#: Overflow-checked in-place int operators (see cw::checked_add).
_CHECKED_AUG: dict[BinaryOp, str] = {
    BinaryOp.ADD: "cw::iadd",
    BinaryOp.SUB: "cw::isub",
    BinaryOp.MUL: "cw::imul",
}


def _aug_assign(node: IRAugAssign, writer: CodeWriter, ctx: CppContext) -> None:
    target = (
        emit_store_target(node.target, ctx)
        if not isinstance(node.target, IRIndex)
        else emit_expression(node.target, ctx).text
    )
    value = emit_expression(node.value, ctx).text
    runtime = _AUG_RUNTIME.get(node.op)
    if runtime is not None:
        target_type = getattr(node.target, "value_type", None)
        if node.op is BinaryOp.DIV and target_type is not None and target_type.name == "int":
            ctx.warn(
                "CW0690",
                "'/=' turns an int into a float in Python, but the C++ variable keeps its integer type",
                span=node.span,
                hint="declare the variable as float",
            )
        writer.line(f"{target} = {runtime}({target}, {value});", node.span)
        return
    if node.op is BinaryOp.ADD and getattr(node.target, "value_type", None) is not None:
        target_type = node.target.value_type
        if target_type.kind is TypeKind.COLLECTION and target_type.name == "list":
            writer.line(f"cw::extend({target}, {value});", node.span)
            return
    checked = _CHECKED_AUG.get(node.op)
    if checked is not None and is_int_arithmetic(
        getattr(node.target, "value_type", None), getattr(node.value, "value_type", None)
    ):
        # The target is bound by reference, so it is evaluated once, as in
        # Python: `xs[f()] += 1`.
        writer.line(f"{checked}({target}, {value});", node.span)
        return
    spelling = _AUG_SPELLING.get(node.op)
    if spelling is None:
        writer.line(
            ctx.gap(
                "AugAssign", f"augmented operator {node.op.value!r} has no C++ spelling", node.span
            ),
            node.span,
        )
        return
    writer.line(f"{target} {spelling} {value};", node.span)


def _return(node: IRReturn, writer: CodeWriter, ctx: CppContext) -> None:
    if node.value is None:
        writer.line("return;", node.span)
        return
    expected = _function_return_type(ctx)
    if ctx.function is not None and ctx.function.is_constructor:
        writer.line("return;", node.span)
        return
    writer.line(f"return {emit_expression(node.value, ctx, expected).text};", node.span)


def _if(node: IRIf, writer: CodeWriter, ctx: CppContext) -> None:
    writer.line(f"if ({condition(node.test, ctx)}) {{", node.span)
    writer.indent()
    emit_block(node.then_body, writer, ctx)
    writer.dedent()
    else_body = node.else_body
    while else_body is not None:
        if len(else_body.statements) == 1 and isinstance(else_body.statements[0], IRIf):
            inner = else_body.statements[0]
            writer.line(f"}} else if ({condition(inner.test, ctx)}) {{", inner.span)
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


def _loop(
    header: str,
    body: IRBlock,
    else_body: IRBlock | None,
    node: IRNode,
    writer: CodeWriter,
    ctx: CppContext,
    prelude: list[str] | None = None,
) -> None:
    has_else = else_body is not None and not else_body.is_empty
    frame = LoopFrame(broke=ctx.temp("broke") if has_else else "")
    if has_else:
        writer.line(f"bool {frame.broke} = false;")
    writer.line(f"{header} {{", node.span)
    writer.indent()
    for line in prelude or []:
        writer.line(line)
    ctx.loops.append(frame)
    emit_block(body, writer, ctx)
    ctx.loops.pop()
    writer.dedent()
    writer.line("}")
    if has_else:
        writer.line(f"if (!{frame.broke}) {{")
        writer.indent()
        emit_block(else_body, writer, ctx)
        writer.dedent()
        writer.line("}")


def _while(node: IRWhile, writer: CodeWriter, ctx: CppContext) -> None:
    _loop(f"while ({condition(node.test, ctx)})", node.body, node.else_body, node, writer, ctx)


def _rebinds(body: IRBlock, name: str) -> bool:
    for item in walk(body):
        if (
            isinstance(item, IRAssign | IRAugAssign)
            and isinstance(item.target, IRName)
            and item.target.name == name
        ):
            return True
    return False


def _for_each(node: IRForEach, writer: CodeWriter, ctx: CppContext) -> None:
    iterable_type = getattr(node.iterable, "value_type", None)
    iterable = emit_expression(node.iterable, ctx).text
    target = node.target
    if not isinstance(target, IRName):
        writer.line(
            ctx.gap("For", "only a single name can be a loop target in C++", node.span), node.span
        )
        return
    ctx.locals.add(target.name)
    name = safe_identifier(target.name)
    if target.name in ctx.escaping:
        _escaping_for_each(node, target.name, iterable, iterable_type, writer, ctx)
        return
    # Rebinding the loop variable must not write through to the container.
    binding = "auto" if _rebinds(node.body, target.name) else "auto&&"
    if (
        iterable_type is not None
        and iterable_type.kind is TypeKind.COLLECTION
        and iterable_type.name == "dict"
    ):
        header = f"for ({binding} [{name}, {ctx.temp('value')}] : {iterable})"
        writer.line("// iterates keys in insertion order")
    elif (
        iterable_type is not None
        and iterable_type.kind is TypeKind.PRIMITIVE
        and iterable_type.name == "str"
    ):
        char = ctx.temp("char")
        header = f"for (char {char} : {iterable})"
        _loop(
            header,
            node.body,
            node.else_body,
            node,
            writer,
            ctx,
            [f"std::string {name}(1, {char});"],
        )
        return
    else:
        header = f"for ({binding} {name} : {iterable})"
    _loop(header, node.body, node.else_body, node, writer, ctx)


def _declare_escaping(name: str, spelled: str, writer: CodeWriter, ctx: CppContext) -> None:
    if name not in ctx.declared:
        ctx.declared.add(name)
        writer.line(f"{spelled} {safe_identifier(name)}{{}};  // outlives the loop, as in Python")


def _escaping_for_each(
    node: IRForEach, name: str, iterable: str, iterable_type, writer: CodeWriter, ctx: CppContext
) -> None:
    from codeweaver.transforms.inference import element_type

    element = element_type(iterable_type)
    spelled = render_type(element, ctx) if element is not None else None
    if spelled is None:
        writer.line(
            ctx.gap(
                "For",
                f"loop variable {name!r} is used after the loop but its type is unknown",
                node.span,
                "annotate the iterable",
            ),
            node.span,
        )
        return
    _declare_escaping(name, spelled, writer, ctx)
    item = ctx.temp("item")
    _loop(
        f"for (const auto& {item} : {iterable})",
        node.body,
        node.else_body,
        node,
        writer,
        ctx,
        [f"{safe_identifier(name)} = {item};"],
    )


def _for_range(node: IRForRange, writer: CodeWriter, ctx: CppContext) -> None:
    ctx.locals.add(node.variable)
    name = safe_identifier(node.variable)
    if node.variable in ctx.escaping:
        # The Python variable keeps the last value it took; the counter is hidden.
        _declare_escaping(node.variable, "std::int64_t", writer, ctx)
        visible = name
        name = ctx.temp("i")
    else:
        visible = ""
    start = emit_expression(node.start, ctx).text
    stop = emit_expression(node.stop, ctx).text
    step_node = node.step
    previous = ctx.unchecked
    ctx.unchecked = previous | unchecked_index_loops(node)
    if step_node is None or (isinstance(step_node, IRLiteral) and step_node.value == 1):
        bound = ctx.temp("stop")
        writer.line(f"const std::int64_t {bound} = {stop};", node.span)
        header = f"for (std::int64_t {name} = {start}; {name} < {bound}; ++{name})"
    elif isinstance(step_node, IRLiteral) and isinstance(step_node.value, int):
        comparison = "<" if step_node.value > 0 else ">"
        bound = ctx.temp("stop")
        writer.line(f"const std::int64_t {bound} = {stop};", node.span)
        header = f"for (std::int64_t {name} = {start}; {name} {comparison} {bound}; {name} += {step_node.value})"
    else:
        step_name = ctx.temp("step")
        bound = ctx.temp("stop")
        writer.line(
            f"const std::int64_t {step_name} = {emit_expression(step_node, ctx).text};", node.span
        )
        writer.line(f"const std::int64_t {bound} = {stop};")
        writer.line(
            f'if ({step_name} == 0) throw cw::ValueError("range() arg 3 must not be zero");'
        )
        header = (
            f"for (std::int64_t {name} = {start}; {step_name} > 0 ? {name} < {bound} : {name} > {bound}; "
            f"{name} += {step_name})"
        )
    if visible:
        ctx.unchecked = previous
    _loop(header, node.body, None, node, writer, ctx, [f"{visible} = {name};"] if visible else None)
    ctx.unchecked = previous


def _break(node: IRBreak, writer: CodeWriter, ctx: CppContext) -> None:
    frame = ctx.loops[-1] if ctx.loops else None
    if frame is not None and frame.broke:
        writer.line(f"{frame.broke} = true;", node.span)
    writer.line("break;", node.span)


def _continue(node: IRContinue, writer: CodeWriter, ctx: CppContext) -> None:
    writer.line("continue;", node.span)


def _noop(node: IRNoOp, writer: CodeWriter, ctx: CppContext) -> None:
    writer.line("// pass", node.span)


def _throw(node: IRThrow, writer: CodeWriter, ctx: CppContext) -> None:
    if node.value is None:
        writer.line("throw;", node.span)
        return
    if node.cause is not None:
        ctx.warn(
            "CW0691",
            "exception chaining ('raise ... from ...') is not represented in C++; the cause is dropped",
            span=node.span,
        )
    value = node.value
    if (
        isinstance(value, IRName)
        and value.name in BUILTIN_EXCEPTIONS
        and value.name not in ctx.locals
    ):
        writer.line(f"throw cw::{value.name}();", node.span)
        return
    if isinstance(value, IRName) and ctx.index is not None and value.name not in ctx.locals:
        info = ctx.class_of(getattr(value, "value_type", None))
        if info is not None and info.is_exception:
            writer.line(f"throw {ctx.class_spelling(info)}();", node.span)
            return
    rendered = emit_expression(value, ctx).text
    if isinstance(value, IRCall) or (isinstance(value, IRName) and value.name in ctx.locals):
        writer.line(f"throw {rendered};", node.span)
        return
    writer.line(f"throw {rendered};", node.span)


def _catch_type(item, ctx: CppContext) -> str:
    if isinstance(item, IRName):
        if item.name in ("Exception", "BaseException") and ctx.class_of(item.value_type) is None:
            return f"cw::{item.name}"
        info = ctx.class_of(item.value_type)
        if info is not None:
            return ctx.class_spelling(info)
        if item.name in BUILTIN_EXCEPTIONS:
            return f"cw::{item.name}"
    return emit_expression(item, ctx).text


def _try(node: IRTry, writer: CodeWriter, ctx: CppContext) -> None:
    has_finally = node.finally_body is not None and not node.finally_body.is_empty
    has_else = node.else_body is not None and not node.else_body.is_empty
    if has_finally:
        assert node.finally_body is not None
        for item in walk(node.finally_body):
            if isinstance(item, IRReturn | IRBreak | IRContinue):
                writer.line(
                    ctx.gap(
                        "Try",
                        "return, break or continue inside finally cannot be expressed in C++",
                        node.span,
                    ),
                    node.span,
                )
                return
        writer.line("{", node.span)
        writer.indent()
        guard = ctx.temp("finally")
        writer.line(f"cw::Finally {guard}([&] {{")
        writer.indent()
        emit_block(node.finally_body, writer, ctx)
        writer.dedent()
        writer.line("});")
    completed = ctx.temp("completed") if has_else else ""
    if has_else:
        writer.line(f"bool {completed} = false;")
    if node.handlers:
        writer.line("try {", node.span if not has_finally else None)
        writer.indent()
        emit_block(node.body, writer, ctx)
        if has_else:
            writer.line(f"{completed} = true;")
        writer.dedent()
        for handler in node.handlers:
            _emit_handler(handler, writer, ctx)
        writer.line("}")
    else:
        writer.line("{")
        writer.indent()
        emit_block(node.body, writer, ctx)
        if has_else:
            writer.line(f"{completed} = true;")
        writer.dedent()
        writer.line("}")
    if has_else:
        writer.line(f"if ({completed}) {{")
        writer.indent()
        emit_block(node.else_body, writer, ctx)
        writer.dedent()
        writer.line("}")
    if has_finally:
        writer.dedent()
        writer.line("}")


def _emit_handler(handler: IRCatch, writer: CodeWriter, ctx: CppContext) -> None:
    types: list[IRExpression | None] = list(handler.exception_types) or [None]
    for item in types:
        if item is None:
            if handler.name:
                header = f"}} catch (const cw::BaseException& {safe_identifier(handler.name)}) {{"
            else:
                header = "} catch (...) {"
        else:
            spelled = _catch_type(item, ctx)
            binding = f" {safe_identifier(handler.name)}" if handler.name else ""
            header = f"}} catch (const {spelled}&{binding}) {{"
        writer.line(header, handler.span)
        writer.indent()
        if handler.name:
            ctx.locals.add(handler.name)
        emit_block(handler.body, writer, ctx)
        writer.dedent()
    # The closing brace is written by the caller after the last handler.


def _delete(node: IRDelete, writer: CodeWriter, ctx: CppContext) -> None:
    for target in node.targets:
        if isinstance(target, IRIndex):
            container = emit_expression(target.target, ctx).text
            container_type = getattr(target.target, "value_type", None)
            key = emit_expression(target.index, ctx).text
            if container_type is not None and container_type.name == "dict":
                writer.line(
                    f'if (!{container}.erase({key})) throw cw::KeyError("key not found");',
                    node.span,
                )
            else:
                writer.line(f"cw::pop({container}, {key});", node.span)
            continue
        name = target.name if isinstance(target, IRName) else "<expression>"
        writer.line(
            ctx.gap("Delete", f"deleting the binding {name!r} has no C++ equivalent", node.span),
            node.span,
        )


def _assert(node: IRAssert, writer: CodeWriter, ctx: CppContext) -> None:
    message = (
        emit_expression(node.message, ctx).text
        if node.message is not None
        else '"assertion failed"'
    )
    test = condition(node.test, ctx)
    writer.line(f"if (!({test})) {{", node.span)
    writer.indent()
    writer.line(f"throw cw::AssertionError({message});")
    writer.dedent()
    writer.line("}")


def _comment(node: IRComment, writer: CodeWriter, ctx: CppContext) -> None:
    writer.comment(node.text, node.span)


def _nested_block(node: IRBlock, writer: CodeWriter, ctx: CppContext) -> None:
    emit_block(node, writer, ctx)


def is_super_init(statement: IRNode) -> IRCall | None:
    """The ``super().__init__(...)`` call a statement makes, if it is one."""
    if not isinstance(statement, IRExpressionStatement):
        return None
    call = statement.expression
    if not isinstance(call, IRCall) or not isinstance(call.callee, IRAttribute):
        return None
    if call.callee.name != "__init__":
        return None
    target = call.callee.target
    if (
        isinstance(target, IRCall)
        and isinstance(target.callee, IRName)
        and target.callee.name == "super"
    ):
        return call
    return None


_Handler = Callable[[IRNode, CodeWriter, CppContext], None]

_HANDLERS: dict[type[IRNode], _Handler] = {
    IRExpressionStatement: _expression_statement,  # type: ignore[dict-item]
    IRVarDecl: _var_decl,  # type: ignore[dict-item]
    IRAssign: _assign,  # type: ignore[dict-item]
    IRAugAssign: _aug_assign,  # type: ignore[dict-item]
    IRReturn: _return,  # type: ignore[dict-item]
    IRIf: _if,  # type: ignore[dict-item]
    IRWhile: _while,  # type: ignore[dict-item]
    IRForEach: _for_each,  # type: ignore[dict-item]
    IRForRange: _for_range,  # type: ignore[dict-item]
    IRBreak: _break,  # type: ignore[dict-item]
    IRContinue: _continue,  # type: ignore[dict-item]
    IRNoOp: _noop,  # type: ignore[dict-item]
    IRThrow: _throw,  # type: ignore[dict-item]
    IRTry: _try,  # type: ignore[dict-item]
    IRDelete: _delete,  # type: ignore[dict-item]
    IRAssert: _assert,  # type: ignore[dict-item]
    IRComment: _comment,  # type: ignore[dict-item]
    IRBlock: _nested_block,  # type: ignore[dict-item]
}
