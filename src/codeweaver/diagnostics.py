"""Shared diagnostic model.

Every stage of the pipeline reports problems as :class:`Diagnostic` values
rather than raising or printing.  Diagnostics carry a stable machine-readable
``code``, a severity, and — whenever the information is available — the exact
source location the problem came from.  This is what lets the CLI, the UI and
the migration report present the same facts without re-deriving them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Diagnostic",
    "DiagnosticBag",
    "Severity",
    "SourceSpan",
]


class Severity(enum.StrEnum):
    """How much a diagnostic should affect the outcome of a run."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "info": 1}[self.value]


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """A half-open region of a source file.

    Lines are 1-based and columns are 0-based, matching Python's ``ast`` module
    so that spans can be produced without conversion arithmetic.
    """

    file: str
    start_line: int = 0
    start_col: int = 0
    end_line: int = 0
    end_col: int = 0

    def __str__(self) -> str:
        if not self.start_line:
            return self.file
        return f"{self.file}:{self.start_line}:{self.start_col + 1}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "startLine": self.start_line,
            "startCol": self.start_col,
            "endLine": self.end_line,
            "endCol": self.end_col,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceSpan:
        return cls(
            file=data["file"],
            start_line=data.get("startLine", 0),
            start_col=data.get("startCol", 0),
            end_line=data.get("endLine", 0),
            end_col=data.get("endCol", 0),
        )


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single reported fact about a project, an IR tree or a generated file."""

    code: str
    message: str
    severity: Severity = Severity.ERROR
    span: SourceSpan | None = None
    stage: str = ""
    hint: str = ""

    def format(self) -> str:
        location = f"{self.span} " if self.span else ""
        hint = f"\n    hint: {self.hint}" if self.hint else ""
        return f"{location}{self.severity.value}[{self.code}]: {self.message}{hint}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "span": self.span.to_dict() if self.span else None,
            "stage": self.stage,
            "hint": self.hint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Diagnostic:
        span = data.get("span")
        return cls(
            code=data["code"],
            message=data["message"],
            severity=Severity(data.get("severity", "error")),
            span=SourceSpan.from_dict(span) if span else None,
            stage=data.get("stage", ""),
            hint=data.get("hint", ""),
        )


@dataclass
class DiagnosticBag:
    """An ordered, de-duplicated collection of diagnostics."""

    items: list[Diagnostic] = field(default_factory=list)
    _seen: set[tuple[str, str, str]] = field(default_factory=set, repr=False)

    def add(self, diagnostic: Diagnostic) -> Diagnostic:
        key = (diagnostic.code, diagnostic.message, str(diagnostic.span))
        if key not in self._seen:
            self._seen.add(key)
            self.items.append(diagnostic)
        return diagnostic

    def error(self, code: str, message: str, **kwargs: Any) -> Diagnostic:
        return self.add(Diagnostic(code, message, Severity.ERROR, **kwargs))

    def warning(self, code: str, message: str, **kwargs: Any) -> Diagnostic:
        return self.add(Diagnostic(code, message, Severity.WARNING, **kwargs))

    def info(self, code: str, message: str, **kwargs: Any) -> Diagnostic:
        return self.add(Diagnostic(code, message, Severity.INFO, **kwargs))

    def extend(self, diagnostics: list[Diagnostic]) -> None:
        for diagnostic in diagnostics:
            self.add(diagnostic)

    def of(self, severity: Severity) -> list[Diagnostic]:
        return [item for item in self.items if item.severity is severity]

    @property
    def errors(self) -> list[Diagnostic]:
        return self.of(Severity.ERROR)

    @property
    def warnings(self) -> list[Diagnostic]:
        return self.of(Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        return any(item.severity is Severity.ERROR for item in self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def to_list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.items]
