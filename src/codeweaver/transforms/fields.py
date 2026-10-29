"""Declare instance fields that Python only ever assigns.

Python creates an attribute the first time it is assigned, so

    class Account:
        def __init__(self, owner):
            self.owner = owner

declares nothing.  A class-based target usually requires the field to exist
before it is assigned — under TypeScript's ``strict`` setting the generated
class would not compile at all.

This pass collects ``self.<name> = ...`` assignments from a class's methods and
adds an :class:`IRField` for each name the class does not already declare.  The
field type comes from the assigned value when the frontend knew it, and from
the constructor parameter of the same name when the value is just that
parameter — which is the overwhelmingly common case.

Assignments in methods other than the constructor are included too, but
reported: an attribute that only ever appears in a late-running method is often
a sign the original relied on it being optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.base import walk
from codeweaver.ir.declarations import IRClass, IRField, IRFunction, IRModule, Visibility
from codeweaver.ir.expressions import IRAttribute, IRName
from codeweaver.ir.statements import IRAssign, IRAugAssign
from codeweaver.ir.types import IRType, TypeKind, t_unknown

__all__ = ["InferFieldsPass", "assigned_attribute_types", "infer_instance_fields"]


def _is_known(ir_type: IRType | None) -> bool:
    return bool(ir_type and ir_type.kind is not TypeKind.UNKNOWN)


def _assigned_attributes(method: IRFunction) -> list[tuple[str, IRType]]:
    """``(name, type)`` for each ``self.<name>`` assigned in *method*."""
    receiver = str(method.meta.get("pythonReceiver", ""))
    if not receiver:
        return []
    by_param = {param.name: param.param_type for param in method.params}
    found: list[tuple[str, IRType]] = []
    for node in walk(method.body):
        if isinstance(node, IRAssign | IRAugAssign):
            target = node.target
            if (
                isinstance(target, IRAttribute)
                and isinstance(target.target, IRName)
                and target.target.name == receiver
            ):
                found.append((target.name, _infer_type(node.value, by_param)))
    return found


def _infer_type(value: object, by_param: dict[str, IRType]) -> IRType:
    if isinstance(value, IRName):
        declared = by_param.get(value.name)
        if declared is not None and _is_known(declared):
            return declared
    observed = getattr(value, "value_type", None)
    if isinstance(observed, IRType) and _is_known(observed):
        return observed
    return t_unknown()


def assigned_attribute_types(klass: IRClass) -> dict[str, IRType]:
    """Attribute names a class assigns to its receiver, with inferred types.

    This is what :func:`infer_instance_fields` would declare, computed without
    rewriting anything, so project-wide analysis can take a subclass's
    *effective* inherited fields into account before the pass has run.
    """
    constructor = klass.constructor
    ordered = (
        [constructor, *[m for m in klass.methods if m is not constructor]]
        if constructor is not None
        else list(klass.methods)
    )
    found: dict[str, IRType] = {}
    for method in ordered:
        for name, inferred in _assigned_attributes(method):
            existing = found.get(name)
            if existing is None or not _is_known(existing):
                found[name] = inferred
    return found


def _infer_for_class(
    klass: IRClass,
    bag: DiagnosticBag | None,
    inherited: dict[str, IRType] | None = None,
) -> IRClass:
    # A field a base class declares must not be re-declared here: in Python the
    # subclass is using the inherited attribute, and re-declaring it in a
    # class-based target is a type error.
    declared = {item.name for item in klass.fields} | set(inherited or {})
    constructor = klass.constructor
    added: list[IRField] = []
    seen: dict[str, IRType] = {}

    ordered_methods = (
        [constructor, *[m for m in klass.methods if m is not constructor]]
        if constructor is not None
        else list(klass.methods)
    )
    for method in ordered_methods:
        for name, inferred in _assigned_attributes(method):
            if name in declared or name in seen:
                # Keep the first type seen; a later assignment with a different
                # type does not get to redefine the field.
                continue
            seen[name] = inferred
            added.append(
                IRField(
                    name=name,
                    field_type=inferred,
                    default=None,
                    is_static=False,
                    visibility=Visibility.from_name(name),
                    optional=method is not constructor,
                    span=method.span,
                    meta={"inferredFrom": method.name},
                )
            )
            if method is not constructor and bag is not None:
                bag.info(
                    "CW0720",
                    f"field {klass.name}.{name} is only assigned in {method.name!r}, "
                    "so it is declared without an initialiser",
                    stage="transform",
                    span=method.span,
                    hint="the field may be undefined before that method runs",
                )
    if not added:
        return klass
    return replace(klass, fields=[*klass.fields, *added])


def infer_instance_fields(
    module: IRModule,
    bag: DiagnosticBag | None = None,
    inherited_fields: dict[str, dict[str, IRType]] | None = None,
) -> IRModule:
    """Return *module* with assigned-only instance fields declared."""
    inherited_fields = inherited_fields or {}
    classes = [
        _infer_for_class(klass, bag, inherited_fields.get(klass.name)) for klass in module.classes
    ]
    if all(new is old for new, old in zip(classes, module.classes, strict=True)):
        return module
    return replace(module, classes=classes)


@dataclass
class InferFieldsPass:
    """Transform pass wrapper for :func:`infer_instance_fields`.

    ``inherited_fields`` comes from project-wide analysis; without it the pass
    still runs, but a subclass may re-declare a base class's field.
    """

    inherited_fields: dict[str, dict[str, IRType]] = field(default_factory=dict)
    name: str = "infer-instance-fields"
    description: str = "Declare instance fields that are only ever assigned"
    targets: tuple[str, ...] = ()

    def applies_to(self, module: IRModule) -> bool:
        return any(
            _assigned_attributes(method) for klass in module.classes for method in klass.methods
        )

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        return infer_instance_fields(module, bag, self.inherited_fields)
