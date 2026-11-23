"""IR statements to Java statements.

Besides the Python control flow Java lacks (``for``/``else``,
``try``/``else``), two Java rules shape the output:

* Java rejects statements it can prove unreachable and methods that can fall
  off the end, so dead code after ``return``/``raise`` is omitted and a
  value-returning method that can complete normally ends in a ``throw``, which
  is what Python's implicit ``return None`` would turn into;
* a multi-catch may not list related types and a later ``catch`` may not be
  subsumed by an earlier one, so handler types are normalised the way Python
  matches them: the first handler that matches wins.
"""

from __future__ import annotations

from collections.abc import Callable

from codeweaver.backends.common.analysis import can_complete, unchecked_index_loops
from codeweaver.backends.common.clike import POSTFIX
from codeweaver.backends.common.writer import CodeWriter
from codeweaver.backends.java.context import JavaContext, LoopFrame
from codeweaver.backends.java.expressions import (
    CHECKED_INT_OPS,
    condition,
    emit_expression,
    emit_store,
    java_string,
)
from codeweaver.backends.java.naming import safe_identifier
from codeweaver.backends.java.types import EXCEPTIONS, default_value, render_type
from codeweaver.diagnostics import SourceSpan
from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS
from codeweaver.ir.base import IRNode, IRUnsupported, walk
from codeweaver.ir.expressions import IRBinary, IRCall, IRExpression, IRIndex, IRLiteral, IRName
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
from codeweaver.transforms.inference import element_type

__all__ = ["can_complete", "emit_block", "emit_statement"]

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

#: Java exception type -> its parent among the types the generator emits.
_JAVA_PARENTS: dict[str, str] = {
    "RuntimeException": "Throwable",
    "Error": "Throwable",
    "AssertionError": "Error",
    "IllegalArgumentException": "RuntimeException",
    "IndexOutOfBoundsException": "RuntimeException",
    "NoSuchElementException": "RuntimeException",
    "ArithmeticException": "RuntimeException",
    "UnsupportedOperationException": "RuntimeException",
    "Py.UnicodeError": "IllegalArgumentException",
    "Py.RecursionError": "Py.RuntimeError",
    "Py.FileNotFoundError": "Py.OSError",
    "Py.PermissionError": "Py.OSError",
    "Py.TimeoutError": "Py.OSError",
}

#: Python handler types that catch more than one Java type.
_CATCH_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "BaseException": ("RuntimeException", "Error"),
    "Exception": ("RuntimeException", "AssertionError"),
    "LookupError": ("IndexOutOfBoundsException", "NoSuchElementException", "Py.LookupError"),
}


# ------------------------------------------------------------------- driver


def _flush(writer: CodeWriter, ctx: JavaContext) -> None:
    for line in ctx.pre:
        writer.line(line)
    ctx.pre.clear()


def _line(writer: CodeWriter, ctx: JavaContext, text: str, span: SourceSpan | None = None) -> None:
    _flush(writer, ctx)
    writer.line(text, span)


def emit_block(block: IRBlock | None, writer: CodeWriter, ctx: JavaContext) -> None:
    if block is None:
        return
    statements = block.statements
    for position, statement in enumerate(statements):
        emit_statement(statement, writer, ctx)
        if not can_complete(statement) and position + 1 < len(statements):
            remaining = [
                item for item in statements[position + 1 :] if not isinstance(item, IRComment)
            ]
            if remaining:
                writer.line("// unreachable code after this point is omitted (Java rejects it)")
            break


def emit_statement(node: IRNode, writer: CodeWriter, ctx: JavaContext) -> None:
    if isinstance(node, IRUnsupported):
        _line(writer, ctx, ctx.record_gap(node), node.span)
        return
    handler = _HANDLERS.get(type(node))
    if handler is None:
        _line(
            writer,
            ctx,
            ctx.gap(node.kind, "no Java mapping for this statement", node.span),
            node.span,
        )
        return
    handler(node, writer, ctx)


