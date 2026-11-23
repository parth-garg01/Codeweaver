"""Flow-ordered type inference for statically typed targets.

:mod:`codeweaver.transforms.types` copies *declared* types onto references.
That is enough for TypeScript, whose compiler infers the rest.  C, C++ and Java
need to know more while generating code: whether ``account.owner`` is reached
through a pointer, what a loop variable's element type is, what a call returns,
whether ``len(x)`` measures a string or a list.

This pass walks every function body in source order with a Python-style
function scope (one scope per function, plus a nested scope per comprehension
or lambda) and fills in ``value_type`` wherever it is still unknown:

* names, from parameters, declarations, loop targets, handlers and module
  scope, including names imported from other modules;
* attributes, from class fields and properties resolved through the project
  class hierarchy, and from module members;
* calls, from project function and method signatures, class construction and a
  table of builtin and builtin-method result types;
* operators, literals, comprehensions, conditionals and indexing.

Two rules keep it safe.  A type the frontend recorded is never replaced by a
less specific one, and nothing is guessed: an expression whose type cannot be
derived stays ``unknown`` and the backend reports it.  Class types are
*qualified* on the way through — the IR type carries the defining module — so
a backend in another module can still find the class.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.graph.project_index import (
    BUILTIN_EXCEPTIONS,
    QUALIFIED,
    ClassInfo,
    ProjectIndex,
    ScopeKind,
)
from codeweaver.ir.base import IRNode, IRUnsupported
from codeweaver.ir.declarations import IRClass, IRField, IRFunction, IRModule
from codeweaver.ir.expressions import (
    IRArrayLiteral,
    IRAttribute,
    IRBinary,
    IRCall,
    IRCompare,
    IRComprehension,
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
from codeweaver.ir.operators import BinaryOp, UnaryOp
from codeweaver.ir.statements import (
    IRAssert,
    IRAssign,
    IRAugAssign,
    IRBlock,
    IRCatch,
    IRDelete,
    IRExpressionStatement,
    IRForEach,
    IRForRange,
    IRIf,
    IRReturn,
    IRStatement,
    IRThrow,
    IRTry,
    IRVarDecl,
    IRWhile,
)
from codeweaver.ir.types import (
    IRType,
    TypeKind,
    t_bool,
    t_callable,
    t_dict,
    t_float,
    t_int,
    t_list,
    t_none,
    t_optional,
    t_set,
    t_str,
    t_tuple,
    t_unknown,
)

__all__ = [
    "CLASS_REF",
    "EXCEPTION_REF",
    "METHOD_OF",
    "MODULE_REF",
    "TypeInferencePass",
    "element_type",
    "infer_module_types",
    "is_known",
]

#: ``IRType.meta`` key: the expression names a class, not an instance.
CLASS_REF = "classRef"
#: ``IRType.meta`` key: the expression names a project module.
MODULE_REF = "moduleRef"
#: ``IRType.meta`` key: a bound method; the value is the qualified class name.
METHOD_OF = "methodOf"
#: ``IRType.meta`` key: the expression names a builtin exception class.
EXCEPTION_REF = "exceptionRef"
#: ``IRType.meta`` key: the value came from ``range(...)``.
RANGE = "range"


def _vt(node: object) -> IRType:
    """The value type of *node*, or unknown for nodes that carry none."""
    found = getattr(node, "value_type", None)
    return found if isinstance(found, IRType) else t_unknown()


def is_known(ir_type: IRType | None) -> bool:
    return ir_type is not None and ir_type.kind is not TypeKind.UNKNOWN


def _with_meta(ir_type: IRType, **meta: Any) -> IRType:
    merged = dict(ir_type.meta)
    merged.update(meta)
    return IRType(
        ir_type.kind, ir_type.name, ir_type.args, ir_type.source_text, ir_type.nullable, merged
    )


def _is_numeric(ir_type: IRType | None) -> bool:
    return bool(
        ir_type and ir_type.kind is TypeKind.PRIMITIVE and ir_type.name in ("int", "float", "bool")
    )


def _is(ir_type: IRType | None, name: str) -> bool:
    return bool(
        ir_type
        and ir_type.name == name
        and ir_type.kind in (TypeKind.PRIMITIVE, TypeKind.COLLECTION)
    )


def element_type(container: IRType | None) -> IRType:
    """The type a ``for`` loop over *container* binds."""
    if container is None:
        return t_unknown()
    if container.kind is TypeKind.OPTIONAL and container.args:
        return element_type(container.args[0])
    if container.kind is TypeKind.PRIMITIVE and container.name == "str":
        return t_str()
    if container.kind is TypeKind.PRIMITIVE and container.name == "bytes":
        return t_int()
    if container.kind is TypeKind.COLLECTION:
        if container.name in ("list", "set", "dict") and container.args:
            return container.args[0]
        if container.name == "tuple" and container.args:
            first = container.args[0]
            if all(arg == first for arg in container.args):
                return first
    return t_unknown()


def _better(old: IRType | None, new: IRType | None) -> bool:
    """Whether *new* is strictly more informative than *old*."""
    if not is_known(new):
        return False
    if not is_known(old):
        return True
    assert old is not None and new is not None
    if old.kind is TypeKind.COLLECTION and new.kind is TypeKind.COLLECTION and old.name == new.name:
        return not all(is_known(arg) for arg in old.args) and all(is_known(arg) for arg in new.args)
    if old.kind is TypeKind.OBJECT and new.kind is TypeKind.OBJECT and old.name == new.name:
        return QUALIFIED not in old.meta and QUALIFIED in new.meta
    return False


def _unify(types: list[IRType]) -> IRType:
    known = [item for item in types if is_known(item)]
    if not known or len(known) != len(types):
        return t_unknown()
    first = known[0]
    if all(item == first for item in known):
        return first
    if all(_is_numeric(item) for item in known):
        return t_float() if any(item.name == "float" for item in known) else t_int()
    non_none = [item for item in known if item.kind is not TypeKind.NONE]
    if non_none and len(non_none) < len(known) and all(item == non_none[0] for item in non_none):
        return t_optional(non_none[0])
    return t_unknown()


#: Builtin name -> how its result type is derived from the argument types.
def _builtin_result(name: str, args: list[IRType]) -> IRType | None:
    first = args[0] if args else None
    if name in ("len", "ord", "hash", "id"):
        return t_int()
    if name in ("str", "repr", "chr", "input", "format"):
        return t_str()
    if name == "int":
        return t_int()
    if name == "float":
        return t_float()
    if name in ("bool", "isinstance", "issubclass", "callable", "any", "all"):
        return t_bool()
    if name == "abs":
        return first if _is_numeric(first) else None
    if name == "round":
        return t_int() if len(args) == 1 else t_float()
    if name in ("min", "max"):
        if len(args) == 1:
            return element_type(first)
        return _unify(args) if args else None
    if name == "sum":
        item = element_type(first)
        return item if _is_numeric(item) else t_int() if not is_known(item) else None
    if name in ("sorted", "list"):
        return t_list(element_type(first)) if args else t_list()
    if name == "set":
        return t_set(element_type(first)) if args else t_set()
    if name == "dict":
        return first if _is(first, "dict") else t_dict()
    if name == "tuple":
        return t_tuple()
    if name == "range":
        return _with_meta(t_list(t_int()), **{RANGE: True})
    if name == "print":
        return t_none()
    if name == "divmod":
        return t_tuple(t_int(), t_int())
    if name == "pow":
        return _unify(args[:2]) if len(args) >= 2 else None
    return None


_STR_TO_STR = frozenset(
    {
        "upper",
        "lower",
        "strip",
        "lstrip",
        "rstrip",
        "replace",
        "title",
        "capitalize",
        "casefold",
        "swapcase",
        "format",
        "join",
        "center",
        "ljust",
        "rjust",
        "zfill",
        "removeprefix",
        "removesuffix",
    }
)
_STR_TO_BOOL = frozenset(
    {
        "startswith",
        "endswith",
        "isdigit",
        "isalpha",
        "isalnum",
        "isspace",
        "isupper",
        "islower",
        "isnumeric",
        "isdecimal",
        "isidentifier",
        "istitle",
    }
)
_STR_TO_INT = frozenset({"find", "rfind", "index", "rindex", "count"})


def _method_result(receiver: IRType | None, name: str) -> IRType | None:
    """Result type of a method called on a builtin value."""
    if receiver is None:
        return None
    if receiver.kind is TypeKind.PRIMITIVE and receiver.name == "str":
        if name in _STR_TO_STR:
            return t_str()
        if name in ("split", "rsplit", "splitlines"):
            return t_list(t_str())
        if name in _STR_TO_BOOL:
            return t_bool()
        if name in _STR_TO_INT:
            return t_int()
        if name == "encode":
            return IRType(TypeKind.PRIMITIVE, "bytes", source_text="bytes")
        return None
    if receiver.kind is not TypeKind.COLLECTION:
        return None
    item = element_type(receiver)
    if receiver.name == "list":
        if name in ("append", "extend", "insert", "remove", "clear", "sort", "reverse"):
            return t_none()
        if name == "pop":
            return item
        if name in ("index", "count"):
            return t_int()
        if name == "copy":
            return receiver
    if receiver.name == "dict":
        key = receiver.args[0] if receiver.args else t_unknown()
        value = receiver.args[1] if len(receiver.args) > 1 else t_unknown()
        if name == "get":
            return value
        if name == "keys":
            return t_list(key)
        if name == "values":
            return t_list(value)
        if name == "items":
            return t_list(t_tuple(key, value))
        if name in ("pop", "setdefault"):
            return value
        if name == "copy":
            return receiver
        if name in ("update", "clear"):
            return t_none()
    if receiver.name == "set":
        if name in ("add", "remove", "discard", "clear", "update"):
            return t_none()
        if name in ("union", "intersection", "difference", "symmetric_difference", "copy"):
            return receiver
        if name == "pop":
            return item
        if name in ("issubset", "issuperset", "isdisjoint"):
            return t_bool()
    return None


@dataclass
class _Scope:
    """One function's bindings, chained to an enclosing scope for lambdas."""

    names: dict[str, IRType] = field(default_factory=dict)
    parent: _Scope | None = None

    def get(self, name: str) -> IRType | None:
        scope: _Scope | None = self
        while scope is not None:
            found = scope.names.get(name)
            if found is not None:
                return found
            scope = scope.parent
        return None

    def bind(self, name: str, ir_type: IRType) -> None:
        current = self.names.get(name)
        if current is None or _better(current, ir_type):
            self.names[name] = ir_type

    def child(self) -> _Scope:
        return _Scope(parent=self)


