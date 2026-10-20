"""Migration reports and their renderers."""

from __future__ import annotations

from codeweaver.reporting.render import render_json, render_markdown, render_text
from codeweaver.reporting.report import (
    EQUIVALENCE_STATEMENT,
    FileCounts,
    MigrationReport,
    MigrationStatus,
    UnsupportedEntry,
)

__all__ = [
    "EQUIVALENCE_STATEMENT",
    "FileCounts",
    "MigrationReport",
    "MigrationStatus",
    "UnsupportedEntry",
    "render_json",
    "render_markdown",
    "render_text",
]
