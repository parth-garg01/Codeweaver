"""Impact analysis and graph queries.

When an interface or an IR node changes, the useful question is "what else has
to be re-checked?".  That is reverse reachability over the dependency graph.
The answer is reported with the distance from the change, because a direct
caller and something six hops away need different amounts of attention.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from codeweaver.graph.dependency import DependencyGraph, EdgeKind

__all__ = ["ImpactReport", "analyse_impact", "search_nodes"]


@dataclass
class ImpactReport:
    """What depends on a node, and what it depends on."""

    origin: str
    #: node id -> hops from the origin, for nodes that depend on it.
    dependents: dict[str, int] = field(default_factory=dict)
    #: node id -> hops from the origin, for nodes it depends on.
    dependencies: dict[str, int] = field(default_factory=dict)
    #: ``False`` when the origin is not in the graph at all.
    found: bool = True

    @property
    def affected_modules(self) -> list[str]:
        return sorted({module for module in self._modules(self.dependents) if module})

    @property
    def direct_dependents(self) -> list[str]:
        return sorted(node for node, hops in self.dependents.items() if hops == 1)

    def _modules(self, mapping: dict[str, int]) -> list[str]:
        return [self._module_of.get(node, "") for node in mapping]

    _module_of: dict[str, str] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "found": self.found,
            "dependents": dict(sorted(self.dependents.items())),
            "dependencies": dict(sorted(self.dependencies.items())),
            "directDependents": self.direct_dependents,
            "affectedModules": self.affected_modules,
        }


def analyse_impact(
    graph: DependencyGraph,
    origin: str,
    *,
    kinds: tuple[EdgeKind, ...] | None = None,
    max_depth: int = 0,
) -> ImpactReport:
    """Report what *origin* affects and what it relies on.

    *max_depth* of ``0`` means unbounded.  Only resolved edges are traversed:
    an unresolved edge to an external package tells us nothing about what else
    in the project needs re-checking.
    """
    report = ImpactReport(
        origin=origin,
        found=origin in graph.nodes,
        _module_of={node.id: graph.module_of(node.id) for node in graph.nodes.values()},
    )
    if not report.found:
        return report
    report.dependents = _reachable(graph, origin, forward=False, kinds=kinds, max_depth=max_depth)
    report.dependencies = _reachable(graph, origin, forward=True, kinds=kinds, max_depth=max_depth)
    return report


def _reachable(
    graph: DependencyGraph,
    origin: str,
    *,
    forward: bool,
    kinds: tuple[EdgeKind, ...] | None,
    max_depth: int,
) -> dict[str, int]:
    adjacency: dict[str, set[str]] = {}
    for edge in graph.edges:
        if not edge.resolved:
            continue
        if kinds is not None and edge.kind not in kinds:
            continue
        source, target = (edge.source, edge.target) if forward else (edge.target, edge.source)
        adjacency.setdefault(source, set()).add(target)

    found: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque([(origin, 0)])
    visited = {origin}
    while queue:
        node, depth = queue.popleft()
        if max_depth and depth >= max_depth:
            continue
        for neighbour in sorted(adjacency.get(node, set())):
            if neighbour in visited:
                continue
            visited.add(neighbour)
            found[neighbour] = depth + 1
            queue.append((neighbour, depth + 1))
    return found


def search_nodes(graph: DependencyGraph, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """Case-insensitive substring search over node ids and labels."""
    needle = query.strip().lower()
    if not needle:
        return []
    matches = [
        node
        for node in graph.nodes.values()
        if needle in node.id.lower() or needle in node.label.lower()
    ]
    matches.sort(key=lambda node: (len(node.id), node.id))
    return [node.to_dict() for node in matches[:limit]]
