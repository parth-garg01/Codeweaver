"""IR statements to C statements.

* ``for``/``while`` with ``else`` use a break flag, exactly as in Python;
* ``for x in xs`` is an index loop over the list's items;
* ``raise`` prints a Python-style traceback line and exits (``cw_raise``):
  C has no exceptions, so ``try``/``except`` is reported rather than guessed
  at, while ``try``/``finally`` without handlers is exact on every path that
  does not raise;
* expressions that need statements of their own (comprehensions,
  ``sorted``) are lowered into the lines just before the statement that uses
  them (``ctx.pre``).
"""

from __future__ import annotations

from collections.abc import Callable

from codeweaver.backends.c.context import CContext, LoopFrame
from codeweaver.backends.c.expressions import condition, emit_expression, emit_store, string_of
from codeweaver.backends.c.naming import safe_identifier
from codeweaver.backends.c.types import list_kind, render_type, zero_value
from codeweaver.backends.common.analysis import unchecked_index_loops
from codeweaver.backends.common.strings import c_string_literal
from codeweaver.backends.common.writer import CodeWriter
from codeweaver.graph.project_index import BUILTIN_EXCEPTIONS
from codeweaver.ir.base import IRNode, IRUnsupported, walk
from codeweaver.ir.expressions import (
    IRAttribute,
    IRCall,
    IRComprehension,
    IRIndex,
    IRLiteral,
    IRName,
)
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
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.inference import element_type

__all__ = ["emit_block", "emit_statement", "flush", "is_super_init"]

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


def flush(writer: CodeWriter, ctx: CContext) -> None:
    """Write the statements an expression asked to have placed before it."""
    for line in ctx.pre:
        writer.line(line)
    ctx.pre.clear()


def _line(text: str, node: IRNode, writer: CodeWriter, ctx: CContext) -> None:
    flush(writer, ctx)
    writer.line(text, node.span)


def emit_block(block: IRBlock | None, writer: CodeWriter, ctx: CContext) -> None:
    if block is None:
        return
    for statement in block.statements:
        emit_statement(statement, writer, ctx)


def emit_statement(node: IRNode, writer: CodeWriter, ctx: CContext) -> None:
    if isinstance(node, IRUnsupported):
        writer.line(ctx.record_gap(node), node.span)
        return
    handler = _HANDLERS.get(type(node))
    if handler is None:
        writer.line(ctx.gap(node.kind, "no C mapping for this statement", node.span), node.span)
        return
    handler(node, writer, ctx)


def _type(node: IRNode | None) -> IRType | None:
    return getattr(node, "value_type", None)


def _expression_statement(node: IRExpressionStatement, writer: CodeWriter, ctx: CContext) -> None:
    expression = node.expression
    if isinstance(expression, IRLiteral) and isinstance(expression.value, str):
        writer.comment(expression.value, node.span)
        return
    text = emit_expression(expression, ctx).text
    if not isinstance(expression, IRCall):
        text = f"(void)({text})"
    _line(f"{text};", node, writer, ctx)


def _declare(
    name: str, var_type: IRType | None, node: IRNode, ctx: CContext
) -> tuple[str | None, str]:
    """The C spelling of *var_type*, or ``None`` and the gap marker for it."""
    spelled = render_type(var_type, ctx)
    marker = ""
    if spelled is None:
        marker = ctx.gap(
            "Assign",
            f"{name!r} has a type with no C representation in this build"
            if var_type is not None and var_type.kind is not TypeKind.UNKNOWN
            else f"the type of {name!r} is unknown",
            node.span,
            "annotate the variable; dict, set and tuple values are not translated to C",
        )
    return spelled, marker


def _var_decl(node: IRVarDecl, writer: CodeWriter, ctx: CContext) -> None:
    ctx.locals.add(node.name)
    name = safe_identifier(node.name)
    spelled, marker = _declare(node.name, node.var_type, node, ctx)
    if spelled is None:
        writer.line(marker, node.span)
        return
    if node.value is None:
        writer.line(f"{spelled} {name} = {zero_value(spelled)};", node.span)
        return
    value = emit_expression(node.value, ctx, node.var_type).text
    _line(f"{spelled} {name} = {value};", node, writer, ctx)


def _assign(node: IRAssign, writer: CodeWriter, ctx: CContext) -> None:
    target = node.target
    if isinstance(target, IRName) and target.name not in ctx.locals and ctx.function is not None:
        ctx.locals.add(target.name)
        spelled, marker = _declare(target.name, _type(node.value), node, ctx)
        if spelled is None:
            writer.line(marker, node.span)
            return
        value = emit_expression(node.value, ctx).text
        _line(f"{spelled} {safe_identifier(target.name)} = {value};", node, writer, ctx)
        return
    value = emit_expression(node.value, ctx, _type(target)).text
    _line(f"{emit_store(target, value, ctx)};", node, writer, ctx)


