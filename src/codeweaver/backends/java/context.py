"""Project layout and per-file emission state for the Java backend.

Java wants one public class per file and a package per directory, so a
Python module ``app/algo.py`` becomes:

* package ``app``;
* one file per Python class (``app/Shape.java``);
* a final class for the module's functions and variables, named after the
  module in Pascal case (``app/Algo.java``), holding ``main`` when the module
  has an ``if __name__ == "__main__":`` block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from codeweaver.backends.common.analysis import ParamMutability, is_main_guard
from codeweaver.backends.common.context import EmitContext
from codeweaver.backends.java.naming import class_identifier, module_class_name, safe_identifier
from codeweaver.graph.project_index import ClassInfo, ProjectIndex, ScopeEntry
from codeweaver.ir.declarations import IRClass, IRFunction, IRModule

__all__ = ["SOURCE_ROOT", "JavaContext", "JavaLayout", "LoopFrame"]

SOURCE_ROOT = "src/main/java"
RUNTIME_PATH = "cwrt/Py.java"


@dataclass
class JavaLayout:
    index: ProjectIndex
    #: module name -> Java package ("" for the default package)
    packages: dict[str, str] = field(default_factory=dict)
    #: module name -> simple name of the class holding its functions
    module_classes: dict[str, str] = field(default_factory=dict)
    #: qualified Python class name -> simple Java class name
    class_names: dict[str, str] = field(default_factory=dict)
    entry_modules: list[str] = field(default_factory=list)
    mutability: ParamMutability = field(default_factory=ParamMutability)

    @classmethod
    def build(cls, modules: list[IRModule], index: ProjectIndex) -> JavaLayout:
        layout = cls(index=index)
        taken: dict[str, set[str]] = {}
        ordered = sorted(modules, key=lambda item: item.name)
        for module in ordered:
            path = PurePosixPath(module.path or module.name.replace(".", "/") + ".py")
            package = ".".join(
                safe_identifier(part) for part in path.parent.parts if part not in ("", ".")
            )
            layout.packages[module.name] = package
            names = taken.setdefault(package, set())
            for klass in module.classes:
                simple = class_identifier(klass.name)
                if simple in names:
                    simple = class_identifier(module_class_name(module.name, set()) + klass.name)
                names.add(simple)
                layout.class_names[f"{module.name}.{klass.name}"] = simple
        for module in ordered:
            names = taken[layout.packages[module.name]]
            simple = module_class_name(module.name, names)
            names.add(simple)
            layout.module_classes[module.name] = simple
            if any(is_main_guard(statement) for statement in module.body):
                layout.entry_modules.append(module.name)
        layout.mutability = ParamMutability.compute(index)
        return layout

    def file_for(self, module: str, simple: str) -> str:
        package = self.packages[module]
        directory = package.replace(".", "/")
        return (
            f"{SOURCE_ROOT}/{directory}/{simple}.java"
            if directory
            else f"{SOURCE_ROOT}/{simple}.java"
        )

    def qualified_class(self, module: str, simple: str) -> str:
        package = self.packages[module]
        return f"{package}.{simple}" if package else simple


@dataclass
class LoopFrame:
    broke: str = ""


@dataclass
class JavaContext(EmitContext):
    """State for generating one Java file."""

    stage: str = "backend/java"
    layout: JavaLayout | None = None
    index: ProjectIndex | None = None
    module: IRModule = field(default_factory=lambda: IRModule(""))
    #: Simple name of the Java class this file defines.
    file_class: str = ""
    #: The module whose functions are emitted unqualified in this file.
    is_module_class: bool = False
    klass: ClassInfo | None = None
    function: IRFunction | None = None
    receiver: str = ""
    class_receiver: str = ""
    locals: set[str] = field(default_factory=set)
    unchecked: frozenset[tuple[str, str]] = frozenset()
    loops: list[LoopFrame] = field(default_factory=list)
    escaping: set[str] = field(default_factory=set)
    declared: set[str] = field(default_factory=set)
    #: Locals assigned more than once, which a Java lambda cannot capture.
    reassigned: set[str] = field(default_factory=set)
    #: Statements to emit before the statement being rendered.
    pre: list[str] = field(default_factory=list)
    #: Names a lambda body must spell differently (captured copies).
    renames: dict[str, str] = field(default_factory=dict)
    #: Fully qualified classes this file imports.
    imports: set[str] = field(default_factory=set)
    #: Names of the exceptions being handled, innermost last (for bare raise).
    active_errors: list[str] = field(default_factory=list)
    _counter: int = 0

    def temp(self, prefix: str) -> str:
        self._counter += 1
        return f"cw{prefix.capitalize()}{self._counter}"

    @property
    def package(self) -> str:
        return self.layout.packages.get(self.module.name, "") if self.layout else ""

    def scope_entry(self, name: str) -> ScopeEntry | None:
        if self.index is None or name in self.locals:
            return None
        return self.index.lookup(self.module.name, name)

    def _import(self, module: str, simple: str) -> None:
        if self.layout is None:
            return
        package = self.layout.packages.get(module, "")
        if package and package != self.package:
            self.imports.add(f"{package}.{simple}")

    def class_spelling(self, info: ClassInfo) -> str:
        if self.layout is None:
            return class_identifier(info.name)
        simple = self.layout.class_names.get(info.qualified, class_identifier(info.name))
        self._import(info.module, simple)
        return simple

    def module_class(self, module: str) -> str:
        if self.layout is None:
            return module
        simple = self.layout.module_classes[module]
        self._import(module, simple)
        return simple

    def member_spelling(self, entry: ScopeEntry) -> str:
        """A module-level function or variable, as seen from this file."""
        name = safe_identifier(entry.name)
        if self.is_module_class and entry.module == self.module.name:
            return name
        return f"{self.module_class(entry.module)}.{name}"

    def class_of(self, node_type) -> ClassInfo | None:
        if self.index is None:
            return None
        return self.index.class_for_type(node_type, self.module.name)

    def own_class(self, klass: IRClass) -> ClassInfo | None:
        if self.index is None:
            return None
        return self.index.classes.get(f"{self.module.name}.{klass.name}")

    def local(self, name: str) -> str:
        return self.renames.get(name) or safe_identifier(name)
