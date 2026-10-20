"""IR node foundation.

Every IR node is a dataclass carrying an optional :class:`SourceSpan` and a
free-form ``meta`` dictionary.  Two rules keep the representation honest:

* ``kind`` is a stable string used in the serialised form, so a checkpoint
  written by one build can be validated by another.
* nothing is ever dropped.  A construct a frontend cannot map becomes an
  :class:`IRUnsupported` node that records what it was and why, which is what
  lets the migration report list unsupported constructs instead of silently
  producing an incomplete translation.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

from codeweaver.diagnostics import SourceSpan
from codeweaver.errors import IRSchemaError
from codeweaver.ir.types import IRType

__all__ = [
    "IRNode",
    "IRUnsupported",
    "decode_value",
    "encode_value",
    "ir_node",
    "node_class",
    "node_from_dict",
    "registered_kinds",
    "walk",
]

#: kind -> node class.  Populated by the :func:`ir_node` decorator.
_NODE_TYPES: dict[str, type[IRNode]] = {}

T = TypeVar("T", bound="type[IRNode]")


@dataclass
class IRNode:
    """Base class for every IR node."""

    kind: ClassVar[str] = "node"

    span: SourceSpan | None = field(default=None, kw_only=True)
    meta: dict[str, Any] = field(default_factory=dict, kw_only=True)

    def fields(self) -> list[dataclasses.Field[Any]]:
        """Payload fields, excluding ``span`` and ``meta``."""
        return [f for f in dataclasses.fields(self) if f.name not in ("span", "meta")]

    def children(self) -> list[IRNode]:
        """Direct child nodes, in declaration order."""
        found: list[IRNode] = []
        for info in self.fields():
            value = getattr(self, info.name)
            if isinstance(value, IRNode):
                found.append(value)
            elif isinstance(value, list | tuple):
                found.extend(item for item in value if isinstance(item, IRNode))
        return found

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        for info in self.fields():
            payload[info.name] = encode_value(getattr(self, info.name))
        if self.span is not None:
            payload["span"] = self.span.to_dict()
        if self.meta:
            payload["meta"] = dict(self.meta)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IRNode:
        kwargs: dict[str, Any] = {}
        for info in dataclasses.fields(cls):
            if info.name in ("span", "meta"):
                continue
            if info.name in data:
                kwargs[info.name] = decode_value(data[info.name])
        span = data.get("span")
        return cls(  # type: ignore[call-arg]
            **kwargs,
            span=SourceSpan.from_dict(span) if span else None,
            meta=dict(data.get("meta") or {}),
        )


def ir_node(cls: T) -> T:
    """Register an IR node class under its ``kind``."""
    kind = cls.kind
    existing = _NODE_TYPES.get(kind)
    if existing is not None and existing is not cls:
        raise IRSchemaError(f"duplicate IR node kind {kind!r}: {existing!r} and {cls!r}")
    _NODE_TYPES[kind] = cls
    return cls


def node_class(kind: str) -> type[IRNode]:
    try:
        return _NODE_TYPES[kind]
    except KeyError:
        raise IRSchemaError(f"unknown IR node kind: {kind!r}") from None


def registered_kinds() -> list[str]:
    return sorted(_NODE_TYPES)


def encode_value(value: Any) -> Any:
    if isinstance(value, IRNode):
        return value.to_dict()
    if isinstance(value, IRType):
        return value.to_dict()
    if isinstance(value, SourceSpan):
        return value.to_dict()
    if isinstance(value, list | tuple):
        return [encode_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): encode_value(item) for key, item in value.items()}
    return value


def decode_value(value: Any) -> Any:
    if isinstance(value, dict):
        kind = value.get("kind")
        if kind == "type":
            return IRType.from_dict(value)
        if isinstance(kind, str):
            return node_from_dict(value)
        return {str(key): decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    return value


def node_from_dict(data: dict[str, Any]) -> IRNode:
    """Rebuild a node from its serialised form."""
    if not isinstance(data, dict) or "kind" not in data:
        raise IRSchemaError("IR node payload must be an object with a 'kind' field")
    return node_class(str(data["kind"])).from_dict(data)


def walk(node: IRNode):
    """Yield *node* and every descendant, depth first, parents before children."""
    yield node
    for child in node.children():
        yield from walk(child)


@ir_node
@dataclass
class IRUnsupported(IRNode):
    """A construct that has no mapping in this build.

    The node is a real part of the tree: backends emit an explicit marker for
    it and the report counts it.  ``source_kind`` is the frontend's own name for
    the construct (for example ``"Yield"``), which keeps the diagnostic specific
    enough to act on.
    """

    kind: ClassVar[str] = "unsupported"

    source_kind: str
    reason: str
    source_text: str = ""
    hint: str = ""