def _is_pure(node: IRNode) -> bool:
    return not any(isinstance(item, IRCall | IRComprehension) for item in walk(node))


def _aug_assign(node: IRAugAssign, writer: CodeWriter, ctx: CContext) -> None:
    target = node.target
    target_type = _type(target)
    is_str = (
        target_type is not None
        and target_type.name == "str"
        and target_type.kind is TypeKind.PRIMITIVE
    )
    is_list = (
        target_type is not None
        and target_type.kind is TypeKind.COLLECTION
        and target_type.name == "list"
    )
    if isinstance(target, IRIndex) and not _is_pure(target):
        writer.line(
            ctx.gap(
                "AugAssign",
                "augmented assignment to an item whose index has side effects",
                node.span,
            ),
            node.span,
        )
        return
    current = emit_expression(target, ctx).text
    value = emit_expression(node.value, ctx, target_type).text
    if is_list and node.op is BinaryOp.ADD:
        kind = list_kind(element_type(target_type), ctx)
        _line(f"cw_list_{kind}_extend({current}, {value});", node, writer, ctx)
        return
    both_int = target_type is not None and target_type.name in ("int", "bool")
    runtime = {
        BinaryOp.DIV: "cw_truediv",
        BinaryOp.FLOOR_DIV: "cw_floordiv_i64" if both_int else "cw_floordiv_f64",
        BinaryOp.MOD: "cw_mod_i64" if both_int else "cw_mod_f64",
        BinaryOp.POW: "cw_pow_i64" if both_int else "pow",
    }.get(node.op)
    if is_str and node.op is BinaryOp.ADD:
        runtime = "cw_str_concat"
    if node.op is BinaryOp.DIV and both_int:
        ctx.warn(
            "CW0690",
            "'/=' turns an int into a float in Python, but the C variable keeps its integer type",
            span=node.span,
            hint="declare the variable as float",
        )
    if runtime is not None:
        _line(f"{emit_store(target, f'{runtime}({current}, {value})', ctx)};", node, writer, ctx)
        return
    spelling = _AUG_SPELLING.get(node.op)
    if spelling is None or is_str or is_list:
        writer.line(
            ctx.gap(
                "AugAssign",
                f"augmented operator {node.op.value!r} has no C spelling for this type",
                node.span,
            ),
            node.span,
        )
        return
    if isinstance(target, IRIndex):
        operator = spelling[:-1]
        _line(f"{emit_store(target, f'{current} {operator} ({value})', ctx)};", node, writer, ctx)
        return
    _line(f"{current} {spelling} {value};", node, writer, ctx)


def _return(node: IRReturn, writer: CodeWriter, ctx: CContext) -> None:
    function = ctx.function
    if node.value is None or (function is not None and function.is_constructor):
        if function is not None and function.name == "__main__":
            writer.line("return 0;", node.span)
        else:
            writer.line("return;", node.span)
        return
    expected = function.return_type if function is not None else None
    _line(f"return {emit_expression(node.value, ctx, expected).text};", node, writer, ctx)


def _if(node: IRIf, writer: CodeWriter, ctx: CContext) -> None:
    _line(f"if ({condition(node.test, ctx)}) {{", node, writer, ctx)
    writer.indent()
    emit_block(node.then_body, writer, ctx)
    writer.dedent()
    else_body = node.else_body
    closers = 1
    while else_body is not None:
        if len(else_body.statements) == 1 and isinstance(else_body.statements[0], IRIf):
            inner = else_body.statements[0]
            test = condition(inner.test, ctx)
            if ctx.pre:
                # The elif test needs statements: they belong inside the else.
                writer.line("} else {")
                writer.indent()
                flush(writer, ctx)
                writer.line(f"if ({test}) {{", inner.span)
                closers += 1
            else:
                writer.line(f"}} else if ({test}) {{", inner.span)
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
    for depth in range(closers):
        writer.line("}")
        if depth < closers - 1:
            writer.dedent()


