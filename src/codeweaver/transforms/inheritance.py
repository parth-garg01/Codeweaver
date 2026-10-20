"""Resolve which fields a class inherits.

Two passes need this and neither can work it out from a single module:

* field inference must not re-declare a field a base class already declares.
  In Python ``self.balance += x`` in a subclass just uses the inherited
  attribute; re-declaring it in the subclass is a type error in TypeScript.
* type propagation needs inherited field types so that ``self.rate`` inside a
  subclass is not ``unknown``.

Base classes are resolved by name across the whole project.  That is a
heuristic: two modules may each declare a ``Base``.  It is the same heuristic
the dependency graph uses for a bare name reference, and it is applied only to
names the project actually declares as classes, so the failure mode is a type
that is too specific rather than a field that silently disappears.
"""

from __future__ import annotations

from codeweaver.ir.declarations import IRClass, IRModule
from codeweaver.ir.expressions import IRName
from codeweaver.ir.types import IRType, TypeKind
from codeweaver.transforms.fields import assigned_attribute_types

__all__ = ["collect_inherited_fields", "resolve_base_names"]

#: How deep the resolver follows a chain of base classes.  A cycle in the
#: hierarchy is impossible in valid Python, but the input is untrusted.
_MAX_DEPTH = 32


def resolve_base_names(klass: IRClass) -> list[str]:
    """Base class names written as bare names, in declaration order."""
    return [base.name for base in klass.bases if isinstance(base, IRName)]


def _own_fields(klass: IRClass) -> dict[str, IRType]:
    """Fields a class effectively owns: declared, plus the ones it assigns.

    Attributes assigned to the receiver are counted even though field inference
    has not run yet.  Otherwise a subclass would re-declare a base class field
    that only exists because the base's constructor assigns it, which is the
    common shape in Python.
    """
    owned = {item.name: item.field_type for item in klass.fields if item.name}
    for name, inferred in assigned_attribute_types(klass).items():
        existing = owned.get(name)
        if existing is None or existing.kind is TypeKind.UNKNOWN:
            owned[name] = inferred
    return owned


def collect_inherited_fields(modules: list[IRModule]) -> dict[str, dict[str, IRType]]:
    """Map each class name to the fields it inherits, with their types.

    Only ancestors' fields are included; a class's own fields are not. Where an
    ancestor declares a field with a known type it wins over an ancestor
    further up that leaves it unknown.
    """
    by_name: dict[str, IRClass] = {}
    for module in modules:
        for klass in module.classes:
            by_name.setdefault(klass.name, klass)

    inherited: dict[str, dict[str, IRType]] = {}
    for name, klass in by_name.items():
        collected: dict[str, IRType] = {}
        queue = list(resolve_base_names(klass))
        seen: set[str] = {name}
        depth = 0
        while queue and depth < _MAX_DEPTH:
            depth += 1
            next_queue: list[str] = []
            for base_name in queue:
                if base_name in seen:
                    continue
                seen.add(base_name)
                base = by_name.get(base_name)
                if base is None:
                    continue
                for field_name, field_type in _own_fields(base).items():
                    existing = collected.get(field_name)
                    if existing is None or existing.kind is TypeKind.UNKNOWN:
                        collected[field_name] = field_type
                next_queue.extend(resolve_base_names(base))
            queue = next_queue
        inherited[name] = collected
    return inherited
