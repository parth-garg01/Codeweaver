"""Graph analysis: strongly connected components, cycles and migration order.

Real projects have import cycles.  A planner that assumed a DAG would either
crash or silently pick an order that cannot work, so CodeWeaver condenses the
graph into strongly connected components and plans over the condensation.  Each
component is one migration group: modules inside a group must be generated
together because no ordering of them is correct on its own.

Tarjan's algorithm is implemented iteratively.  A deep import chain in a large
project would otherwise exhaust the interpreter's recursion limit, turning a
supported project into a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "DependencyGroup",
    "find_cycles",
    "strongly_connected_components",
    "topological_groups",
]


@dataclass(frozen=True, slots=True)
class DependencyGroup:
    """One migration group: a set of modules with no valid internal ordering."""

    index: int
    members: tuple[str, ...]
    #: Indices of groups this group depends on.  Always lower than ``index``.
    depends_on: tuple[int, ...] = ()

    @property
    def is_cycle(self) -> bool:
        return len(self.members) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "members": list(self.members),
            "dependsOn": list(self.depends_on),
            "isCycle": self.is_cycle,
        }


def strongly_connected_components(adjacency: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Return the SCCs of *adjacency* in reverse topological order.

    "Reverse topological" means a component appears before any component that
    depends on it, which is exactly migration order.  Members within a
    component are sorted so the result is deterministic.
    """
    nodes = sorted(adjacency)
    successors = {node: sorted(adjacency.get(node, set()) & set(nodes)) for node in nodes}

    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[tuple[str, ...]] = []
    counter = 0

    for root in nodes:
        if root in index_of:
            continue
        # Each frame is (node, iterator position into its successors).
        work: list[tuple[str, int]] = [(root, 0)]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            node, position = work[-1]
            children = successors[node]
            if position < len(children):
                work[-1] = (node, position + 1)
                child = children[position]
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(tuple(sorted(component)))
    return components


def topological_groups(adjacency: dict[str, set[str]]) -> list[DependencyGroup]:
    """Condense *adjacency* into ordered :class:`DependencyGroup` values."""
    components = strongly_connected_components(adjacency)
    group_of: dict[str, int] = {}
    for index, component in enumerate(components):
        for member in component:
            group_of[member] = index

    groups: list[DependencyGroup] = []
    for index, component in enumerate(components):
        dependencies: set[int] = set()
        for member in component:
            for target in adjacency.get(member, set()):
                other = group_of.get(target)
                if other is not None and other != index:
                    dependencies.add(other)
        groups.append(DependencyGroup(index, component, tuple(sorted(dependencies))))
    return groups


def find_cycles(adjacency: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Return every component that forms a cycle, including self-loops."""
    cycles = [
        component for component in strongly_connected_components(adjacency) if len(component) > 1
    ]
    cycles.extend((node,) for node in sorted(adjacency) if node in adjacency.get(node, set()))
    return cycles
