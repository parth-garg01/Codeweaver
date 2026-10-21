"""The dependency graph.

Nodes are modules and symbols; edges are the relationships CodeWeaver could
actually observe in the IR.  Two properties matter more than completeness:

* every edge records whether it was **resolved** to a project symbol.  An
  unresolved call to ``requests.get`` is kept as an edge to an external node,
  not dropped and not pretended to be internal;
* every edge carries a source span, so the planner's "this group has a cycle"
  can be traced back to the import that caused it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.graph.symbols import SymbolIndex, SymbolKind, build_symbol_index
from codeweaver.ir.base import IRNode, walk
from codeweaver.ir.declarations import IRClass, IRFunction, IRImport, IRModule
from codeweaver.ir.expressions import IRAttribute, IRCall, IRName
from codeweaver.ir.types import IRType, TypeKind

__all__ = [
    "DependencyGraph",
    "Edge",
    "EdgeKind",
    "Node",
    "NodeKind",
    "build_dependency_graph",
    "resolve_relative_import",
]


class NodeKind(enum.StrEnum):
    MODULE = "module"
    SYMBOL = "symbol"
    EXTERNAL = "external"


class EdgeKind(enum.StrEnum):
    IMPORT = "import"
    CALL = "call"
    INHERIT = "inherit"
    TYPE = "type"


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    kind: NodeKind
    label: str
    module: str = ""
    symbol_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "module": self.module,
            "symbolKind": self.symbol_kind,
        }


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    kind: EdgeKind
    resolved: bool = True
    span: SourceSpan | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind.value,
            "resolved": self.resolved,
            "span": self.span.to_dict() if self.span else None,
            "detail": self.detail,
        }


@dataclass
class DependencyGraph:
    """A queryable graph of modules, symbols and the edges between them."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    symbols: SymbolIndex = field(default_factory=SymbolIndex)
    _seen_edges: set[tuple[str, str, str]] = field(default_factory=set, repr=False)

    def add_node(self, node: Node) -> Node:
        self.nodes.setdefault(node.id, node)
        return self.nodes[node.id]

    def add_edge(self, edge: Edge) -> Edge | None:
        """Add *edge*, de-duplicating identical source/target/kind triples."""
        key = (edge.source, edge.target, edge.kind.value)
        if key in self._seen_edges:
            return None
        self._seen_edges.add(key)
        self.edges.append(edge)
        return edge

    def edges_of(self, kind: EdgeKind) -> list[Edge]:
        return [edge for edge in self.edges if edge.kind is kind]

    def unresolved_edges(self) -> list[Edge]:
        return [edge for edge in self.edges if not edge.resolved]

    def external_nodes(self) -> list[Node]:
        return sorted(
            (node for node in self.nodes.values() if node.kind is NodeKind.EXTERNAL),
            key=lambda node: node.id,
        )

    def module_nodes(self) -> list[Node]:
        return sorted(
            (node for node in self.nodes.values() if node.kind is NodeKind.MODULE),
            key=lambda node: node.id,
        )

    def successors(self, node_id: str, kinds: tuple[EdgeKind, ...] | None = None) -> list[str]:
        return sorted(
            {
                edge.target
                for edge in self.edges
                if edge.source == node_id and (kinds is None or edge.kind in kinds)
            }
        )

    def predecessors(self, node_id: str, kinds: tuple[EdgeKind, ...] | None = None) -> list[str]:
        return sorted(
            {
                edge.source
                for edge in self.edges
                if edge.target == node_id and (kinds is None or edge.kind in kinds)
            }
        )

    def module_adjacency(self) -> dict[str, set[str]]:
        """Module-to-module edges, with self-loops removed.

        Every edge is projected onto the modules of its endpoints, so a call
        from a function in ``a`` to a class in ``b`` contributes ``a -> b``.
        """
        adjacency: dict[str, set[str]] = {node.id: set() for node in self.module_nodes()}
        for edge in self.edges:
            source = self.module_of(edge.source)
            target = self.module_of(edge.target)
            if source and target and source != target and target in adjacency:
                adjacency.setdefault(source, set()).add(target)
        return adjacency

    def module_of(self, node_id: str) -> str:
        node = self.nodes.get(node_id)
        if node is None:
            return ""
        if node.kind is NodeKind.MODULE:
            return node.id
        return node.module

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [self.nodes[key].to_dict() for key in sorted(self.nodes)],
            "edges": [edge.to_dict() for edge in self.edges],
            "counts": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                "unresolved": len(self.unresolved_edges()),
                "external": len(self.external_nodes()),
            },
        }


