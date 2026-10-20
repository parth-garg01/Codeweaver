"""Symbol index, dependency graph and the analyses built on them."""

from __future__ import annotations

from codeweaver.graph.analysis import (
    DependencyGroup,
    find_cycles,
    strongly_connected_components,
    topological_groups,
)
from codeweaver.graph.dependency import (
    DependencyGraph,
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    build_dependency_graph,
    resolve_relative_import,
)
from codeweaver.graph.impact import ImpactReport, analyse_impact, search_nodes
from codeweaver.graph.symbols import Symbol, SymbolIndex, SymbolKind, build_symbol_index

__all__ = [
    "DependencyGraph",
    "DependencyGroup",
    "Edge",
    "EdgeKind",
    "ImpactReport",
    "Node",
    "NodeKind",
    "Symbol",
    "SymbolIndex",
    "SymbolKind",
    "analyse_impact",
    "build_dependency_graph",
    "build_symbol_index",
    "find_cycles",
    "resolve_relative_import",
    "search_nodes",
    "strongly_connected_components",
    "topological_groups",
]