def declared_type(
    name: str, var_type: IRType, ctx: JavaContext, *, span: SourceSpan | None = None
) -> str | None:
    rendered = render_type(var_type, ctx)
    if rendered is None and var_type.kind is not TypeKind.UNKNOWN:
        ctx.warn("CW0689", f"type of {name!r} has no Java spelling; 'var' is used", span=span)
    return rendered


# ---------------------------------------------------------------- simple


def _is_statement_expression(node: IRNode, text: str, precedence: int) -> bool:
    """Java allows only calls, assignments and ``new`` as expression statements."""
    return (
        isinstance(node, IRCall)
        and precedence >= POSTFIX
        and text.endswith(")")
        and not text.startswith(("(", "!"))
    )


def _expression_statement(
    node: IRExpressionStatement, writer: CodeWriter, ctx: JavaContext
) -> None:
    expression = node.expression
    if isinstance(expression, IRLiteral) and isinstance(expression.value, str):
        writer.comment(expression.value, node.span)
        return
    rendered = emit_expression(expression, ctx)
    if _is_statement_expression(expression, rendered.text, rendered.precedence):
        _line(writer, ctx, f"{rendered.text};", node.span)
    else:
        _line(writer, ctx, f"var {ctx.temp('unused')} = {rendered.text};", node.span)


def _var_decl(node: IRVarDecl, writer: CodeWriter, ctx: JavaContext) -> None:
    ctx.locals.add(node.name)
    name = ctx.local(node.name)
    spelled = declared_type(node.name, node.var_type, ctx, span=node.span)
    if node.value is None:
        if spelled is None:
            marker = ctx.gap(
                "Assign",
                f"{node.name!r} is declared before it is assigned but its type is unknown",
                node.span,
                "annotate the variable",
            )
            _line(writer, ctx, f"{marker} Object {name} = null;", node.span)
            return
        _line(writer, ctx, f"{spelled} {name} = {default_value(spelled)};", node.span)
        return
    value = emit_expression(node.value, ctx, node.var_type).text
    if spelled is None and value == "null":
        ctx.warn(
            "CW0689",
            f"type of {node.name!r} is unknown and it starts as None; 'Object' is used",
            span=node.span,
        )
        spelled = "Object"
    _line(writer, ctx, f"{spelled or 'var'} {name} = {value};", node.span)


def _assign(node: IRAssign, writer: CodeWriter, ctx: JavaContext) -> None:
    target = node.target
    if isinstance(target, IRName) and target.name not in ctx.locals and ctx.function is not None:
        ctx.locals.add(target.name)
        spelled = declared_type(target.name, node.value.value_type, ctx, span=node.span)
        value = emit_expression(node.value, ctx).text
        _line(writer, ctx, f"{spelled or 'var'} {ctx.local(target.name)} = {value};", node.span)
        return
    expected = getattr(target, "value_type", None)
    value = emit_expression(node.value, ctx, expected).text
    _line(writer, ctx, f"{emit_store(target, value, ctx)};", node.span)


def _aug_assign(node: IRAugAssign, writer: CodeWriter, ctx: JavaContext) -> None:
    target = node.target
    target_type = getattr(target, "value_type", None)
    if (
        node.op is BinaryOp.ADD
        and target_type is not None
        and target_type.kind is TypeKind.COLLECTION
    ):
        if target_type.name in ("list", "set"):
            container = emit_expression(target, ctx).text
            _line(
                writer,
                ctx,
                f"{container}.addAll({emit_expression(node.value, ctx).text});",
                node.span,
            )
            return
    simple = _AUG_SPELLING.get(node.op)
    value_type = getattr(node.value, "value_type", None)
    checked = (
        node.op in CHECKED_INT_OPS
        and target_type is not None
        and target_type.name == "int"
        and value_type is not None
        and value_type.name == "int"
    )
    if isinstance(target, IRIndex) or simple is None or checked:
        if node.op is BinaryOp.DIV and target_type is not None and target_type.name == "int":
            ctx.warn(
                "CW0690",
                "'/=' turns an int into a float in Python, but the Java variable keeps its integer type",
                span=node.span,
                hint="declare the variable as float",
            )
        combined = IRBinary(node.op, target, node.value, value_type=target_type, span=node.span)  # type: ignore[arg-type]
        value = emit_expression(combined, ctx, target_type).text
        _line(writer, ctx, f"{emit_store(target, value, ctx)};", node.span)
        return
    rendered_target = emit_expression(target, ctx).text
    value = emit_expression(node.value, ctx, target_type).text
    _line(writer, ctx, f"{rendered_target} {simple} {value};", node.span)