def _loop(
    header: str,
    body: IRBlock,
    else_body: IRBlock | None,
    node: IRNode,
    writer: CodeWriter,
    ctx: CContext,
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


def _while(node: IRWhile, writer: CodeWriter, ctx: CContext) -> None:
    test = condition(node.test, ctx)
    if ctx.pre:
        # The test needs statements of its own, which must run on every pass.
        lines = list(ctx.pre)
        ctx.pre.clear()
        prelude = [*lines, f"if (!({test})) break;"]
        if node.else_body is not None and not node.else_body.is_empty:
            writer.line(
                ctx.gap("While", "while/else whose test needs a temporary", node.span), node.span
            )
            return
        _loop("for (;;)", node.body, None, node, writer, ctx, prelude)
        return
    _loop(f"while ({test})", node.body, node.else_body, node, writer, ctx)


def _declare_escaping(name: str, spelled: str, writer: CodeWriter, ctx: CContext) -> None:
    if name not in ctx.declared:
        ctx.declared.add(name)
        writer.line(
            f"{spelled} {safe_identifier(name)} = {zero_value(spelled)};  /* outlives the loop, as in Python */"
        )


def _for_each(node: IRForEach, writer: CodeWriter, ctx: CContext) -> None:
    target = node.target
    if not isinstance(target, IRName):
        writer.line(
            ctx.gap("For", "only a single name can be a loop target in C", node.span), node.span
        )
        return
    iterable_type = _type(node.iterable)
    is_str = (
        iterable_type is not None
        and iterable_type.kind is TypeKind.PRIMITIVE
        and iterable_type.name == "str"
    )
    kind = None if is_str else list_kind(element_type(iterable_type), ctx)
    is_list = (
        iterable_type is not None
        and iterable_type.kind is TypeKind.COLLECTION
        and iterable_type.name == "list"
    )
    if not is_str and (kind is None or not is_list):
        writer.line(
            ctx.gap(
                "For",
                "only lists and strings can be iterated in C",
                node.span,
                "iterate over a list",
            ),
            node.span,
        )
        return
    element_c = "cw_str" if is_str else render_type(element_type(iterable_type), ctx)
    assert element_c is not None
    iterable = emit_expression(node.iterable, ctx).text
    flush(writer, ctx)
    ctx.locals.add(target.name)
    name = safe_identifier(target.name)
    source = ctx.temp("source")
    index = ctx.temp("i")
    if is_str:
        writer.line(f"cw_str {source} = {iterable};", node.span)
        header = f"for (int64_t {index} = 0; {source}[{index}] != '\\0'; ++{index})"
        item = f"cw_strdup_n({source} + {index}, 1)"
    else:
        writer.line(f"cw_list_{kind}* {source} = {iterable};", node.span)
        header = f"for (int64_t {index} = 0; {index} < {source}->len; ++{index})"
        item = f"{source}->items[{index}]"
        if kind == "ptr":
            item = f"({element_c})({item})"
    if target.name in ctx.escaping:
        _declare_escaping(target.name, element_c, writer, ctx)
        prelude = [f"{name} = {item};"]
    else:
        prelude = [f"{element_c} {name} = {item};"]
    _loop(header, node.body, node.else_body, node, writer, ctx, prelude)


def _for_range(node: IRForRange, writer: CodeWriter, ctx: CContext) -> None:
    ctx.locals.add(node.variable)
    name = safe_identifier(node.variable)
    visible = ""
    if node.variable in ctx.escaping:
        _declare_escaping(node.variable, "int64_t", writer, ctx)
        visible = name
        name = ctx.temp("i")
    start = emit_expression(node.start, ctx).text
    stop = emit_expression(node.stop, ctx).text
    step_node = node.step
    previous = ctx.unchecked
    ctx.unchecked = previous | unchecked_index_loops(node)
    bound = ctx.temp("stop")
    flush(writer, ctx)
    if step_node is None or (isinstance(step_node, IRLiteral) and step_node.value == 1):
        writer.line(f"const int64_t {bound} = {stop};", node.span)
        header = f"for (int64_t {name} = {start}; {name} < {bound}; ++{name})"
    elif isinstance(step_node, IRLiteral) and isinstance(step_node.value, int):
        comparison = "<" if step_node.value > 0 else ">"
        writer.line(f"const int64_t {bound} = {stop};", node.span)
        header = f"for (int64_t {name} = {start}; {name} {comparison} {bound}; {name} += {step_node.value})"
    else:
        step = ctx.temp("step")
        writer.line(f"const int64_t {step} = {emit_expression(step_node, ctx).text};", node.span)
        writer.line(f"const int64_t {bound} = {stop};")
        writer.line(f'if ({step} == 0) cw_raise("ValueError", "range() arg 3 must not be zero");')
        header = f"for (int64_t {name} = {start}; {step} > 0 ? {name} < {bound} : {name} > {bound}; {name} += {step})"
    if visible:
        ctx.unchecked = previous
    _loop(header, node.body, None, node, writer, ctx, [f"{visible} = {name};"] if visible else None)
    ctx.unchecked = previous


def _break(node: IRBreak, writer: CodeWriter, ctx: CContext) -> None:
    frame = ctx.loops[-1] if ctx.loops else None
    if frame is not None and frame.broke:
        writer.line(f"{frame.broke} = true;", node.span)
    writer.line("break;", node.span)


def _continue(node: IRContinue, writer: CodeWriter, ctx: CContext) -> None:
    writer.line("continue;", node.span)


def _noop(node: IRNoOp, writer: CodeWriter, ctx: CContext) -> None:
    writer.line("/* pass */", node.span)


def _exception_name(value: IRNode, ctx: CContext) -> tuple[str, IRCall | None] | None:
    call = value if isinstance(value, IRCall) else None
    callee = call.callee if call is not None else value
    if isinstance(callee, IRName):
        entry = ctx.scope_entry(callee.name)
        if entry is None and callee.name in BUILTIN_EXCEPTIONS:
            return callee.name, call
        if entry is not None and ctx.index is not None and entry.qualified in ctx.index.classes:
            info = ctx.index.classes[entry.qualified]
            if info.is_exception:
                return info.name, call
    return None


def _throw(node: IRThrow, writer: CodeWriter, ctx: CContext) -> None:
    if node.value is None:
        writer.line(
            ctx.gap("Raise", "a bare 'raise' has no active exception to re-raise in C", node.span),
            node.span,
        )
        return
    found = _exception_name(node.value, ctx)
    if found is None:
        writer.line(
            ctx.gap(
                "Raise", "only raising an exception class by name is translated to C", node.span
            ),
            node.span,
        )
        return
    name, call = found
    message = '""'
    if call is not None and call.args:
        converted = string_of(call.args[0], ctx)
        if converted is None:
            ctx.warn("CW0692", "exception message of unknown type is not shown", span=node.span)
        else:
            message = converted
    _line(f"cw_raise({c_string_literal(name, trigraphs=True)}, {message});", node, writer, ctx)


def _try(node: IRTry, writer: CodeWriter, ctx: CContext) -> None:
    if node.handlers or (node.else_body is not None and not node.else_body.is_empty):
        writer.line(
            ctx.gap(
                "Try",
                "C has no exceptions: try/except cannot be translated, and every raise ends the program",
                node.span,
                "check for the error condition explicitly before it happens",
            ),
            node.span,
        )
        return
    ctx.warn(
        "CW0693",
        "try/finally: the finally body runs when the try body completes or returns early only "
        "through normal flow; a raise ends the program without running it",
        span=node.span,
    )
    if any(isinstance(item, IRReturn | IRBreak | IRContinue) for item in walk(node.body)):
        writer.line(
            ctx.gap(
                "Try", "try/finally whose body leaves early (return/break/continue)", node.span
            ),
            node.span,
        )
        return
    writer.line("{  /* try */", node.span)
    writer.indent()
    emit_block(node.body, writer, ctx)
    writer.dedent()
    writer.line("}")
    writer.line("{  /* finally */")
    writer.indent()
    emit_block(node.finally_body, writer, ctx)
    writer.dedent()
    writer.line("}")


def _delete(node: IRDelete, writer: CodeWriter, ctx: CContext) -> None:
    for target in node.targets:
        if isinstance(target, IRIndex):
            kind = list_kind(element_type(_type(target.target)), ctx)
            container_type = _type(target.target)
            if kind is not None and container_type is not None and container_type.name == "list":
                container = emit_expression(target.target, ctx).text
                key = emit_expression(target.index, ctx).text
                _line(f"(void)cw_list_{kind}_pop({container}, {key});", node, writer, ctx)
                continue
        name = target.name if isinstance(target, IRName) else "<expression>"
        writer.line(
            ctx.gap("Delete", f"deleting {name!r} has no C equivalent", node.span), node.span
        )


def _assert(node: IRAssert, writer: CodeWriter, ctx: CContext) -> None:
    message = '""'
    if node.message is not None:
        converted = string_of(node.message, ctx)
        message = converted if converted is not None else '"assertion failed"'
    test = condition(node.test, ctx)
    _line(f'if (!({test})) cw_raise("AssertionError", {message});', node, writer, ctx)


def _comment(node: IRComment, writer: CodeWriter, ctx: CContext) -> None:
    writer.comment(node.text, node.span)


def _nested_block(node: IRBlock, writer: CodeWriter, ctx: CContext) -> None:
    emit_block(node, writer, ctx)


def is_super_init(statement: IRNode) -> IRCall | None:
    if not isinstance(statement, IRExpressionStatement):
        return None
    call = statement.expression
    if (
        not isinstance(call, IRCall)
        or not isinstance(call.callee, IRAttribute)
        or call.callee.name != "__init__"
    ):
        return None
    target = call.callee.target
    if (
        isinstance(target, IRCall)
        and isinstance(target.callee, IRName)
        and target.callee.name == "super"
    ):
        return call
    return None


_Handler = Callable[[IRNode, CodeWriter, CContext], None]

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
