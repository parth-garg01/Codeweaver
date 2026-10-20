"""Rendering a migration report for a terminal, a file or a UI.

All three renderers read the same :class:`MigrationReport`, so a report shown
in the terminal and one exported to Markdown cannot disagree.  Each renderer
prints the status reason and the equivalence statement: a report that only
showed "passed" would be the easiest place in the product to mislead someone.
"""

from __future__ import annotations

import json

from codeweaver.diagnostics import Diagnostic
from codeweaver.reporting.report import MigrationReport
from codeweaver.validation.results import CheckOutcome

__all__ = ["render_json", "render_markdown", "render_text"]

#: Diagnostics listed inline before the renderer summarises the remainder.
_MAX_LISTED = 20


def render_json(report: MigrationReport, *, indent: int | None = 2) -> str:
    """Render the report as JSON, deterministically."""
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)


def render_text(report: MigrationReport, *, use_colour: bool = False) -> str:
    """Render the report as plain text for a terminal."""
    lines: list[str] = []
    status = report.status.value.upper()
    lines.append(f"CodeWeaver migration report: {_colour(status, report, use_colour)}")
    lines.append(report.status_reason)
    lines.append("")
    lines.append(
        f"  {report.source_language or '?'} -> {report.target_language or '?'}"
        + (f"  (support: {report.support_level})" if report.support_level else "")
    )
    if report.source_root:
        lines.append(f"  source: {report.source_root}")
    if report.output_dir:
        lines.append(f"  output: {report.output_dir}")
    if report.ir_schema_version:
        lines.append(f"  IR schema: {report.ir_schema_version}")
    for name, version in sorted(report.tool_versions.items()):
        lines.append(f"  {name}: {version}")
    lines.append("")

    counts = report.counts
    lines.append("Files")
    lines.append(
        f"  discovered {counts.discovered}   parsed {counts.parsed}   "
        f"translated {counts.translated}   skipped {counts.skipped}   failed {counts.failed}"
    )
    lines.append("")

    if report.unsupported:
        lines.append(f"Unsupported constructs ({report.unsupported_total} occurrence(s))")
        for entry in report.unsupported:
            lines.append(f"  {entry.source_kind} x{entry.count}: {entry.reason}")
            for location in entry.locations[:5]:
                lines.append(f"    at {location}")
            if len(entry.locations) > 5:
                lines.append(f"    ... and {len(entry.locations) - 5} more")
            if entry.hint:
                lines.append(f"    hint: {entry.hint}")
        lines.append("")

    if report.unresolved_imports or report.unresolved_symbols:
        lines.append("Unresolved")
        for name in report.unresolved_imports:
            lines.append(f"  import {name}")
        for name in report.unresolved_symbols:
            lines.append(f"  symbol {name}")
        lines.append("")

    if report.dependency_cycles:
        lines.append(f"Dependency cycles ({len(report.dependency_cycles)})")
        for cycle in report.dependency_cycles:
            lines.append(f"  {' -> '.join(cycle)} -> {cycle[0]}")
        if report.cycle_handling:
            lines.append(f"  handling: {report.cycle_handling}")
        lines.append("")

    lines.append("Validation")
    if not report.validation.checks:
        lines.append("  no checks were configured")
    for check in report.validation.checks:
        marker = {
            CheckOutcome.PASSED: "pass",
            CheckOutcome.FAILED: "FAIL",
            CheckOutcome.SKIPPED: "skip",
            CheckOutcome.ERRORED: "ERROR",
        }[check.outcome]
        detail = f" (exit {check.exit_code})" if check.exit_code is not None else ""
        lines.append(f"  [{marker}] {check.name}{detail}")
        if check.command:
            lines.append(f"         $ {check.command_line}")
        if check.reason:
            lines.append(f"         {check.reason}")
        for diagnostic in check.diagnostics[:_MAX_LISTED]:
            lines.append(f"         {_one_line(diagnostic)}")
        if len(check.diagnostics) > _MAX_LISTED:
            lines.append(
                f"         ... and {len(check.diagnostics) - _MAX_LISTED} more diagnostics"
            )
    lines.append("")

    if report.applied_passes or report.skipped_passes:
        lines.append("Transform passes")
        for name in report.applied_passes:
            lines.append(f"  applied {name}")
        for name in report.skipped_passes:
            lines.append(f"  skipped {name}")
        lines.append("")

    if report.diagnostics:
        lines.append(
            f"Diagnostics ({len(report.errors)} error(s), {len(report.warnings)} warning(s))"
        )
        for diagnostic in report.diagnostics[:_MAX_LISTED]:
            lines.append(f"  {_one_line(diagnostic)}")
        if len(report.diagnostics) > _MAX_LISTED:
            lines.append(f"  ... and {len(report.diagnostics) - _MAX_LISTED} more")
        lines.append("")

    if report.generated_files:
        lines.append(f"Generated {len(report.generated_files)} file(s)")
        for path in report.generated_files[:_MAX_LISTED]:
            lines.append(f"  {path}")
        if len(report.generated_files) > _MAX_LISTED:
            lines.append(f"  ... and {len(report.generated_files) - _MAX_LISTED} more")
        lines.append("")

    lines.append(report.equivalence_statement)
    return "\n".join(lines).rstrip() + "\n"


