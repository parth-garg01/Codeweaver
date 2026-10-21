"""Propagate declared types onto the places that use them.

The frontend records a type where the source states one: on a parameter, an
annotated assignment, a return annotation, a class field.  It does *not* put
that type on every reference to the name, because a reference is just a name.

Backends need it there.  ``len(x)`` generates ``x.length``, ``x.size`` or
``Object.keys(x).length`` depending on what ``x`` is, and without propagation
every such decision falls back to a warning.  This pass copies declared types
onto the references that resolve to them.

It is deliberately a flat, single-assignment analysis:

* one scope per function, built from parameters and declarations anywhere in
  the body — so a name reused with two different types ends up with the first
  one and is reported;
* no flow sensitivity, so a narrowed type inside an ``if`` is not tracked;
* a reference whose type is already known is never overwritten.

Anything beyond that needs real inference, and a wrong type here would make a
backend generate confidently incorrect code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.base import IRNode, walk
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule
from codeweaver.ir.expressions import IRAttribute, IRName
from codeweaver.ir.statements import IRVarDecl
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.rewrite import rewrite

__all__ = ["TypePropagationPass", "propagate_types", "scope_types"]


def _is_known(ir_type: IRType | None) -> bool:
    return bool(ir_type and ir_type.kind is not TypeKind.UNKNOWN)


def scope_types(root: IRNode, bag: DiagnosticBag | None = None) -> dict[str, IRType]:
    """Collect the declared types visible in the scope rooted at *root*."""
    found: dict[str, IRType] = {}
    if isinstance(root, IRFunction):
        for param in root.params:
            if _is_known(param.param_type):
                found[param.name] = param.param_type
    for node in walk(root):
        if isinstance(node, IRVarDecl) and node.name:
            declared: IRType | None = node.var_type
            if not _is_known(declared):
                observed = getattr(node.value, "value_type", None)
                declared = observed if isinstance(observed, IRType) else None
            if declared is None or not _is_known(declared):
                continue
            existing = found.get(node.name)
            if existing is not None and existing != declared:
                if bag is not None:
                    bag.info(
                        "CW0710",
                        f"name {node.name!r} is declared with more than one type; "
                        f"keeping {existing}",
                        stage="transform",
                        span=node.span,
                        hint="the generated code uses the first declared type",
                    )
                continue
            found.setdefault(node.name, declared)
    return found


def _annotate(scope: dict[str, IRType], fields: dict[str, IRType], receiver: str) -> object:
    def rewriter(node: IRNode) -> IRNode | None:
        if isinstance(node, IRName):
            if _is_known(node.value_type):
                return None
            declared = scope.get(node.name)
            return replace(node, value_type=declared) if declared else None
        if (
            isinstance(node, IRAttribute)
            and receiver
            and isinstance(node.target, IRName)
            and node.target.name == receiver
            and not _is_known(node.value_type)
        ):
            declared = fields.get(node.name)
            return replace(node, value_type=declared) if declared else None
        return None

    return rewriter


def _propagate_function(
    function: IRFunction,
    fields: dict[str, IRType],
    bag: DiagnosticBag | None,
) -> IRFunction:
    scope = scope_types(function, bag)
    receiver = str(function.meta.get("pythonReceiver", ""))
    rewriter = _annotate(scope, fields, receiver)
    body = rewrite(function.body, rewriter)  # type: ignore[arg-type]
    if body is function.body:
        return function
    return replace(function, body=body)


def _propagate_class(
    klass: IRClass,
    bag: DiagnosticBag | None,
    inherited: dict[str, IRType] | None = None,
) -> IRClass:
    fields = {name: declared for name, declared in (inherited or {}).items() if _is_known(declared)}
    fields.update(
        {item.name: item.field_type for item in klass.fields if _is_known(item.field_type)}
    )
    methods = [_propagate_function(method, fields, bag) for method in klass.methods]
    if all(new is old for new, old in zip(methods, klass.methods, strict=True)):
        return klass
    return replace(klass, methods=methods)


def propagate_types(
    module: IRModule,
    bag: DiagnosticBag | None = None,
    inherited_fields: dict[str, dict[str, IRType]] | None = None,
) -> IRModule:
    """Return *module* with declared types copied onto matching references."""
    inherited_fields = inherited_fields or {}
    module_scope = scope_types(module, bag)
    rewriter = _annotate(module_scope, {}, "")
    body = [rewrite(statement, rewriter) for statement in module.body]  # type: ignore[arg-type]
    functions = [_propagate_function(function, {}, bag) for function in module.functions]
    classes = [
        _propagate_class(klass, bag, inherited_fields.get(klass.name)) for klass in module.classes
    ]
    return replace(module, body=body, functions=functions, classes=classes)


@dataclass
class TypePropagationPass:
    """Transform pass wrapper for :func:`propagate_types`."""

    inherited_fields: dict[str, dict[str, IRType]] = field(default_factory=dict)
    name: str = "propagate-declared-types"
    description: str = "Copy declared parameter, variable and field types onto references"
    targets: tuple[str, ...] = ()

    def applies_to(self, module: IRModule) -> bool:
        return bool(module.functions or module.classes or module.body)

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        return propagate_types(module, bag, self.inherited_fields)