def resolve_relative_import(current_module: str, module: str, level: int) -> str:
    """Resolve a relative import to an absolute dotted module name.

    ``level`` counts the leading dots.  One dot means "the package containing
    this module", so the module's own last component is dropped first.
    """
    if level <= 0:
        return module
    parts = current_module.split(".")
    trimmed = parts[: max(len(parts) - level, 0)]
    if module:
        trimmed = [*trimmed, *module.split(".")]
    return ".".join(part for part in trimmed if part)


def build_dependency_graph(
    modules: list[IRModule],
    *,
    symbols: SymbolIndex | None = None,
    diagnostics: DiagnosticBag | None = None,
) -> DependencyGraph:
    """Build the graph for *modules*."""
    index = symbols or build_symbol_index(modules)
    bag = diagnostics if diagnostics is not None else DiagnosticBag()
    graph = DependencyGraph(symbols=index)

    for symbol in (index.symbols[key] for key in sorted(index.symbols)):
        if symbol.kind is SymbolKind.MODULE:
            graph.add_node(Node(symbol.id, NodeKind.MODULE, symbol.name, symbol.module))
        else:
            graph.add_node(
                Node(symbol.id, NodeKind.SYMBOL, symbol.name, symbol.module, symbol.kind.value)
            )

    known_modules = set(index.modules())
    for module in sorted(modules, key=lambda item: item.name):
        _add_import_edges(graph, module, known_modules, bag)
        local = _local_bindings(module, index)
        for function in module.functions:
            _add_body_edges(graph, f"{module.name}.{function.name}", function, local)
        for klass in module.classes:
            _add_class_edges(graph, module, klass, local)
        for statement in module.body:
            _add_reference_edges(graph, module.name, statement, local)
    return graph


def _add_import_edges(
    graph: DependencyGraph,
    module: IRModule,
    known_modules: set[str],
    bag: DiagnosticBag,
) -> None:
    for imported in module.imports:
        target = resolve_relative_import(module.name, imported.module, imported.level)
        if target in known_modules:
            graph.add_edge(
                Edge(module.name, target, EdgeKind.IMPORT, True, imported.span, "module import")
            )
            _add_imported_symbol_edges(graph, module, imported, target, bag)
            continue
        imported.external = True
        external_id = f"external:{target or imported.module or '.'}"
        graph.add_node(Node(external_id, NodeKind.EXTERNAL, target or imported.module or "."))
        graph.add_edge(
            Edge(
                module.name,
                external_id,
                EdgeKind.IMPORT,
                False,
                imported.span,
                "not a project module: standard library or third-party",
            )
        )
        bag.info(
            "CW0500",
            f"import of {target or imported.module!r} in {module.name} is external",
            stage="graph",
            span=imported.span,
            hint="the target ecosystem may need a different package",
        )


def _add_imported_symbol_edges(
    graph: DependencyGraph,
    module: IRModule,
    imported: IRImport,
    target_module: str,
    bag: DiagnosticBag,
) -> None:
    for name in imported.names:
        symbol = graph.symbols.resolve_in_module(target_module, name.name)
        if symbol is not None:
            graph.add_edge(
                Edge(
                    module.name,
                    symbol.id,
                    EdgeKind.IMPORT,
                    True,
                    imported.span,
                    f"imports {name.name}",
                )
            )
        else:
            bag.warning(
                "CW0501",
                f"{module.name} imports {name.name!r} from {target_module}, "
                "which does not declare it",
                stage="graph",
                span=imported.span,
                hint="the name may be re-exported, dynamic, or a typo",
            )


