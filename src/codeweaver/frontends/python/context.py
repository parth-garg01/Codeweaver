"""Shared conversion state for the Python frontend."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.ir.base import IRUnsupported

__all__ = ["ConversionContext"]


@dataclass
class ConversionContext:
    """Everything a converter needs besides the AST node itself.

    The context is what makes "nothing is silently dropped" enforceable: the
    only way to handle a construct the frontend cannot map is
    :meth:`unsupported`, which records a node *and* a diagnostic.
    """

    path: str
    source_lines: list[str] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    #: Names bound in the module or function scope being converted, used to
    #: tell a new binding apart from a re-assignment.
    bound_names: set[str] = field(default_factory=set)

    def span(self, node: ast.AST) -> SourceSpan:
        return SourceSpan(
            file=self.path,
            start_line=getattr(node, "lineno", 0) or 0,
            start_col=getattr(node, "col_offset", 0) or 0,
            end_line=getattr(node, "end_lineno", 0) or 0,
            end_col=getattr(node, "end_col_offset", 0) or 0,
        )

    def source_of(self, node: ast.AST) -> str:
        """The original text of *node*, preferring the real source slice."""
        try:
            if self.source_lines:
                segment = ast.get_source_segment("\n".join(self.source_lines), node)
                if segment:
                    return segment
            return ast.unparse(node)
        except (AttributeError, ValueError, TypeError):  # pragma: no cover - defensive
            return ""

    def unsupported(self, node: ast.AST, reason: str, hint: str = "") -> IRUnsupported:
        """Record an unmappable construct and return the node that replaces it."""
        source_kind = type(node).__name__
        span = self.span(node)
        self.diagnostics.warning(
            "CW0400",
            f"unsupported Python construct {source_kind}: {reason}",
            stage="frontend/python",
            span=span,
            hint=hint,
        )
        return IRUnsupported(
            source_kind=source_kind,
            reason=reason,
            source_text=self.source_of(node),
            hint=hint,
            span=span,
        )