def _return(node: IRReturn, writer: CodeWriter, ctx: JavaContext) -> None:
    function = ctx.function
    if node.value is None or (function is not None and function.is_constructor):
        _line(writer, ctx, "return;", node.span)
        return
    expected = function.return_type if function is not None else None
    if function is not None and render_type(function.return_type, ctx, returning=True) == "void":
        # `return f()` in a function annotated -> None: evaluate, then return.
        rendered = emit_expression(node.value, ctx)
        if not (isinstance(node.value, IRLiteral) and node.value.value is None):
            _line(writer, ctx, f"{rendered.text};", node.span)
        _line(writer, ctx, "return;", node.span)
        return
    _line(writer, ctx, f"return {emit_expression(node.value, ctx, expected).text};", node.span)


def _noop(node: IRNoOp, writer: CodeWriter, ctx: JavaContext) -> None:
    writer.line("// pass", node.span)


def _comment(node: IRComment, writer: CodeWriter, ctx: JavaContext) -> None:
    writer.comment(node.text, node.span)


def _nested_block(node: IRBlock, writer: CodeWriter, ctx: JavaContext) -> None:
    emit_block(node, writer, ctx)


# ----------------------------------------------------------- control flow


def _body(block: IRBlock | None, writer: CodeWriter, ctx: JavaContext) -> None:
    writer.indent()
    emit_block(block, writer, ctx)
    writer.dedent()


def _if(node: IRIf, writer: CodeWriter, ctx: JavaContext) -> None:
    _line(writer, ctx, f"if ({condition(node.test, ctx)}) {{", node.span)
    _body(node.then_body, writer, ctx)
    else_body = node.else_body
    while else_body is not None and not else_body.is_empty:
        if len(else_body.statements) == 1 and isinstance(else_body.statements[0], IRIf):
            inner = else_body.statements[0]
            test = condition(inner.test, ctx)
            if not ctx.pre:
                writer.line(f"}} else if ({test}) {{", inner.span)
                _body(inner.then_body, writer, ctx)
                else_body = inner.else_body
                continue
            # The condition needs captured copies first: nest it.
            writer.line("} else {")
            writer.indent()
            _line(writer, ctx, f"if ({test}) {{", inner.span)
            _body(inner.then_body, writer, ctx)
            if inner.else_body is not None and not inner.else_body.is_empty:
                writer.line("} else {")
                _body(inner.else_body, writer, ctx)
            writer.line("}")
            writer.dedent()
            break
        writer.line("} else {")
        _body(else_body, writer, ctx)
        break
    writer.line("}")


def _loop(
    header: str,
    body: IRBlock,
    else_body: IRBlock | None,
    node: IRNode,
    writer: CodeWriter,
    ctx: JavaContext,
    prelude: list[str] | None = None,
) -> None:
    has_else = else_body is not None and not else_body.is_empty
    frame = LoopFrame(broke=ctx.temp("broke") if has_else else "")
    if has_else:
        _line(writer, ctx, f"boolean {frame.broke} = false;")
    _line(writer, ctx, f"{header} {{", node.span)
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
        _body(else_body, writer, ctx)
        writer.line("}")


