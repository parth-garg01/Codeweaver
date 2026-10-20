"""IR serialisation.

IR leaves the process in exactly one shape: a JSON document wrapping the node
tree in an envelope that names the schema version and the producing tool.  A
reader that cannot understand the version refuses the document rather than
interpreting unfamiliar fields.

Output is deterministic: keys are sorted and indentation is fixed, so two runs
over the same source produce byte-identical IR and a diff of two checkpoints is
meaningful.
"""

from __future__ import annotations

import json
from typing import Any

from codeweaver import __version__
from codeweaver.errors import IRSchemaError
from codeweaver.ir.base import IRNode, node_from_dict
from codeweaver.ir.version import IR_SCHEMA_VERSION, is_compatible

__all__ = [
    "ENVELOPE_KIND",
    "dumps",
    "loads",
    "to_envelope",
    "from_envelope",
]

ENVELOPE_KIND = "codeweaver.ir"


def to_envelope(node: IRNode, **extra: Any) -> dict[str, Any]:
    """Wrap *node* in a self-describing envelope."""
    envelope: dict[str, Any] = {
        "envelope": ENVELOPE_KIND,
        "schemaVersion": IR_SCHEMA_VERSION,
        "producer": f"codeweaver/{__version__}",
        "root": node.to_dict(),
    }
    envelope.update(extra)
    return envelope


def from_envelope(envelope: dict[str, Any]) -> IRNode:
    """Unwrap and rebuild the node tree, checking the schema version first."""
    if not isinstance(envelope, dict):
        raise IRSchemaError("IR envelope must be a JSON object")
    if envelope.get("envelope") != ENVELOPE_KIND:
        raise IRSchemaError(f"not a CodeWeaver IR document (envelope={envelope.get('envelope')!r})")
    version = str(envelope.get("schemaVersion", ""))
    if not is_compatible(version):
        raise IRSchemaError(
            f"IR schema version {version or '<missing>'} cannot be read by this build "
            f"(supports {IR_SCHEMA_VERSION}); re-run the analysis to regenerate it"
        )
    root = envelope.get("root")
    if not isinstance(root, dict):
        raise IRSchemaError("IR envelope is missing its 'root' node")
    return node_from_dict(root)


def dumps(node: IRNode, *, indent: int | None = 2, **extra: Any) -> str:
    """Serialise *node* to deterministic JSON text."""
    return json.dumps(
        to_envelope(node, **extra),
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
        default=_fallback,
    )


def loads(text: str) -> IRNode:
    """Parse JSON *text* produced by :func:`dumps`."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IRSchemaError(f"IR document is not valid JSON: {exc}") from exc
    return from_envelope(payload)


def _fallback(value: Any) -> Any:
    """Last-resort encoder.

    Reaching this means a node holds a value the IR does not define a shape
    for.  Encoding its repr keeps the document readable, and the schema
    validator reports the node so the gap gets fixed rather than hidden.
    """
    return {"unencodable": repr(value), "pythonType": type(value).__name__}
