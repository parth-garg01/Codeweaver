"""Emission context shared by the TypeScript backend's generators."""

from __future__ import annotations

from dataclasses import dataclass, field

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.ir.base import IRUnsupported

__all__ = ["EmitContext", "UNSUPPORTED_MARKER"]

#: Marker written into generated source wherever an unsupported construct was.
#: It is a comment plus a value, so the file still parses and the compiler
#: still type-checks the code around the gap.
UNSUPPORTED_MARKER = "CODEWEAVER-UNSUPPORTED"


@dataclass
class EmitContext:
    """Diagnostics and bookkeeping for one generated module."""

    source_path: str = ""
    output_path: str = ""
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    #: Unsupported constructs encountered while generating this module.
    gaps: list[IRUnsupported] = field(default_factory=list)
    #: Runtime helpers the generated module needs (emitted as a prelude).
    helpers: set[str] = field(default_factory=set)
    #: Name of the method receiver in the body being emitted (Python's
    #: ``self``/``cls``).  References to it become ``this``.
    receiver: str = ""

    def record_gap(self, node: IRUnsupported) -> str:
        """Record an unsupported node and return the marker comment text."""
        self.gaps.append(node)
        self.diagnostics.warning(
            "CW0600",
            f"unsupported construct {node.source_kind!r} left as a marker in generated code",
            stage="backend/typescript",
            span=node.span,
            hint=node.hint or node.reason,
        )
        detail = node.reason.replace("*/", "* /")
        return f"/* {UNSUPPORTED_MARKER}: {node.source_kind}: {detail} */"

    def warn(self, code: str, message: str, span: SourceSpan | None = None, hint: str = "") -> None:
        self.diagnostics.warning(code, message, stage="backend/typescript", span=span, hint=hint)

    def need_helper(self, name: str) -> str:
        self.helpers.add(name)
        return name