def _while(node: IRWhile, writer: CodeWriter, ctx: JavaContext) -> None:
    test = condition(node.test, ctx)
    if ctx.pre:
        ctx.warn(
            "CW0703",
            "a lambda in a while condition captures a copy made once, before the loop",
            span=node.span,
            hint="assign the lambda to a variable inside the loop",
        )
    _loop(f"while ({test})", node.body, node.else_body, node, writer, ctx)


def _rebinds(body: IRBlock, name: str) -> bool:
    return any(
        isinstance(item, IRAssign | IRAugAssign)
        and isinstance(item.target, IRName)
        and item.target.name == name
        for item in walk(body)
    )


def _declare_escaping(name: str, spelled: str, writer: CodeWriter, ctx: JavaContext) -> None:
    if name not in ctx.declared:
        ctx.declared.add(name)
        _line(
            writer,
            ctx,
            f"{spelled} {ctx.local(name)} = {default_value(spelled)};  // outlives the loop, as in Python",
        )


def _for_each(node: IRForEach, writer: CodeWriter, ctx: JavaContext) -> None:
    target = node.target
    if not isinstance(target, IRName):
        _line(
            writer,
            ctx,
            ctx.gap("For", "only a single name can be a loop target in Java", node.span),
            node.span,
        )
        return
    from codeweaver.backends.java.expressions import _iterable

    iterable_type = getattr(node.iterable, "value_type", None)
    iterable = _iterable(node.iterable, ctx)
    if (
        iterable_type is not None
        and iterable_type.kind is TypeKind.COLLECTION
        and iterable_type.name == "dict"
    ):
        element = iterable_type.args[0] if iterable_type.args else None
    elif (
        iterable_type is not None
        and iterable_type.kind is TypeKind.PRIMITIVE
        and iterable_type.name == "str"
    ):
        element = IRType(TypeKind.PRIMITIVE, "str")
    else:
        element = element_type(iterable_type)
    spelled = render_type(element, ctx) if element is not None else None
    ctx.locals.add(target.name)
    name = ctx.local(target.name)
    if target.name in ctx.escaping:
        if spelled is None:
            _line(
                writer,
                ctx,
                ctx.gap(
                    "For",
                    f"loop variable {target.name!r} is used after the loop but its type is unknown",
                    node.span,
                ),
                node.span,
            )
            return
        _declare_escaping(target.name, spelled, writer, ctx)
        item = ctx.temp("item")
        _loop(
            f"for ({spelled} {item} : {iterable})",
            node.body,
            node.else_body,
            node,
            writer,
            ctx,
            [f"{name} = {item};"],
        )
        return
    final = "" if _rebinds(node.body, target.name) else "final "
    _loop(
        f"for ({final}{spelled or 'var'} {name} : {iterable})",
        node.body,
        node.else_body,
        node,
        writer,
        ctx,
    )


def _for_range(node: IRForRange, writer: CodeWriter, ctx: JavaContext) -> None:
    ctx.locals.add(node.variable)
    visible = ""
    name = ctx.local(node.variable)
    if node.variable in ctx.escaping:
        _declare_escaping(node.variable, "long", writer, ctx)
        visible, name = name, ctx.temp("i")
    start = emit_expression(node.start, ctx).text
    stop_node, step_node = node.stop, node.step
    stop = emit_expression(stop_node, ctx).text
    if not isinstance(stop_node, IRLiteral):
        # Python evaluates the bound once, before the loop.
        bound = ctx.temp("stop")
        _line(writer, ctx, f"final long {bound} = {stop};", node.span)
        stop = bound
    previous = ctx.unchecked
    if not visible:
        ctx.unchecked = previous | unchecked_index_loops(node)
    if step_node is None or (isinstance(step_node, IRLiteral) and step_node.value == 1):
        header = f"for (long {name} = {start}; {name} < {stop}; {name}++)"
    elif isinstance(step_node, IRLiteral) and isinstance(step_node.value, int):
        comparison = "<" if step_node.value > 0 else ">"
        header = (
            f"for (long {name} = {start}; {name} {comparison} {stop}; {name} += {step_node.value}L)"
        )
    else:
        step = ctx.temp("step")
        _line(
            writer, ctx, f"final long {step} = {emit_expression(step_node, ctx).text};", node.span
        )
        _line(
            writer,
            ctx,
            f'if ({step} == 0) throw new IllegalArgumentException("range() arg 3 must not be zero");',
        )
        header = f"for (long {name} = {start}; {step} > 0 ? {name} < {stop} : {name} > {stop}; {name} += {step})"
    _loop(header, node.body, None, node, writer, ctx, [f"{visible} = {name};"] if visible else None)
    ctx.unchecked = previous


