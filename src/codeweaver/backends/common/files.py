"""The result of generating one target file, shared by every backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.base import IRUnsupported

__all__ = ["GeneratedFile"]


@dataclass
class GeneratedFile:
    """One generated target file and everything learned while generating it."""

    path: str
    source_path: str
    contents: str
    mappings: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    unsupported_count: int = 0
    #: The constructs counted by ``unsupported_count``, for the report.
    gaps: list[IRUnsupported] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return self.contents.count("\n")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sourcePath": self.source_path,
            "lineCount": self.line_count,
            "unsupportedCount": self.unsupported_count,
            "diagnostics": self.diagnostics.to_list(),
        }