def _local_bindings(module: IRModule, index: SymbolIndex) -> dict[str, str]:
    """Map names visible in *module* onto the symbol ids they refer to."""
    bindings: dict[str, str] = {}
    for symbol in index.top_level_of(module.name):
        bindings[symbol.name] = symbol.id
    for imported in module.imports:
        target = resolve_relative_import(module.name, imported.module, imported.level)
        for name in imported.names:
            imported_symbol = index.resolve_in_module(target, name.name)
            if imported_symbol is not None:
                bindings[name.local_name] = imported_symbol.id
        if imported.imports_whole_module and target in set(index.modules()):
            # Use the binding the IR defines, so the graph and the backends
            # cannot disagree about the local name an import introduced.
            bindings[imported.local_binding] = target
    return bindings


def _add_class_edges(
    graph: DependencyGraph, module: IRModule, klass: IRClass, local: dict[str, str]
) -> None:
    class_id = f"{module.name}.{klass.name}"
    for base in klass.bases:
        name = _reference_name(base)
        target = local.get(name)
        if target:
            graph.add_edge(
                Edge(class_id, target, EdgeKind.INHERIT, True, base.span, f"extends {name}")
            )
        elif name:
            graph.add_edge(
                Edge(
                    class_id,
                    f"external:{name}",
                    EdgeKind.INHERIT,
                    False,
                    base.span,
                    f"base class {name} is not a project symbol",
                )
            )
            graph.add_node(Node(f"external:{name}", NodeKind.EXTERNAL, name))
    for item in klass.fields:
        _add_type_edge(graph, class_id, item.field_type, local, item.span)
    for method in klass.methods:
        _add_body_edges(graph, f"{class_id}.{method.name}", method, local)


def _add_body_edges(
    graph: DependencyGraph,
    owner_id: str,
    function: IRFunction,
    local: dict[str, str],
) -> None:
    for param in function.params:
        _add_type_edge(graph, owner_id, param.param_type, local, param.span)
    _add_type_edge(graph, owner_id, function.return_type, local, function.span)
    _add_reference_edges(graph, owner_id, function.body, local)


def _add_reference_edges(
    graph: DependencyGraph,
    owner_id: str,
    root: IRNode,
    local: dict[str, str],
) -> None:
    if owner_id not in graph.nodes:
        return
    for node in walk(root):
        if not isinstance(node, IRCall):
            continue
        name = _reference_name(node.callee)
        if not name:
            continue
        target = local.get(name)
        if target:
            graph.add_edge(Edge(owner_id, target, EdgeKind.CALL, True, node.span, f"calls {name}"))


def _add_type_edge(
    graph: DependencyGraph,
    owner_id: str,
    ir_type: IRType,
    local: dict[str, str],
    span: SourceSpan | None,
) -> None:
    if owner_id not in graph.nodes:
        return
    for name in _object_type_names(ir_type):
        target = local.get(name)
        if target:
            graph.add_edge(
                Edge(owner_id, target, EdgeKind.TYPE, True, span, f"references type {name}")
            )


def _object_type_names(ir_type: IRType) -> list[str]:
    names: list[str] = []
    if ir_type.kind is TypeKind.OBJECT and ir_type.name:
        names.append(ir_type.name)
    for argument in ir_type.args:
        names.extend(_object_type_names(argument))
    return names


def _reference_name(expression: IRNode) -> str:
    """The leading name of a reference expression, if it has one."""
    if isinstance(expression, IRName):
        return expression.name
    if isinstance(expression, IRAttribute):
        return _reference_name(expression.target)
    return ""
