"""Hoist declarations whose Python scope is wider than the target's block scope.

Python scopes names to the function: a variable first assigned inside an
``if`` is still bound after it.  Every bundled target scopes ``let``, local
variables and ``auto`` to the enclosing block, so a faithful translation of::

    if ok:
        label = "yes"
    else:
        label = "no"
    return label

needs the declaration lifted above the ``if``.  This pass finds declarations
nested inside a compound statement whose name is also used outside that block,
moves the declaration (without a value) to the top of the function, and turns
the original into an assignment.  Declarations that never escape their block
are left where they are, so the output stays as close to the source as the
target allows.

Hoisted declarations carry ``meta["hoisted"] = True``; backends initialise them
to the type's zero value where the target requires definite assignment.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.base import IRNode, walk
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule
from codeweaver.ir.expressions import IRName
from codeweaver.ir.statements import (
    IRAssign,
    IRBlock,
    IRCatch,
    IRForEach,
    IRForRange,
    IRIf,
    IRStatement,
    IRTry,
    IRVarDecl,
    IRWhile,
)

__all__ = ["HOISTED", "HoistDeclarationsPass", "hoist_declarations"]

HOISTED = "hoisted"


def _child_blocks(statement: IRNode) -> list[IRBlock]:
    blocks: list[IRBlock | None] = []
    if isinstance(statement, IRIf):
        blocks = [statement.then_body, statement.else_body]
    elif isinstance(statement, IRWhile | IRForEach):
        blocks = [statement.body, statement.else_body]
    elif isinstance(statement, IRForRange):
        blocks = [statement.body]
    elif isinstance(statement, IRTry):
        blocks = [statement.body, *(handler.body for handler in statement.handlers)]
        blocks += [statement.else_body, statement.finally_body]
    elif isinstance(statement, IRBlock):
        blocks = [statement]
    return [block for block in blocks if block is not None]


def _names_in(node: IRNode) -> set[str]:
    found: set[str] = set()
    for item in walk(node):
        if isinstance(item, (IRName, IRVarDecl)):
            found.add(item.name)
        elif isinstance(item, IRForRange):
            found.add(item.variable)
        elif isinstance(item, IRCatch) and item.name:
            found.add(item.name)
    return found


def _escaping_declarations(body: IRBlock) -> dict[int, IRVarDecl]:
    """Nested declarations whose name is used outside their own block."""
    escaping: dict[int, IRVarDecl] = {}

    def visit(block: IRBlock, outside: list[IRNode], depth: int) -> None:
        for statement in block.statements:
            if isinstance(statement, IRVarDecl) and depth > 0:
                # Uses later in the same block are already in scope; only uses
                # outside this block need the declaration moved.
                used_elsewhere = any(statement.name in _names_in(node) for node in outside)
                if used_elsewhere:
                    escaping[id(statement)] = statement
            for child in _child_blocks(statement):
                # Everything in the function except this child block is
                # "outside" it.
                others: list[IRNode] = [s for s in block.statements if s is not statement]
                others += [b for b in _child_blocks(statement) if b is not child]
                if isinstance(statement, IRIf | IRWhile):
                    others.append(statement.test)
                elif isinstance(statement, IRForEach):
                    others += [statement.target, statement.iterable]
                visit(child, [*outside, *others], depth + 1)

    visit(body, [], 0)
    return escaping


def _rewrite_block(block: IRBlock, hoist: dict[int, IRVarDecl]) -> IRBlock:
    statements: list[IRStatement] = []
    changed = False
    for statement in block.statements:
        rebuilt = _rewrite_statement(statement, hoist)
        if rebuilt is not statement:
            changed = True
        if rebuilt is not None:
            statements.append(rebuilt)
    return replace(block, statements=statements) if changed else block


def _rewrite_statement(statement: IRStatement, hoist: dict[int, IRVarDecl]) -> IRStatement | None:
    if isinstance(statement, IRVarDecl) and id(statement) in hoist:
        if statement.value is None:
            return None
        target = IRName(statement.name, value_type=statement.var_type, span=statement.span)
        return IRAssign(target, statement.value, span=statement.span)
    if isinstance(statement, IRIf):
        then_body = _rewrite_block(statement.then_body, hoist)
        else_body = _rewrite_block(statement.else_body, hoist) if statement.else_body else None
        if then_body is statement.then_body and else_body is statement.else_body:
            return statement
        return replace(statement, then_body=then_body, else_body=else_body)
    if isinstance(statement, IRWhile | IRForEach):
        body = _rewrite_block(statement.body, hoist)
        else_body = _rewrite_block(statement.else_body, hoist) if statement.else_body else None
        if body is statement.body and else_body is statement.else_body:
            return statement
        return replace(statement, body=body, else_body=else_body)
    if isinstance(statement, IRForRange):
        body = _rewrite_block(statement.body, hoist)
        return statement if body is statement.body else replace(statement, body=body)
    if isinstance(statement, IRTry):
        body = _rewrite_block(statement.body, hoist)
        handlers = [
            replace(handler, body=_rewrite_block(handler.body, hoist))
            for handler in statement.handlers
        ]
        else_body = _rewrite_block(statement.else_body, hoist) if statement.else_body else None
        final = _rewrite_block(statement.finally_body, hoist) if statement.finally_body else None
        return replace(
            statement, body=body, handlers=handlers, else_body=else_body, finally_body=final
        )
    if isinstance(statement, IRBlock):
        return _rewrite_block(statement, hoist)
    return statement


def _hoist_function(function: IRFunction, bag: DiagnosticBag | None) -> IRFunction:
    escaping = _escaping_declarations(function.body)
    if not escaping:
        return function
    seen: set[str] = set()
    hoisted: list[IRStatement] = []
    for declaration in escaping.values():
        if declaration.name in seen:
            continue
        seen.add(declaration.name)
        hoisted.append(
            IRVarDecl(
                declaration.name,
                declaration.var_type,
                None,
                span=declaration.span,
                meta={**declaration.meta, HOISTED: True},
            )
        )
        if bag is not None:
            bag.info(
                "CW0720",
                f"declaration of {declaration.name!r} was hoisted to function scope "
                "because it is used outside the block that declares it",
                stage="transform",
                span=declaration.span,
            )
    body = _rewrite_block(function.body, escaping)
    return replace(function, body=replace(body, statements=[*hoisted, *body.statements]))


def hoist_declarations(module: IRModule, bag: DiagnosticBag | None = None) -> IRModule:
    functions = [_hoist_function(function, bag) for function in module.functions]
    classes: list[IRClass] = []
    for klass in module.classes:
        methods = [_hoist_function(method, bag) for method in klass.methods]
        changed = any(new is not old for new, old in zip(methods, klass.methods, strict=True))
        classes.append(replace(klass, methods=methods) if changed else klass)
    return replace(module, functions=functions, classes=classes)


@dataclass
class HoistDeclarationsPass:
    """Transform pass wrapper for :func:`hoist_declarations`."""

    name: str = "hoist-block-declarations"
    description: str = "Lift declarations used outside their block to function scope"
    targets: tuple[str, ...] = ()

    def applies_to(self, module: IRModule) -> bool:
        return bool(module.functions or module.classes)

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        return hoist_declarations(module, bag)
