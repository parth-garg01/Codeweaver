"""IR declaration and module nodes.

A module is the unit CodeWeaver persists, plans and generates.  It keeps its
own source path and language so that a checkpoint is self-describing, and it
separates imports from the body because every backend has to emit them in a
different place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from codeweaver.ir.base import IRNode, ir_node, walk
from codeweaver.ir.expressions import IRExpression, IRParam
from codeweaver.ir.statements import IRBlock, IRStatement
from codeweaver.ir.types import IRType, t_unknown

__all__ = [
    "ACCESSOR_GETTER",
    "ACCESSOR_SETTER",
    "IRClass",
    "IRDecorator",
    "IRField",
    "IRFunction",
    "IRImport",
    "IRImportName",
    "IRModule",
    "Visibility",
]


class Visibility:
    """Visibility markers.

    Python has no enforced visibility, so a frontend records the convention it
    observed (a leading underscore) and leaves the decision about what that
    means to the backend.
    """

    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"

    @staticmethod
    def from_name(name: str) -> str:
        if name.startswith("__") and name.endswith("__"):
            return Visibility.PUBLIC
        if name.startswith("__"):
            return Visibility.PRIVATE
        if name.startswith("_"):
            return Visibility.PROTECTED
        return Visibility.PUBLIC


#: ``IRFunction.accessor`` values.  Empty means an ordinary method.
ACCESSOR_GETTER = "getter"
ACCESSOR_SETTER = "setter"


@ir_node
@dataclass
class IRDecorator(IRNode):
    """A decorator application, kept verbatim.

    Decorators are not executed or interpreted by the IR: a backend either
    recognises one (``@staticmethod``, ``@dataclass``) or reports it.
    """

    kind: ClassVar[str] = "decorator"

    expression: IRExpression
    source_text: str = ""


@ir_node
@dataclass
class IRImportName(IRNode):
    kind: ClassVar[str] = "import_name"

    name: str
    alias: str = ""

    @property
    def local_name(self) -> str:
        return self.alias or self.name


@ir_node
@dataclass
class IRImport(IRNode):
    """An import of a module or of names from a module."""

    kind: ClassVar[str] = "import"

    module: str
    names: list[IRImportName] = field(default_factory=list)
    #: ``True`` for ``from . import x`` style imports.
    is_relative: bool = False
    #: Number of leading dots in a relative import.
    level: int = 0
    #: Alias for whole-module imports (``import x as y``).
    module_alias: str = ""
    #: ``True`` when the import target could not be resolved to a project
    #: module, which usually means it is a third-party or standard library
    #: dependency the target ecosystem may not have.
    external: bool = False

    @property
    def imports_whole_module(self) -> bool:
        return not self.names


@ir_node
@dataclass
class IRField(IRNode):
    """A class-level or instance-level field."""

    kind: ClassVar[str] = "field"

    name: str
    field_type: IRType = field(default_factory=t_unknown)
    default: IRExpression | None = None
    is_static: bool = False
    visibility: str = Visibility.PUBLIC
    #: ``True`` when the field is not guaranteed to hold a value before it is
    #: first read, for example an attribute only assigned in a late method.
    optional: bool = False


@ir_node
@dataclass
class IRFunction(IRNode):
    """A function, method, constructor or property accessor."""

    kind: ClassVar[str] = "function"

    name: str
    params: list[IRParam] = field(default_factory=list)
    return_type: IRType = field(default_factory=t_unknown)
    body: IRBlock = field(default_factory=IRBlock)
    is_async: bool = False
    is_static: bool = False
    is_method: bool = False
    is_constructor: bool = False
    is_abstract: bool = False
    #: ``""`` for an ordinary method, otherwise :data:`ACCESSOR_GETTER` or
    #: :data:`ACCESSOR_SETTER`.
    #:
    #: A getter and a setter share a name on purpose — they are two halves of
    #: one property, not two methods — so the duplicate-name rule in
    #: :mod:`codeweaver.ir.schema` has to be able to tell them apart.  Without
    #: this field a ``@property`` / ``@x.setter`` pair made the whole module
    #: invalid.
    accessor: str = ""
    decorators: list[IRDecorator] = field(default_factory=list)
    docstring: str = ""
    visibility: str = Visibility.PUBLIC

    @property
    def is_accessor(self) -> bool:
        return bool(self.accessor)

    @property
    def arity(self) -> int:
        return len(self.params)

    def param_named(self, name: str) -> IRParam | None:
        return next((param for param in self.params if param.name == name), None)


@ir_node
@dataclass
class IRClass(IRNode):
    """A class, or an interface when the frontend could prove it has no state."""

    kind: ClassVar[str] = "class"

    name: str
    bases: list[IRExpression] = field(default_factory=list)
    fields: list[IRField] = field(default_factory=list)
    methods: list[IRFunction] = field(default_factory=list)
    decorators: list[IRDecorator] = field(default_factory=list)
    docstring: str = ""
    is_interface: bool = False
    visibility: str = Visibility.PUBLIC

    def method_named(self, name: str, accessor: str = "") -> IRFunction | None:
        """The method called *name*, optionally restricted to one accessor half.

        With no *accessor* the first match wins, which for a property pair is
        the getter.
        """
        return next(
            (
                method
                for method in self.methods
                if method.name == name and (not accessor or method.accessor == accessor)
            ),
            None,
        )

    def accessor_pairs(self) -> dict[str, dict[str, IRFunction]]:
        """Property name -> its accessor halves, keyed ``getter``/``setter``."""
        pairs: dict[str, dict[str, IRFunction]] = {}
        for method in self.methods:
            if method.accessor:
                pairs.setdefault(method.name, {})[method.accessor] = method
        return pairs

    @property
    def constructor(self) -> IRFunction | None:
        return next((method for method in self.methods if method.is_constructor), None)


@ir_node
@dataclass
class IRModule(IRNode):
    """One source file translated into IR."""

    kind: ClassVar[str] = "module"

    name: str
    path: str = ""
    source_language: str = ""
    imports: list[IRImport] = field(default_factory=list)
    body: list[IRStatement] = field(default_factory=list)
    functions: list[IRFunction] = field(default_factory=list)
    classes: list[IRClass] = field(default_factory=list)
    docstring: str = ""

    @property
    def declared_names(self) -> list[str]:
        """Top-level names this module defines, in source order of declaration."""
        names = [function.name for function in self.functions]
        names.extend(klass.name for klass in self.classes)
        for statement in self.body:
            name = getattr(statement, "name", None)
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def unsupported_nodes(self) -> list[IRNode]:
        """Every :class:`IRUnsupported` node anywhere in this module."""
        return [node for node in walk(self) if node.kind == "unsupported"]

    def function_named(self, name: str) -> IRFunction | None:
        return next((function for function in self.functions if function.name == name), None)

    def class_named(self, name: str) -> IRClass | None:
        return next((klass for klass in self.classes if klass.name == name), None)
