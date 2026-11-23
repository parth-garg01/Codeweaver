"""A project-wide view of what every module declares, for statically typed targets.

The symbol index answers "what exists and where".  A statically typed backend
needs more than that while it is writing a single file:

* which class a name refers to *from this module*, through imports and aliases;
* the type of ``obj.field`` when the field is declared on a base class two
  modules away;
* the parameter list of a function in another module, so a keyword argument can
  be placed in the right position;
* whether a method is overridden anywhere, so only genuinely polymorphic calls
  pay for virtual dispatch;
* whether a class is an exception, because C++ throws those by value.

:class:`ProjectIndex` is built once per run from the IR of every module and is
read-only afterwards, so backends and transforms can share one instance.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from codeweaver.graph.dependency import resolve_relative_import
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule
from codeweaver.ir.expressions import IRName
from codeweaver.ir.statements import IRVarDecl
from codeweaver.ir.types import IRType, TypeKind, t_unknown

__all__ = [
    "BUILTIN_EXCEPTIONS",
    "ClassInfo",
    "FunctionInfo",
    "ProjectIndex",
    "ScopeEntry",
    "ScopeKind",
    "qualified_type",
]

#: Python's builtin exception names.  A class deriving from one of these,
#: directly or through project classes, is an exception class.
BUILTIN_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "BaseException",
        "Exception",
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "EOFError",
        "FileNotFoundError",
        "ImportError",
        "IndexError",
        "KeyError",
        "LookupError",
        "MemoryError",
        "NameError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "PermissionError",
        "RecursionError",
        "RuntimeError",
        "StopIteration",
        "TimeoutError",
        "TypeError",
        "UnicodeError",
        "ValueError",
        "ZeroDivisionError",
    }
)

#: Metadata key on an :class:`IRType` holding the qualified class it names.
QUALIFIED = "qualified"


class ScopeKind(enum.StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    VARIABLE = "variable"
    MODULE = "module"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class ScopeEntry:
    """What a top-level name means inside one module."""

    kind: ScopeKind
    #: Qualified name of the thing (``pkg.models.Account``), or the module name
    #: for :attr:`ScopeKind.MODULE`.
    qualified: str
    #: Module that declares it.
    module: str
    #: Its name in the declaring module.
    name: str


@dataclass
class FunctionInfo:
    module: str
    name: str
    node: IRFunction

    @property
    def qualified(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass
class ClassInfo:
    module: str
    name: str
    node: IRClass
    #: Qualified name of the first base that is a project class.
    base: str = ""
    #: The builtin exception at the root of the hierarchy, if any.
    builtin_base: str = ""
    #: Every base expression could be resolved to a project class or builtin.
    bases_resolved: bool = True
    fields: dict[str, IRType] = field(default_factory=dict)
    static_fields: set[str] = field(default_factory=set)
    methods: dict[str, IRFunction] = field(default_factory=dict)
    properties: set[str] = field(default_factory=set)
    subclasses: list[str] = field(default_factory=list)

    @property
    def qualified(self) -> str:
        return f"{self.module}.{self.name}"

    @property
    def is_exception(self) -> bool:
        return bool(self.builtin_base)

    @property
    def is_abstract(self) -> bool:
        return any(method.is_abstract for method in self.node.methods)


def qualified_type(ir_type: IRType, qualified: str) -> IRType:
    """Return *ir_type* with the qualified class name recorded in its metadata."""
    meta = dict(ir_type.meta)
    meta[QUALIFIED] = qualified
    return IRType(
        ir_type.kind,
        ir_type.name,
        ir_type.args,
        ir_type.source_text,
        ir_type.nullable,
        meta,
    )


@dataclass
class ProjectIndex:
    """Classes, functions, variables and per-module scopes for a whole project."""

    modules: dict[str, IRModule] = field(default_factory=dict)
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    variables: dict[str, IRType] = field(default_factory=dict)
    #: module -> local name -> entry
    scopes: dict[str, dict[str, ScopeEntry]] = field(default_factory=dict)

    # ------------------------------------------------------------------ build

    @classmethod
    def build(cls, modules: list[IRModule]) -> ProjectIndex:
        index = cls()
        ordered = sorted(modules, key=lambda module: module.name)
        for module in ordered:
            index.modules[module.name] = module
            scope: dict[str, ScopeEntry] = {}
            for function in module.functions:
                info = FunctionInfo(module.name, function.name, function)
                index.functions[info.qualified] = info
                scope[function.name] = ScopeEntry(
                    ScopeKind.FUNCTION, info.qualified, module.name, function.name
                )
            for klass in module.classes:
                info_c = ClassInfo(module.name, klass.name, klass)
                for item in klass.fields:
                    info_c.fields[item.name] = item.field_type
                    if item.is_static:
                        info_c.static_fields.add(item.name)
                for method in klass.methods:
                    info_c.methods[method.name] = method
                    if method.meta.get("pythonProperty"):
                        info_c.properties.add(method.name)
                index.classes[info_c.qualified] = info_c
                scope[klass.name] = ScopeEntry(
                    ScopeKind.CLASS, info_c.qualified, module.name, klass.name
                )
            for statement in module.body:
                if isinstance(statement, IRVarDecl) and statement.name:
                    declared = statement.var_type
                    if declared.kind is TypeKind.UNKNOWN and statement.value is not None:
                        declared = getattr(statement.value, "value_type", declared)
                    qualified = f"{module.name}.{statement.name}"
                    index.variables[qualified] = declared
                    scope[statement.name] = ScopeEntry(
                        ScopeKind.VARIABLE, qualified, module.name, statement.name
                    )
            index.scopes[module.name] = scope

        # Imports are resolved after every module's own names are known, so an
        # import cycle resolves the same way regardless of module order.
        for module in ordered:
            scope = index.scopes[module.name]
            for imported in module.imports:
                absolute = resolve_relative_import(module.name, imported.module, imported.level)
                if imported.imports_whole_module:
                    # `import a.b as c` binds `c` to a.b; `import a.b` binds `a`.
                    if imported.module_alias:
                        local, target = imported.module_alias, absolute
                    else:
                        local = absolute.split(".")[0]
                        target = local
                    known = target in index.modules or any(
                        name.startswith(f"{target}.") for name in index.modules
                    )
                    kind = ScopeKind.MODULE if known else ScopeKind.EXTERNAL
                    scope.setdefault(local, ScopeEntry(kind, target, target, local))
                    continue
                for name in imported.names:
                    local = name.local_name
                    source_scope = index.scopes.get(absolute)
                    if source_scope is not None and name.name in source_scope:
                        found = source_scope[name.name]
                        scope[local] = found
                    elif f"{absolute}.{name.name}" in index.modules:
                        qualified = f"{absolute}.{name.name}"
                        scope[local] = ScopeEntry(ScopeKind.MODULE, qualified, qualified, local)
                    else:
                        scope[local] = ScopeEntry(
                            ScopeKind.EXTERNAL, f"{absolute}.{name.name}", absolute, name.name
                        )

        index._link_hierarchy()
        return index

    def _link_hierarchy(self) -> None:
        for info in self.classes.values():
            for base in info.node.bases:
                if not isinstance(base, IRName):
                    info.bases_resolved = False
                    continue
                entry = self.scopes.get(info.module, {}).get(base.name)
                if entry is not None and entry.kind is ScopeKind.CLASS:
                    if not info.base:
                        info.base = entry.qualified
                elif base.name in BUILTIN_EXCEPTIONS:
                    if not info.base and not info.builtin_base:
                        info.builtin_base = base.name
                elif base.name in ("object", "ABC", "Generic", "Protocol"):
                    continue
                else:
                    info.bases_resolved = False
        for info in self.classes.values():
            if info.base and info.base in self.classes:
                self.classes[info.base].subclasses.append(info.qualified)
        # Exception-ness is inherited through project classes.
        for info in self.classes.values():
            if info.builtin_base:
                continue
            seen: set[str] = set()
            current = info
            while current.base and current.base in self.classes and current.base not in seen:
                seen.add(current.base)
                current = self.classes[current.base]
                if current.builtin_base:
                    info.builtin_base = current.builtin_base
                    break
        for info in self.classes.values():
            info.subclasses.sort()

    # ---------------------------------------------------------------- queries

    def lookup(self, module: str, name: str) -> ScopeEntry | None:
        """What *name* means at the top level of *module*."""
        return self.scopes.get(module, {}).get(name)

    def class_named(self, module: str, name: str) -> ClassInfo | None:
        entry = self.lookup(module, name)
        if entry is not None and entry.kind is ScopeKind.CLASS:
            return self.classes.get(entry.qualified)
        return None

    def class_for_type(self, ir_type: IRType | None, module: str) -> ClassInfo | None:
        """The project class an OBJECT type refers to, seen from *module*."""
        if ir_type is None:
            return None
        if ir_type.kind is TypeKind.OPTIONAL and ir_type.args:
            return self.class_for_type(ir_type.args[0], module)
        if ir_type.kind is not TypeKind.OBJECT:
            return None
        qualified = ir_type.meta.get(QUALIFIED)
        if isinstance(qualified, str) and qualified in self.classes:
            return self.classes[qualified]
        found = self.class_named(module, ir_type.name)
        if found is not None:
            return found
        # A dotted annotation such as `models.Account`.
        if "." in ir_type.name:
            head, _, tail = ir_type.name.rpartition(".")
            entry = self.lookup(module, head)
            if entry is not None and entry.kind is ScopeKind.MODULE:
                return self.classes.get(f"{entry.qualified}.{tail}")
        return None

    def qualify(self, ir_type: IRType, module: str) -> IRType:
        """Record the qualified class name on every OBJECT type in *ir_type*."""
        if ir_type.kind is TypeKind.OBJECT:
            info = self.class_for_type(ir_type, module)
            args = tuple(self.qualify(arg, module) for arg in ir_type.args)
            base = ir_type if args == ir_type.args else IRType(
                ir_type.kind, ir_type.name, args, ir_type.source_text, ir_type.nullable, ir_type.meta
            )
            return qualified_type(base, info.qualified) if info is not None else base
        if ir_type.args:
            args = tuple(self.qualify(arg, module) for arg in ir_type.args)
            if args != ir_type.args or any(
                a.meta.get(QUALIFIED) != b.meta.get(QUALIFIED)
                for a, b in zip(args, ir_type.args, strict=True)
            ):
                return IRType(
                    ir_type.kind,
                    ir_type.name,
                    args,
                    ir_type.source_text,
                    ir_type.nullable,
                    ir_type.meta,
                )
        return ir_type

    def instance_type(self, info: ClassInfo) -> IRType:
        return qualified_type(IRType(TypeKind.OBJECT, info.name, source_text=info.name), info.qualified)

    def ancestors(self, info: ClassInfo) -> list[ClassInfo]:
        """*info* followed by its project base classes, nearest first."""
        chain = [info]
        seen = {info.qualified}
        current = info
        while current.base and current.base in self.classes and current.base not in seen:
            seen.add(current.base)
            current = self.classes[current.base]
            chain.append(current)
        return chain

    def field_owner(self, info: ClassInfo, name: str) -> ClassInfo | None:
        for klass in self.ancestors(info):
            if name in klass.fields:
                return klass
        return None

    def field_type(self, info: ClassInfo, name: str) -> IRType | None:
        owner = self.field_owner(info, name)
        if owner is None:
            return None
        return self.qualify(owner.fields[name], owner.module)

    def find_method(self, info: ClassInfo, name: str) -> tuple[ClassInfo, IRFunction] | None:
        for klass in self.ancestors(info):
            method = klass.methods.get(name)
            if method is not None:
                return klass, method
        return None

    def constructor(self, info: ClassInfo) -> tuple[ClassInfo, IRFunction] | None:
        """The ``__init__`` a construction of *info* runs, possibly inherited."""
        return self.find_method(info, "__init__")

    def descendants(self, info: ClassInfo) -> list[ClassInfo]:
        found: list[ClassInfo] = []
        pending = list(info.subclasses)
        seen: set[str] = set()
        while pending:
            name = pending.pop(0)
            if name in seen or name not in self.classes:
                continue
            seen.add(name)
            child = self.classes[name]
            found.append(child)
            pending.extend(child.subclasses)
        return found

    def is_overridden(self, info: ClassInfo, method: str) -> bool:
        """``True`` when any subclass of *info* redefines *method*."""
        return any(method in child.methods for child in self.descendants(info))

    def overrides(self, info: ClassInfo, method: str) -> bool:
        """``True`` when *method* on *info* redefines one from a base class."""
        return any(method in klass.methods for klass in self.ancestors(info)[1:])

    def needs_dispatch(self, info: ClassInfo, method: str) -> bool:
        """Whether calls to *method* must be dispatched dynamically.

        A method is virtual if it is abstract, overrides something, or is
        overridden somewhere.  Everything else can be a direct call that the
        target compiler is free to inline.
        """
        found = info.methods.get(method)
        if found is not None and found.is_abstract:
            return True
        return self.overrides(info, method) or self.is_overridden(info, method)

    def function_for(self, module: str, name: str) -> FunctionInfo | None:
        entry = self.lookup(module, name)
        if entry is not None and entry.kind is ScopeKind.FUNCTION:
            return self.functions.get(entry.qualified)
        return None

    def variable_type(self, module: str, name: str) -> IRType | None:
        entry = self.lookup(module, name)
        if entry is not None and entry.kind is ScopeKind.VARIABLE:
            declared = self.variables.get(entry.qualified, t_unknown())
            return self.qualify(declared, entry.module)
        return None