class _Inferrer:
    def __init__(self, index: ProjectIndex, module: IRModule, bag: DiagnosticBag | None) -> None:
        self.index = index
        self.module = module
        self.bag = bag
        self.klass: ClassInfo | None = None
        self.receiver = ""
        self.class_receiver = ""
        self.returns: list[IRType] | None = None

    # ------------------------------------------------------------ declarations

    def qualify(self, ir_type: IRType) -> IRType:
        return self.index.qualify(ir_type, self.module.name)

    def run(self) -> IRModule:
        module_scope = _Scope()
        body = [self.statement(statement, module_scope) for statement in self.module.body]
        functions = [self.function(function, None) for function in self.module.functions]
        classes = [self.class_(klass) for klass in self.module.classes]
        return replace(self.module, body=body, functions=functions, classes=classes)

    def class_(self, klass: IRClass) -> IRClass:
        info = self.index.classes.get(f"{self.module.name}.{klass.name}")
        previous = self.klass
        self.klass = info
        fields: list[IRField] = []
        for item in klass.fields:
            default = self.expression(item.default, _Scope()) if item.default is not None else None
            declared = self.qualify(item.field_type)
            if not is_known(declared) and default is not None and is_known(_vt(default)):
                declared = _vt(default)
            fields.append(replace(item, field_type=declared, default=default))
        methods = [self.function(method, info) for method in klass.methods]
        self.klass = previous
        return replace(klass, fields=fields, methods=methods)

    def function(self, function: IRFunction, info: ClassInfo | None) -> IRFunction:
        scope = _Scope()
        params: list[IRParam] = []
        for param in function.params:
            declared = self.qualify(param.param_type)
            default = self.expression(param.default, scope) if param.default is not None else None
            if not is_known(declared) and default is not None:
                candidate = _vt(default)
                if is_known(candidate) and candidate.kind is not TypeKind.NONE:
                    declared = candidate
            if param.style == "var_positional":
                bound = t_list(declared) if is_known(declared) else t_list()
            elif param.style == "var_keyword":
                bound = t_dict(t_str(), declared)
            else:
                bound = declared
            scope.bind(param.name, bound)
            params.append(replace(param, param_type=declared, default=default))

        previous = (self.receiver, self.class_receiver, self.returns)
        self.receiver = ""
        self.class_receiver = ""
        receiver = str(function.meta.get("pythonReceiver", ""))
        if info is not None and receiver:
            if function.meta.get("pythonClassMethod"):
                self.class_receiver = receiver
                scope.bind(
                    receiver, _with_meta(self.index.instance_type(info), **{CLASS_REF: True})
                )
            else:
                self.receiver = receiver
                scope.bind(receiver, self.index.instance_type(info))
        self.returns = []
        body = self.block(function.body, scope)
        returns = self.qualify(function.return_type)
        if not is_known(returns) and not function.is_constructor:
            observed = self.returns
            if not observed:
                returns = t_none()
            else:
                unified = _unify(observed)
                if is_known(unified):
                    returns = unified
            if is_known(returns):
                function = replace(function, meta={**function.meta, "inferredReturn": True})
        self.receiver, self.class_receiver, self.returns = previous
        return replace(function, params=params, body=body, return_type=returns)

    # -------------------------------------------------------------- statements

    def block(self, block: IRBlock | None, scope: _Scope) -> IRBlock:
        if block is None:
            return IRBlock()
        statements = [self.statement(statement, scope) for statement in block.statements]
        return replace(block, statements=statements)

    def optional_block(self, block: IRBlock | None, scope: _Scope) -> IRBlock | None:
        return None if block is None else self.block(block, scope)

    def statement(self, node: IRStatement, scope: _Scope) -> IRStatement:
        if isinstance(node, IRVarDecl):
            value = self.expression(node.value, scope) if node.value is not None else None
            declared = self.qualify(node.var_type)
            meta = node.meta
            if not is_known(declared) and value is not None and is_known(_vt(value)):
                declared = _vt(value)
                meta = {**meta, "inferredType": True}
            elif is_known(declared) and value is not None and _better(declared, _vt(value)):
                declared = _vt(value)
            scope.bind(node.name, declared)
            return replace(node, var_type=declared, value=value, meta=meta)
        if isinstance(node, IRAssign):
            value = self.expression(node.value, scope)
            target = self.expression(node.target, scope)
            if isinstance(target, IRName):
                scope.bind(target.name, _vt(value))
            return replace(node, target=target, value=value)
        if isinstance(node, IRAugAssign):
            return replace(
                node,
                target=self.expression(node.target, scope),
                value=self.expression(node.value, scope),
            )
        if isinstance(node, IRExpressionStatement):
            return replace(node, expression=self.expression(node.expression, scope))
        if isinstance(node, IRReturn):
            if node.value is None:
                if self.returns is not None:
                    self.returns.append(t_none())
                return node
            value = self.expression(node.value, scope)
            if self.returns is not None:
                self.returns.append(_vt(value))
            return replace(node, value=value)
        if isinstance(node, IRIf):
            return replace(
                node,
                test=self.expression(node.test, scope),
                then_body=self.block(node.then_body, scope),
                else_body=self.optional_block(node.else_body, scope),
            )
        if isinstance(node, IRWhile):
            return replace(
                node,
                test=self.expression(node.test, scope),
                body=self.block(node.body, scope),
                else_body=self.optional_block(node.else_body, scope),
            )
        if isinstance(node, IRForEach):
            iterable = self.expression(node.iterable, scope)
            item = element_type(_vt(iterable))
            target = node.target
            if isinstance(target, IRName):
                scope.bind(target.name, item)
                target = replace(target, value_type=scope.get(target.name) or item)
            else:
                target = self.expression(target, scope)
            return replace(
                node,
                target=target,
                iterable=iterable,
                body=self.block(node.body, scope),
                else_body=self.optional_block(node.else_body, scope),
            )
        if isinstance(node, IRForRange):
            scope.bind(node.variable, t_int())
            return replace(
                node,
                start=self.expression(node.start, scope),
                stop=self.expression(node.stop, scope),
                step=self.expression(node.step, scope) if node.step is not None else None,
                body=self.block(node.body, scope),
            )
        if isinstance(node, IRTry):
            handlers: list[IRCatch] = []
            body = self.block(node.body, scope)
            for handler in node.handlers:
                types = [self.expression(item, scope) for item in handler.exception_types]
                if handler.name:
                    bound = t_unknown()
                    if len(types) == 1:
                        bound = self._instance_of(_vt(types[0]))
                    scope.bind(handler.name, bound)
                handlers.append(
                    replace(handler, exception_types=types, body=self.block(handler.body, scope))
                )
            return replace(
                node,
                body=body,
                handlers=handlers,
                else_body=self.optional_block(node.else_body, scope),
                finally_body=self.optional_block(node.finally_body, scope),
            )
        if isinstance(node, IRThrow):
            return replace(
                node,
                value=self.expression(node.value, scope) if node.value is not None else None,
                cause=self.expression(node.cause, scope) if node.cause is not None else None,
            )
        if isinstance(node, IRAssert):
            return replace(
                node,
                test=self.expression(node.test, scope),
                message=self.expression(node.message, scope) if node.message is not None else None,
            )
        if isinstance(node, IRDelete):
            return replace(node, targets=[self.expression(item, scope) for item in node.targets])
        if isinstance(node, IRBlock):
            return self.block(node, scope)
        return node

    def _instance_of(self, ir_type: IRType) -> IRType:
        """The instance type for a class-reference type."""
        if ir_type.meta.get(CLASS_REF):
            meta = {key: value for key, value in ir_type.meta.items() if key != CLASS_REF}
            return IRType(
                ir_type.kind, ir_type.name, ir_type.args, ir_type.source_text, False, meta
            )
        if ir_type.meta.get(EXCEPTION_REF):
            return IRType(
                TypeKind.OBJECT, ir_type.name, source_text=ir_type.name, meta={EXCEPTION_REF: True}
            )
        return t_unknown()

    # ------------------------------------------------------------- expressions

    def expression(self, node: IRExpression | None, scope: _Scope) -> IRExpression:
        if node is None:
            return IRLiteral(None, value_type=t_none())
        if isinstance(node, IRUnsupported):
            return node
        handler = getattr(self, f"_x_{type(node).__name__}", None)
        return handler(node, scope) if handler is not None else self._generic(node, scope)

    def _generic(self, node: IRExpression, scope: _Scope) -> IRExpression:
        changes: dict[str, Any] = {}
        for name in node.payload_names():
            value = getattr(node, name)
            if isinstance(value, IRExpression):
                changes[name] = self.expression(value, scope)
            elif isinstance(value, list) and value and isinstance(value[0], IRNode):
                changes[name] = [self._child(item, scope) for item in value]
        return replace(node, **changes) if changes else node

    def _child(self, item: Any, scope: _Scope) -> Any:
        if isinstance(item, IRExpression):
            return self.expression(item, scope)
        if isinstance(item, IRKeyword):
            return replace(item, value=self.expression(item.value, scope))
        if isinstance(item, IRMapEntry):
            return replace(
                item, key=self.expression(item.key, scope), value=self.expression(item.value, scope)
            )
        return item

    @staticmethod
    def _typed(node: IRExpression, ir_type: IRType | None) -> IRExpression:
        if ir_type is not None and _better(_vt(node), ir_type):
            return replace(node, value_type=ir_type)
        return node

    def _x_IRLiteral(self, node: IRLiteral, scope: _Scope) -> IRExpression:  # noqa: ARG002
        return node

    def _x_IRName(self, node: IRName, scope: _Scope) -> IRExpression:
        local = scope.get(node.name)
        if local is not None:
            return self._typed(node, local)
        return self._typed(node, self._global_name(node.name))

    def _global_name(self, name: str) -> IRType | None:
        entry = self.index.lookup(self.module.name, name)
        if entry is None:
            if name in BUILTIN_EXCEPTIONS:
                return IRType(TypeKind.OBJECT, name, source_text=name, meta={EXCEPTION_REF: True})
            return None
        if entry.kind is ScopeKind.CLASS:
            info = self.index.classes[entry.qualified]
            return _with_meta(self.index.instance_type(info), **{CLASS_REF: True})
        if entry.kind is ScopeKind.FUNCTION:
            function = self.index.functions[entry.qualified].node
            params = tuple(
                self.index.qualify(param.param_type, entry.module) for param in function.params
            )
            returns = self.index.qualify(function.return_type, entry.module)
            return t_callable(params, returns)
        if entry.kind is ScopeKind.VARIABLE:
            return self.index.variable_type(self.module.name, name)
        if entry.kind is ScopeKind.MODULE:
            return IRType(TypeKind.OBJECT, entry.qualified, meta={MODULE_REF: entry.qualified})
        return None

    def _x_IRAttribute(self, node: IRAttribute, scope: _Scope) -> IRExpression:
        target = self.expression(node.target, scope)
        rebuilt = replace(node, target=target) if target is not node.target else node
        return self._typed(rebuilt, self._attribute_type(target, node.name))

    def _attribute_type(self, target: IRExpression, name: str) -> IRType | None:
        owner_type = _vt(target)
        if _is_super_call(target) and self.klass is not None and self.klass.base:
            base = self.index.classes.get(self.klass.base)
            return self._member_type(base, name, instance=True) if base else None
        module_ref = owner_type.meta.get(MODULE_REF)
        if isinstance(module_ref, str):
            nested = f"{module_ref}.{name}"
            if nested in self.index.modules:
                return IRType(TypeKind.OBJECT, nested, meta={MODULE_REF: nested})
            entry = self.index.lookup(module_ref, name)
            if entry is None:
                return None
            saved = self.module
            self.module = self.index.modules[module_ref]
            try:
                return self._global_name(name)
            finally:
                self.module = saved
        info = self.index.class_for_type(owner_type, self.module.name)
        if info is None:
            return None
        return self._member_type(info, name, instance=not owner_type.meta.get(CLASS_REF))

    def _member_type(self, info: ClassInfo, name: str, *, instance: bool) -> IRType | None:
        found = self.index.find_method(info, name)
        if found is not None:
            owner, method = found
            returns = self.index.qualify(method.return_type, owner.module)
            if name in owner.properties and instance:
                return returns
            params = tuple(self.index.qualify(p.param_type, owner.module) for p in method.params)
            return _with_meta(t_callable(params, returns), **{METHOD_OF: owner.qualified})
        return self.index.field_type(info, name)

    def _x_IRCall(self, node: IRCall, scope: _Scope) -> IRExpression:
        callee = self.expression(node.callee, scope)
        args = [self.expression(item, scope) for item in node.args]
        keywords = [
            replace(item, value=self.expression(item.value, scope)) for item in node.keywords
        ]
        star_args = [self.expression(item, scope) for item in node.star_args]
        star_keywords = [self.expression(item, scope) for item in node.star_keywords]
        rebuilt = replace(
            node,
            callee=callee,
            args=args,
            keywords=keywords,
            star_args=star_args,
            star_keywords=star_keywords,
        )
        return self._typed(rebuilt, self._call_type(callee, args, scope))

    def _call_type(
        self, callee: IRExpression, args: list[IRExpression], scope: _Scope
    ) -> IRType | None:
        callee_type = _vt(callee)
        if isinstance(callee, IRName) and scope.get(callee.name) is None:
            if self.index.lookup(self.module.name, callee.name) is None:
                if callee.name == "super" and self.klass is not None and self.klass.base:
                    base = self.index.classes.get(self.klass.base)
                    return self.index.instance_type(base) if base else None
                builtin = _builtin_result(callee.name, [_vt(item) for item in args])
                if builtin is not None:
                    return builtin
        if callee_type.meta.get(CLASS_REF):
            return self._instance_of(callee_type)
        if callee_type.meta.get(EXCEPTION_REF):
            return self._instance_of(callee_type)
        if callee_type.kind is TypeKind.CALLABLE and callee_type.args:
            returns = callee_type.args[-1]
            return returns if is_known(returns) else None
        if isinstance(callee, IRAttribute):
            receiver = _vt(callee.target)
            return _method_result(receiver, callee.name)
        return None

    def _x_IRBinary(self, node: IRBinary, scope: _Scope) -> IRExpression:
        left = self.expression(node.left, scope)
        right = self.expression(node.right, scope)
        rebuilt = replace(node, left=left, right=right)
        return self._typed(rebuilt, _binary_type(node.op, _vt(left), _vt(right)))

    def _x_IRUnary(self, node: IRUnary, scope: _Scope) -> IRExpression:
        operand = self.expression(node.operand, scope)
        rebuilt = replace(node, operand=operand)
        if node.op is UnaryOp.NOT:
            return self._typed(rebuilt, t_bool())
        operand_type = _vt(operand)
        if node.op is UnaryOp.BIT_NOT:
            return self._typed(rebuilt, t_int() if _is_numeric(operand_type) else None)
        if _is_numeric(operand_type):
            return self._typed(rebuilt, t_int() if operand_type.name == "bool" else operand_type)
        return rebuilt

    def _x_IRCompare(self, node: IRCompare, scope: _Scope) -> IRExpression:
        rebuilt = replace(
            node, left=self.expression(node.left, scope), right=self.expression(node.right, scope)
        )
        return self._typed(rebuilt, t_bool())

    def _x_IRLogical(self, node: IRLogical, scope: _Scope) -> IRExpression:
        operands = [self.expression(item, scope) for item in node.operands]
        rebuilt = replace(node, operands=operands)
        unified = _unify([_vt(item) for item in operands])
        if is_known(unified) and not _is(unified, "bool"):
            # `a or b` returns an operand; keep the operand type when they agree.
            return replace(rebuilt, value_type=unified)
        return self._typed(rebuilt, t_bool())

    def _x_IRConditional(self, node: IRConditional, scope: _Scope) -> IRExpression:
        then_value = self.expression(node.then_value, scope)
        else_value = self.expression(node.else_value, scope)
        rebuilt = replace(
            node,
            test=self.expression(node.test, scope),
            then_value=then_value,
            else_value=else_value,
        )
        return self._typed(rebuilt, _unify([_vt(then_value), _vt(else_value)]))

    def _x_IRIndex(self, node: IRIndex, scope: _Scope) -> IRExpression:
        target = self.expression(node.target, scope)
        index = self.expression(node.index, scope)
        rebuilt = replace(node, target=target, index=index)
        container = _vt(target)
        if container.kind is TypeKind.OPTIONAL and container.args:
            container = container.args[0]
        result: IRType | None = None
        if _is(container, "str"):
            result = t_str()
        elif _is(container, "list") and container.args:
            result = container.args[0]
        elif _is(container, "dict") and len(container.args) > 1:
            result = container.args[1]
        elif _is(container, "tuple") and container.args:
            if isinstance(index, IRLiteral) and isinstance(index.value, int):
                position = index.value
                if -len(container.args) <= position < len(container.args):
                    result = container.args[position]
            else:
                result = element_type(container)
        return self._typed(rebuilt, result)

    def _x_IRSlice(self, node: IRSlice, scope: _Scope) -> IRExpression:
        target = self.expression(node.target, scope)
        rebuilt = replace(
            node,
            target=target,
            lower=self.expression(node.lower, scope) if node.lower is not None else None,
            upper=self.expression(node.upper, scope) if node.upper is not None else None,
            step=self.expression(node.step, scope) if node.step is not None else None,
        )
        container = _vt(target)
        if _is(container, "str") or _is(container, "list"):
            return self._typed(rebuilt, container)
        return rebuilt

    def _x_IRArrayLiteral(self, node: IRArrayLiteral, scope: _Scope) -> IRExpression:
        elements = [self.expression(item, scope) for item in node.elements]
        rebuilt = replace(node, elements=elements)
        if elements:
            return self._typed(rebuilt, t_list(_unify([_vt(item) for item in elements])))
        return rebuilt

    def _x_IRSetLiteral(self, node: IRSetLiteral, scope: _Scope) -> IRExpression:
        elements = [self.expression(item, scope) for item in node.elements]
        rebuilt = replace(node, elements=elements)
        if elements:
            return self._typed(rebuilt, t_set(_unify([_vt(item) for item in elements])))
        return rebuilt

    def _x_IRTupleLiteral(self, node: IRTupleLiteral, scope: _Scope) -> IRExpression:
        elements = [self.expression(item, scope) for item in node.elements]
        rebuilt = replace(node, elements=elements)
        types = [_vt(item) for item in elements]
        if all(is_known(item) for item in types):
            return self._typed(rebuilt, t_tuple(*types))
        return rebuilt

    def _x_IRMapLiteral(self, node: IRMapLiteral, scope: _Scope) -> IRExpression:
        entries = [
            replace(
                entry,
                key=self.expression(entry.key, scope),
                value=self.expression(entry.value, scope),
            )
            for entry in node.entries
        ]
        rebuilt = replace(node, entries=entries)
        if entries:
            key = _unify([_vt(entry.key) for entry in entries])
            value = _unify([_vt(entry.value) for entry in entries])
            return self._typed(rebuilt, t_dict(key, value))
        return rebuilt

    def _x_IRTemplateString(self, node: IRTemplateString, scope: _Scope) -> IRExpression:
        return replace(node, parts=[self.expression(item, scope) for item in node.parts])

    def _x_IRLambda(self, node: IRLambda, scope: _Scope) -> IRExpression:
        inner = scope.child()
        for param in node.params:
            inner.bind(param.name, param.param_type)
        body = self.expression(node.body, inner) if node.body is not None else None
        rebuilt = replace(node, body=body)
        returns = _vt(body) if body is not None else t_unknown()
        params = tuple(param.param_type for param in node.params)
        return self._typed(rebuilt, t_callable(params, returns))

    def _x_IRComprehension(self, node: IRComprehension, scope: _Scope) -> IRExpression:
        iterable = self.expression(node.iterable, scope)
        inner = scope.child()
        inner.bind(node.target, element_type(_vt(iterable)))
        condition = self.expression(node.condition, inner) if node.condition is not None else None
        element = self.expression(node.element, inner)
        key = self.expression(node.key, inner) if node.key is not None else None
        rebuilt = replace(node, iterable=iterable, condition=condition, element=element, key=key)
        if node.form == "dict":
            result = t_dict(_vt(key) if key is not None else t_unknown(), _vt(element))
        elif node.form == "set":
            result = t_set(_vt(element))
        else:
            result = t_list(_vt(element))
        rebuilt = replace(rebuilt, meta={**rebuilt.meta, "itemType": element_type(_vt(iterable))})
        return self._typed(rebuilt, result)


