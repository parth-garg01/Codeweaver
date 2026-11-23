"""Per-file emission state shared by the C-family backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.ir.base import IRUnsupported

__all__ = ["UNSUPPORTED_MARKER", "EmitContext"]

#: Marker written into generated source wherever an unsupported construct was.
UNSUPPORTED_MARKER = "CODEWEAVER-UNSUPPORTED"


@dataclass
class EmitContext:
    """Diagnostics and bookkeeping for one generated module.

    ``stage`` names the backend in every diagnostic so the report can say which
    target produced it.  Backends subclass this to add their own state.
    """

    stage: str = "backend"
    source_path: str = ""
    output_path: str = ""
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    gaps: list[IRUnsupported] = field(default_factory=list)
    #: Headers, imports or runtime features the generated file needs.
    requires: set[str] = field(default_factory=set)
    #: Diagnostic codes already reported at a given position, so a construct
    #: visited twice (a hoisted temporary, a duplicated overload) is reported
    #: once.
    _reported: set[tuple[str, str, int, int]] = field(default_factory=set)

    def marker(self, node: IRUnsupported) -> str:
        detail = node.reason.replace("*/", "* /")
        return f"/* {UNSUPPORTED_MARKER}: {node.source_kind}: {detail} */"

    def record_gap(self, node: IRUnsupported) -> str:
        """Record an unsupported node and return the marker comment text."""
        self.gaps.append(node)
        self.warn(
            "CW0600",
            f"unsupported construct {node.source_kind!r} left as a marker in generated code",
            span=node.span,
            hint=node.hint or node.reason,
        )
        return self.marker(node)

    def gap(self, kind: str, reason: str, span: SourceSpan | None, hint: str = "") -> str:
        """Record a construct the *backend* cannot express, and return a marker."""
        return self.record_gap(IRUnsupported(kind, reason, hint=hint, span=span))

    def warn(self, code: str, message: str, span: SourceSpan | None = None, hint: str = "") -> None:
        key = (code, span.file if span else "", span.start_line if span else 0, span.start_col if span else 0)
        if span is not None and key in self._reported:
            return
        self._reported.add(key)
        self.diagnostics.warning(code, message, stage=self.stage, span=span, hint=hint)

    def need(self, feature: str) -> str:
        self.requires.add(feature)
        return feature

    def snapshot(self) -> dict[str, Any]:
        return {"gaps": len(self.gaps), "requires": sorted(self.requires)}
