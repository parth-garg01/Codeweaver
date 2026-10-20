"""Generic bottom-up IR rewriting.

Every transform needs the same thing: visit a tree, replace some nodes, and get
an equivalent tree back without hand-writing a traversal per node type.  The
rewriter walks the dataclass fields of each node, so it keeps working when new
node kinds are added — a transform that only cares about ``IRForEach`` does not
have to know that ``IRTry`` exists.

Rewriting is bottom-up: children are rewritten before their parent sees them,
so a transform can match on already-simplified children.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

from codeweaver.ir.base import IRNode

__all__ = ["count_nodes", "rewrite", "rewrite_all"]

Rewriter = Callable[[IRNode], IRNode | None]


def rewrite(node: IRNode, rewriter: Rewriter) -> IRNode:
    """Rewrite *node* bottom-up.

    *rewriter* is called with each node after its children have been rewritten.
    Returning ``None`` means "leave this node alone"; returning a node replaces
    it.  A replacement is not re-visited, which keeps a rewriter that produces
    nodes it also matches from looping forever.
    """
    rebuilt = _rebuild_children(node, rewriter)
    replacement = rewriter(rebuilt)
    return replacement if replacement is not None else rebuilt


def rewrite_all(nodes: list[IRNode], rewriter: Rewriter) -> list[IRNode]:
    return [rewrite(node, rewriter) for node in nodes]


def _rebuild_children(node: IRNode, rewriter: Rewriter) -> IRNode:
    changes: dict[str, object] = {}
    for info in node.payload_fields():
        value = getattr(node, info.name)
        if isinstance(value, IRNode):
            rewritten = rewrite(value, rewriter)
            if rewritten is not value:
                changes[info.name] = rewritten
        elif isinstance(value, list) and any(isinstance(item, IRNode) for item in value):
            rewritten_list = [
                rewrite(item, rewriter) if isinstance(item, IRNode) else item for item in value
            ]
            if any(new is not old for new, old in zip(rewritten_list, value, strict=True)):
                changes[info.name] = rewritten_list
    if not changes:
        return node
    return dataclasses.replace(node, **changes)


def count_nodes(node: IRNode, kind: str) -> int:
    """How many nodes of *kind* the tree rooted at *node* contains."""
    from codeweaver.ir.base import walk

    return sum(1 for item in walk(node) if item.kind == kind)
