"""Deterministic source writer with mapping metadata.

The writer is the only thing in a backend that touches text layout, which keeps
formatting decisions in one place and makes generated output byte-identical
between runs.  While writing, it records which output line came from which
source span, so a ``tsc`` error at line 42 can be reported against the original
Python line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import SourceSpan

__all__ = ["CodeWriter", "MappingEntry"]


@dataclass(frozen=True, slots=True)
class MappingEntry:
    """One output line and the source span it was generated from."""

    output_line: int
    source_file: str
    source_line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputLine": self.output_line,
            "sourceFile": self.source_file,
            "sourceLine": self.source_line,
        }


@dataclass
class CodeWriter:
    """Accumulates indented lines of generated source."""

    indent_text: str = "  "
    lines: list[str] = field(default_factory=list)
    mappings: list[MappingEntry] = field(default_factory=list)
    _level: int = 0

    @property
    def level(self) -> int:
        return self._level

    @property
    def line_count(self) -> int:
        return len(self.lines)

    def indent(self) -> None:
        self._level += 1

    def dedent(self) -> None:
        if self._level == 0:
            raise ValueError("cannot dedent below the left margin")
        self._level -= 1

    def line(self, text: str = "", span: SourceSpan | None = None) -> None:
        """Write one line at the current indentation.

        An empty *text* writes a truly empty line rather than a line of
        whitespace, so trailing-whitespace linters stay quiet on output.
        """
        self.lines.append(f"{self.indent_text * self._level}{text}" if text else "")
        if span is not None and span.start_line:
            self.mappings.append(MappingEntry(len(self.lines), span.file, span.start_line))

    def lines_of(self, text: str, span: SourceSpan | None = None) -> None:
        """Write a multi-line string, indenting each line."""
        for index, piece in enumerate(text.split("\n")):
            self.line(piece, span if index == 0 else None)

    def blank(self) -> None:
        """Write a blank line, collapsing consecutive blanks."""
        if self.lines and self.lines[-1] == "":
            return
        self.lines.append("")

    def block(self, header: str, span: SourceSpan | None = None) -> _Block:
        """Open a braced block: ``header {`` … ``}``."""
        self.line(f"{header} {{", span)
        return _Block(self)

    def render(self) -> str:
        """Return the accumulated source with exactly one trailing newline."""
        text = "\n".join(self.lines).rstrip("\n")
        return f"{text}\n" if text else ""

    def mapping_dict(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.mappings]


@dataclass
class _Block:
    """Context manager that indents until the closing brace is written."""

    writer: CodeWriter

    def __enter__(self) -> CodeWriter:
        self.writer.indent()
        return self.writer

    def __exit__(self, *exc: object) -> None:
        self.writer.dedent()
        self.writer.line("}")