def _break(node: IRBreak, writer: CodeWriter, ctx: JavaContext) -> None:
    frame = ctx.loops[-1] if ctx.loops else None
    if frame is not None and frame.broke:
        _line(writer, ctx, f"{frame.broke} = true;", node.span)
    _line(writer, ctx, "break;", node.span)


def _continue(node: IRContinue, writer: CodeWriter, ctx: JavaContext) -> None:
    _line(writer, ctx, "continue;", node.span)


# --------------------------------------------------------------- exceptions


def _exception_class(node: IRExpression, ctx: JavaContext) -> str | None:
    if isinstance(node, IRName) and node.name not in ctx.locals:
        info = ctx.class_of(node.value_type)
        if info is not None:
            return ctx.class_spelling(info)
        if node.name in BUILTIN_EXCEPTIONS:
            return EXCEPTIONS.get(node.name, "RuntimeException")
    return None


def _throw(node: IRThrow, writer: CodeWriter, ctx: JavaContext) -> None:
    if node.value is None:
        active = ctx.active_errors[-1] if ctx.active_errors else ""
        if not active:
            _line(
                writer,
                ctx,
                ctx.gap(
                    "Raise", "a bare 'raise' outside an except block has no Java mapping", node.span
                ),
                node.span,
            )
            return
        _line(writer, ctx, f"throw {active};", node.span)
        return
    value = node.value
    klass = _exception_class(value, ctx)
    rendered = f"new {klass}()" if klass is not None else emit_expression(value, ctx).text
    if node.cause is not None:
        cause = node.cause
        if isinstance(cause, IRLiteral) and cause.value is None:
            pass
        else:
            rendered = f"Py.withCause({rendered}, {emit_expression(cause, ctx).text})"
    _line(writer, ctx, f"throw {rendered};", node.span)


def _ancestry(spelled: str, ctx: JavaContext) -> list[str]:
    chain = [spelled]
    seen = {spelled}
    while True:
        current = chain[-1]
        parent = _JAVA_PARENTS.get(current)
        if parent is None and ctx.index is not None and ctx.layout is not None:
            for info in ctx.index.classes.values():
                if ctx.layout.class_names.get(info.qualified) == current:
                    if info.base and info.base in ctx.index.classes:
                        parent = ctx.layout.class_names.get(info.base)
                    elif info.builtin_base:
                        parent = EXCEPTIONS.get(info.builtin_base, "RuntimeException")
                    break
        if parent is None or parent in seen:
            return chain
        seen.add(parent)
        chain.append(parent)


def _catch_types(handler: IRCatch, ctx: JavaContext) -> list[str]:
    if not handler.exception_types:
        return list(_CATCH_EXPANSIONS["BaseException"])
    found: list[str] = []
    for item in handler.exception_types:
        if (
            isinstance(item, IRName)
            and item.name in _CATCH_EXPANSIONS
            and ctx.class_of(item.value_type) is None
        ):
            found.extend(_CATCH_EXPANSIONS[item.name])
            continue
        spelled = _exception_class(item, ctx)
        if spelled is None:
            ctx.warn(
                "CW0702",
                "exception type in an except clause could not be resolved",
                span=handler.span,
            )
            spelled = "RuntimeException"
        found.append(spelled)
    return found