def render_markdown(report: MigrationReport) -> str:
    """Render the report as Markdown for export or a pull request."""
    lines: list[str] = ["# CodeWeaver migration report", ""]
    lines.append(f"**Status:** {report.status.value}")
    lines.append("")
    lines.append(report.status_reason)
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| ----- | ----- |")
    lines.append(f"| Source language | {report.source_language or '-'} |")
    lines.append(f"| Target language | {report.target_language or '-'} |")
    lines.append(f"| Support level | {report.support_level or '-'} |")
    lines.append(f"| Source root | `{report.source_root or '-'}` |")
    lines.append(f"| Output directory | `{report.output_dir or '-'}` |")
    lines.append(f"| IR schema | {report.ir_schema_version or '-'} |")
    for name, version in sorted(report.tool_versions.items()):
        lines.append(f"| {name} | {version} |")
    lines.append("")

    counts = report.counts
    lines.append("## Files")
    lines.append("")
    lines.append("| Discovered | Parsed | Translated | Skipped | Failed |")
    lines.append("| ---------- | ------ | ---------- | ------- | ------ |")
    lines.append(
        f"| {counts.discovered} | {counts.parsed} | {counts.translated} "
        f"| {counts.skipped} | {counts.failed} |"
    )
    lines.append("")

    lines.append("## Unsupported constructs")
    lines.append("")
    if not report.unsupported:
        lines.append("None. Every construct encountered has a mapping.")
    else:
        lines.append("| Construct | Occurrences | Reason | Locations |")
        lines.append("| --------- | ----------- | ------ | --------- |")
        for entry in report.unsupported:
            locations = ", ".join(f"`{item}`" for item in entry.locations[:5]) or "-"
            lines.append(
                f"| `{entry.source_kind}` | {entry.count} | {entry.reason} | {locations} |"
            )
    lines.append("")

    lines.append("## Validation")
    lines.append("")
    if not report.validation.checks:
        lines.append("No checks were configured.")
    else:
        lines.append("| Check | Outcome | Exit | Command | Notes |")
        lines.append("| ----- | ------- | ---- | ------- | ----- |")
        for check in report.validation.checks:
            exit_code = "-" if check.exit_code is None else str(check.exit_code)
            command = f"`{check.command_line}`" if check.command else "-"
            lines.append(
                f"| {check.name} | {check.outcome.value} | {exit_code} | {command} "
                f"| {check.reason or '-'} |"
            )
    lines.append("")

    if report.dependency_cycles:
        lines.append("## Dependency cycles")
        lines.append("")
        for cycle in report.dependency_cycles:
            lines.append(f"- `{' -> '.join(cycle)} -> {cycle[0]}`")
        if report.cycle_handling:
            lines.append("")
            lines.append(f"Handling: {report.cycle_handling}")
        lines.append("")

    if report.unresolved_imports:
        lines.append("## Unresolved imports")
        lines.append("")
        for name in report.unresolved_imports:
            lines.append(f"- `{name}`")
        lines.append("")

    if report.diagnostics:
        lines.append("## Diagnostics")
        lines.append("")
        for diagnostic in report.diagnostics[:_MAX_LISTED]:
            lines.append(f"- {_one_line(diagnostic)}")
        if len(report.diagnostics) > _MAX_LISTED:
            lines.append(f"- ... and {len(report.diagnostics) - _MAX_LISTED} more")
        lines.append("")

    lines.append("## Caveat")
    lines.append("")
    lines.append(report.equivalence_statement)
    return "\n".join(lines).rstrip() + "\n"


def _one_line(diagnostic: Diagnostic) -> str:
    location = f"{diagnostic.span} " if diagnostic.span else ""
    hint = f" (hint: {diagnostic.hint})" if diagnostic.hint else ""
    return f"{location}{diagnostic.severity.value}[{diagnostic.code}]: {diagnostic.message}{hint}"


def _colour(text: str, report: MigrationReport, use_colour: bool) -> str:
    if not use_colour:
        return text
    code = {
        "passed": "32",
        "passed with warnings": "33",
        "incomplete": "33",
        "failed": "31",
    }[report.status.value]
    return f"\033[{code}m{text}\033[0m"
