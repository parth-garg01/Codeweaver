"""Whole-function and whole-project facts the C-family backends share.

These answer questions that decide how efficient the generated code can be
while staying faithful:

* :func:`rebound_names` — a parameter that is reassigned cannot be passed by
  ``const`` reference;
* :class:`ParamMutability` — a container parameter that is mutated, directly or
  by passing it to a project function that mutates it, must be passed by
  mutable reference; every other container is passed by ``const`` reference
  and never copied;
* :func:`unchecked_index_loops` — inside ``for i in range(len(xs))`` where the
  body cannot change ``xs``, ``xs[i]`` is provably in bounds and may skip the
  bounds check, which lets the target compiler vectorise the loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from codeweaver.graph.project_index import ProjectIndex, ScopeKind
from codeweaver.ir.base import IRNode, walk
from codeweaver.ir.declarations import IRFunction, IRModule
from codeweaver.ir.expressions import (
    IRAttribute,
    IRCall,
    IRCompare,
    IRComprehension,
    IRIndex,
    IRLiteral,
    IRName,
    IRSlice,
)
from codeweaver.ir.operators import CompareOp
from codeweaver.ir.statements import (
    IRAssign,
    IRAugAssign,
    IRBlock,
    IRBreak,
    IRContinue,
    IRDelete,
    IRForEach,
    IRForRange,
    IRIf,
    IRReturn,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)

__all__ = [
    "MUTATING_METHODS",
    "ParamMutability",
    "can_complete",
    "escaping_loop_variables",
    "is_main_guard",
    "local_names",
    "rebound_names",
    "unchecked_index_loops",
]

#: Methods of builtin containers that change their receiver.
MUTATING_METHODS: frozenset[str] = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "remove",
        "clear",
        "sort",
        "reverse",
        "add",
        "discard",
        "update",
        "setdefault",
        "popitem",
        "difference_update",
        "intersection_update",
    }
)

#: Builtins that only read their arguments.
_READ_ONLY_BUILTINS: frozenset[str] = frozenset(
    {
        "len",
        "print",
        "str",
        "repr",
        "int",
        "float",
        "bool",
        "abs",
        "round",
        "min",
        "max",
        "sum",
        "sorted",
        "list",
        "set",
        "tuple",
        "dict",
        "isinstance",
        "any",
        "all",
        "range",
    }
)


def is_main_guard(statement: IRNode) -> bool:
    """``True`` for ``if __name__ == "__main__":``."""
    if not isinstance(statement, IRIf):
        return False
    test = statement.test
    if not isinstance(test, IRCompare) or test.op is not CompareOp.EQ:
        return False
    sides = (test.left, test.right)
    has_name = any(isinstance(side, IRName) and side.name == "__name__" for side in sides)
    has_main = any(isinstance(side, IRLiteral) and side.value == "__main__" for side in sides)
    return has_name and has_main


def rebound_names(function: IRFunction) -> set[str]:
    """Names the body assigns to as a whole binding."""
    found: set[str] = set()
    for node in walk(function.body):
        if (
            isinstance(node, IRAssign | IRAugAssign)
            and isinstance(node.target, IRName)
            or isinstance(node, IRForEach)
            and isinstance(node.target, IRName)
        ):
            found.add(node.target.name)
        elif isinstance(node, IRForRange):
            found.add(node.variable)
    return found


def local_names(function: IRFunction) -> set[str]:
    """Every name bound inside *function*: parameters, declarations, loop targets."""
    names = {param.name for param in function.params}
    receiver = function.meta.get("pythonReceiver")
    if isinstance(receiver, str) and receiver:
        names.add(receiver)
    for node in walk(function.body):
        if isinstance(node, IRVarDecl):
            names.add(node.name)
        elif isinstance(node, IRForEach) and isinstance(node.target, IRName):
            names.add(node.target.name)
        elif isinstance(node, IRForRange):
            names.add(node.variable)
        elif isinstance(node, IRAssign) and isinstance(node.target, IRName):
            names.add(node.target.name)
        elif node.kind == "catch" and getattr(node, "name", ""):
            names.add(node.name)
    return names


def escaping_loop_variables(body: IRNode) -> set[str]:
    """Loop variables that are read after (or before) the loop that binds them.

    Python's loop variable outlives its loop; C-family loop variables do not.
    A name is escaping when some reference to it lies outside every loop that
    binds it, e.g. ``for i in ...: if ...: break`` followed by ``return i``.
    """
    loops: dict[str, list[IRNode]] = {}
    for node in walk(body):
        if isinstance(node, IRForRange):
            loops.setdefault(node.variable, []).append(node)
        elif isinstance(node, IRForEach) and isinstance(node.target, IRName):
            loops.setdefault(node.target.name, []).append(node)
    if not loops:
        return set()
    references: dict[str, list[int]] = {}
    comprehensions: list[IRComprehension] = []
    for node in walk(body):
        if isinstance(node, IRName) and node.name in loops:
            references.setdefault(node.name, []).append(id(node))
        elif isinstance(node, IRComprehension) and node.target in loops:
            comprehensions.append(node)
    escaping: set[str] = set()
    for name, binders in loops.items():
        inside: set[int] = set()
        for loop in binders:
            inside.update(id(item) for item in walk(loop))
        # A comprehension binding the same name has its own scope in Python 3.
        for comprehension in comprehensions:
            if comprehension.target == name:
                for part in (comprehension.element, comprehension.key, comprehension.condition):
                    if part is not None:
                        inside.update(id(item) for item in walk(part))
        if any(reference not in inside for reference in references.get(name, ())):
            escaping.add(name)
    return escaping


def _target_root(node: IRNode) -> str:
    """The name at the root of ``xs[i]`` / ``xs[i][j]`` / ``xs[a:b]``."""
    current = node
    while isinstance(current, IRIndex | IRSlice):
        current = current.target
    return current.name if isinstance(current, IRName) else ""


def _directly_mutated(function: IRFunction) -> set[str]:
    names: set[str] = set()
    for node in walk(function.body):
        if isinstance(node, IRCall) and isinstance(node.callee, IRAttribute):
            if node.callee.name in MUTATING_METHODS and isinstance(node.callee.target, IRName):
                names.add(node.callee.target.name)
        elif isinstance(node, IRAssign | IRAugAssign) and isinstance(node.target, IRIndex):
            root = _target_root(node.target)
            if root:
                names.add(root)
        elif isinstance(node, IRDelete):
            for target in node.targets:
                root = _target_root(target)
                if root and isinstance(target, IRIndex):
                    names.add(root)
    return names


@dataclass
class ParamMutability:
    """Which parameters of which functions must be passed by mutable reference."""

    #: ``id(IRFunction)`` -> mutated parameter names.
    mutated: dict[int, set[str]] = field(default_factory=dict)

    @classmethod
    def compute(cls, index: ProjectIndex) -> ParamMutability:
        functions: list[tuple[IRModule, IRFunction, str]] = []
        for module in index.modules.values():
            for function in module.functions:
                functions.append((module, function, ""))
            for klass in module.classes:
                qualified = f"{module.name}.{klass.name}"
                for method in klass.methods:
                    functions.append((module, method, qualified))

        result = cls()
        for _, function, _ in functions:
            params = {param.name for param in function.params}
            result.mutated[id(function)] = _directly_mutated(function) & params

        # Propagate through calls until nothing changes: passing a parameter to
        # a project function that mutates that argument mutates it here too.
        changed = True
        while changed:
            changed = False
            for module, function, owner in functions:
                params = {param.name for param in function.params}
                current = result.mutated[id(function)]
                for node in walk(function.body):
                    if not isinstance(node, IRCall):
                        continue
                    callee = _resolve_callee(node, module, owner, index)
                    if callee is None:
                        continue
                    callee_mutated = result.mutated.get(id(callee), set())
                    for position, argument in enumerate(node.args):
                        if position >= len(callee.params):
                            break
                        if (
                            isinstance(argument, IRName)
                            and argument.name in params
                            and argument.name not in current
                            and callee.params[position].name in callee_mutated
                        ):
                            current.add(argument.name)
                            changed = True
                    for keyword in node.keywords:
                        if (
                            isinstance(keyword.value, IRName)
                            and keyword.value.name in params
                            and keyword.value.name not in current
                            and keyword.name in callee_mutated
                        ):
                            current.add(keyword.value.name)
                            changed = True
        return result

    def of(self, function: IRFunction) -> set[str]:
        return self.mutated.get(id(function), set())


def _resolve_callee(
    call: IRCall, module: IRModule, owner: str, index: ProjectIndex
) -> IRFunction | None:
    callee = call.callee
    if isinstance(callee, IRName):
        if callee.name in _READ_ONLY_BUILTINS:
            return None
        entry = index.lookup(module.name, callee.name)
        if entry is not None and entry.kind is ScopeKind.FUNCTION:
            return index.functions[entry.qualified].node
        return None
    if isinstance(callee, IRAttribute):
        info = index.class_for_type(callee.target.value_type, module.name)
        if info is None and owner and isinstance(callee.target, IRName):
            receiver_owner = index.classes.get(owner)
            if receiver_owner is not None and callee.target.name in ("self",):
                info = receiver_owner
        if info is not None:
            found = index.find_method(info, callee.name)
            return found[1] if found else None
    return None


def _mutates(body_nodes: Sequence[IRNode], name: str) -> bool:
    """Whether anything in *body_nodes* could change the length of *name*."""
    for root in body_nodes:
        for node in walk(root):
            if isinstance(node, IRCall):
                callee = node.callee
                if (
                    isinstance(callee, IRAttribute)
                    and isinstance(callee.target, IRName)
                    and callee.target.name == name
                    and callee.name in MUTATING_METHODS
                ):
                    return True
                if not (isinstance(callee, IRName) and callee.name in _READ_ONLY_BUILTINS):
                    if any(isinstance(arg, IRName) and arg.name == name for arg in node.args):
                        return True
            elif isinstance(node, IRAssign | IRAugAssign | IRVarDecl):
                target = getattr(node, "target", None)
                if isinstance(target, IRName) and target.name == name:
                    return True
                if isinstance(node, IRVarDecl) and node.name == name:
                    return True
            elif isinstance(node, IRDelete):
                if any(_target_root(target) == name for target in node.targets):
                    return True
    return False


def unchecked_index_loops(loop: IRForRange) -> frozenset[tuple[str, str]]:
    """``(container, index)`` pairs that are provably in bounds inside *loop*.

    Recognises ``for i in range(len(xs))`` (start 0, step 1) whose body neither
    resizes ``xs``, rebinds it, nor rebinds ``i``.
    """
    if loop.step is not None:
        literal = loop.step
        if not (isinstance(literal, IRLiteral) and literal.value == 1):
            return frozenset()
    start = loop.start
    if not (isinstance(start, IRLiteral) and start.value == 0):
        return frozenset()
    stop = loop.stop
    if not (
        isinstance(stop, IRCall)
        and isinstance(stop.callee, IRName)
        and stop.callee.name == "len"
        and len(stop.args) == 1
        and isinstance(stop.args[0], IRName)
    ):
        return frozenset()
    container = stop.args[0].name
    body = list(loop.body.statements)
    if _mutates(body, container):
        return frozenset()
    for node in body:
        for item in walk(node):
            if isinstance(item, IRAssign | IRAugAssign) and isinstance(item.target, IRName):
                if item.target.name == loop.variable:
                    return frozenset()
    return frozenset({(container, loop.variable)})


# --------------------------------------------------------------- reachability


def _breaks_out(body: IRBlock) -> bool:
    """A ``break`` in *body* that targets the enclosing loop (not a nested one)."""

    def visit(node: IRNode) -> bool:
        if isinstance(node, IRBreak):
            return True
        if isinstance(node, IRWhile | IRForEach | IRForRange):
            return False
        return any(visit(child) for child in node.children())

    return visit(body)


def can_complete(node: IRNode | None) -> bool:
    """Whether Java considers that *node* can complete normally (JLS 14.22).

    Java rejects code it can prove unreachable and C warns about it, so both
    backends use this to decide where a fall-through guard belongs."""
    if node is None:
        return True
    if isinstance(node, IRReturn | IRThrow | IRBreak | IRContinue):
        return False
    if isinstance(node, IRBlock):
        return all(can_complete(statement) for statement in node.statements)
    if isinstance(node, IRIf):
        if node.else_body is None:
            return True
        return can_complete(node.then_body) or can_complete(node.else_body)
    if isinstance(node, IRWhile):
        test = node.test
        if isinstance(test, IRLiteral) and test.value is True:
            return _breaks_out(node.body)
        return True
    if isinstance(node, IRTry):
        if (
            node.finally_body is not None
            and not node.finally_body.is_empty
            and not can_complete(node.finally_body)
        ):
            return False
        # The else body is emitted inside `if (completed) { ... }`, which Java
        # always considers able to complete; mirror the generated shape.
        return can_complete(node.body) or any(
            can_complete(handler.body) for handler in node.handlers
        )
    return True