def _normalise(types: list[str], caught: list[str], ctx: JavaContext) -> list[str]:
    """Drop types subsumed by another in the list or by an earlier handler."""
    kept: list[str] = []
    for spelled in dict.fromkeys(types):
        ancestors = _ancestry(spelled, ctx)[1:]
        if (
            any(ancestor in types or ancestor in caught for ancestor in ancestors)
            or spelled in caught
        ):
            continue
        kept.append(spelled)
    return kept


def _emit_handlers(handlers: list[IRCatch], writer: CodeWriter, ctx: JavaContext) -> None:
    caught: list[str] = []
    for handler in handlers:
        types = _normalise(_catch_types(handler, ctx), caught, ctx)
        if not types:
            ctx.warn(
                "CW0701",
                "this except clause can never run: an earlier one catches the same exceptions",
                span=handler.span,
            )
            continue
        caught.extend(types)
        variable = safe_identifier(handler.name) if handler.name else ctx.temp("error")
        writer.line(f"}} catch ({' | '.join(types)} {variable}) {{", handler.span)
        if handler.name:
            ctx.locals.add(handler.name)
        ctx.active_errors.append(variable)
        _body(handler.body, writer, ctx)
        ctx.active_errors.pop()


def _try(node: IRTry, writer: CodeWriter, ctx: JavaContext) -> None:
    """``try``/``except``/``else``/``finally`` with Python's ordering.

    Java's own ``finally`` matches Python's.  ``else`` runs only when the body
    completed without raising, before ``finally``, and outside the handlers,
    so it is placed after an inner ``try`` inside the outer ``try``/``finally``.
    """
    has_finally = node.finally_body is not None and not node.finally_body.is_empty
    has_else = node.else_body is not None and not node.else_body.is_empty
    if not has_else:
        _line(writer, ctx, "try {", node.span)
        _body(node.body, writer, ctx)
        _emit_handlers(node.handlers, writer, ctx)
        if has_finally:
            writer.line("} finally {")
            _body(node.finally_body, writer, ctx)
        writer.line("}")
        return
    if has_finally:
        _line(writer, ctx, "try {", node.span)
        writer.indent()
    completed = ctx.temp("completed")
    _line(writer, ctx, f"boolean {completed} = false;")
    _line(writer, ctx, "try {", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    if can_complete(node.body):
        writer.line(f"{completed} = true;")
    writer.dedent()
    _emit_handlers(node.handlers, writer, ctx)
    writer.line("}")
    writer.line(f"if ({completed}) {{")
    _body(node.else_body, writer, ctx)
    writer.line("}")
    if has_finally:
        writer.dedent()
        writer.line("} finally {")
        _body(node.finally_body, writer, ctx)
        writer.line("}")


def _delete(node: IRDelete, writer: CodeWriter, ctx: JavaContext) -> None:
    for target in node.targets:
        if isinstance(target, IRIndex):
            container = emit_expression(target.target, ctx).text
            key = emit_expression(target.index, ctx).text
            _line(writer, ctx, f"Py.pop({container}, {key});", node.span)
            continue
        name = target.name if isinstance(target, IRName) else "<expression>"
        _line(
            writer,
            ctx,
            ctx.gap("Delete", f"deleting the binding {name!r} has no Java equivalent", node.span),
            node.span,
        )


def _assert(node: IRAssert, writer: CodeWriter, ctx: JavaContext) -> None:
    test = condition(node.test, ctx)
    if node.message is None:
        message = java_string("assertion failed")
    else:
        rendered = emit_expression(node.message, ctx).text
        message_type = getattr(node.message, "value_type", None)
        message = (
            rendered
            if message_type is not None and message_type.name == "str"
            else f"Py.str({rendered})"
        )
    _line(writer, ctx, f"if (!({test})) {{", node.span)
    writer.indent()
    writer.line(f"throw new AssertionError({message});")
    writer.dedent()
    writer.line("}")


_Handler = Callable[[IRNode, CodeWriter, JavaContext], None]

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
