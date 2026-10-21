"""Emission context for the Go backend."""

from __future__ import annotations

from dataclasses import dataclass, field

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.ir.base import IRUnsupported

__all__ = ["UNSUPPORTED_MARKER", "GoEmitContext"]

UNSUPPORTED_MARKER = "CODEWEAVER-UNSUPPORTED"


@dataclass
class GoEmitContext:
    """Diagnostics and bookkeeping for one generated Go file."""

    source_path: str = ""
    output_path: str = ""
    package: str = "main"
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    gaps: list[IRUnsupported] = field(default_factory=list)
    #: Standard-library packages this file turned out to need.  Collected while
    #: emitting rather than guessed up front, because Go rejects an unused
    #: import — a speculative ``import "fmt"`` would make the file fail to
    #: build.
    imports: set[str] = field(default_factory=set)
    #: Method receiver name in the body being emitted, if any.
    receiver: str = ""
    #: Source name -> Go name, for every name this file may refer to.  Go
    #: decides visibility by capitalisation, so references have to be rewritten
    #: consistently with declarations.
    renames: dict[str, str] = field(default_factory=dict)

    def need(self, package: str) -> str:
        self.imports.add(package)
        return package

    def record_gap(self, node: IRUnsupported) -> str:
        self.gaps.append(node)
        self.diagnostics.warning(
            "CW0700",
            f"unsupported construct {node.source_kind!r} left as a marker in generated Go",
            stage="backend/go",
            span=node.span,
            hint=node.hint or node.reason,
        )
        detail = node.reason.replace("*/", "* /")
        return f"/* {UNSUPPORTED_MARKER}: {node.source_kind}: {detail} */"

    def warn(self, code: str, message: str, span: SourceSpan | None = None, hint: str = "") -> None:
        self.diagnostics.warning(code, message, stage="backend/go", span=span, hint=hint)

    def rename(self, source: str) -> str:
        """The Go name for a source name, or the name unchanged."""
        return self.renames.get(source, source)