def _is_super_call(node: IRExpression) -> bool:
    return (
        isinstance(node, IRCall)
        and isinstance(node.callee, IRName)
        and node.callee.name == "super"
        and not node.args
    )


def _binary_type(op: BinaryOp, left: IRType, right: IRType) -> IRType | None:
    if op is BinaryOp.ADD:
        if _is(left, "str") and _is(right, "str"):
            return t_str()
        if _is(left, "list") and _is(right, "list"):
            return (
                left
                if left == right or not is_known(element_type(right))
                else _unify([left, right])
            )
    if op is BinaryOp.MUL:
        if _is(left, "str") and _is(right, "int"):
            return t_str()
        if _is(left, "list") and _is(right, "int"):
            return left
    if op is BinaryOp.MOD and _is(left, "str"):
        return t_str()
    if not (_is_numeric(left) and _is_numeric(right)):
        return None
    if op is BinaryOp.DIV:
        return t_float()
    if op in (
        BinaryOp.BIT_AND,
        BinaryOp.BIT_OR,
        BinaryOp.BIT_XOR,
        BinaryOp.SHIFT_LEFT,
        BinaryOp.SHIFT_RIGHT,
    ):
        if left.name == "bool" and right.name == "bool" and op is not BinaryOp.SHIFT_LEFT:
            return t_bool()
        return t_int()
    if left.name == "float" or right.name == "float":
        return t_float()
    return t_int()


def infer_module_types(
    module: IRModule, index: ProjectIndex, bag: DiagnosticBag | None = None
) -> IRModule:
    """Return *module* with inferred expression types filled in."""
    return _Inferrer(index, module, bag).run()


@dataclass
class TypeInferencePass:
    """Transform pass wrapper for :func:`infer_module_types`."""

    index: ProjectIndex
    name: str = "infer-expression-types"
    description: str = "Infer local, attribute, call and element types for statically typed targets"
    targets: tuple[str, ...] = ()

    def applies_to(self, module: IRModule) -> bool:
        return bool(module.functions or module.classes or module.body)

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        return infer_module_types(module, self.index, bag)
