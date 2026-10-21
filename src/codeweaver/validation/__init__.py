"""Validating generated output with locally available toolchains."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from codeweaver.validation.javascript import parse_node_output, run_syntax_check
from codeweaver.validation.policy import ExecutionPolicy
from codeweaver.validation.results import CheckOutcome, CheckResult, ValidationReport
from codeweaver.validation.toolchain import (
    Toolchain,
    ToolchainIndex,
    detect_toolchain,
    detect_toolchains,
)
from codeweaver.validation.typescript import (
    SourceMapIndex,
    parse_tsc_output,
    run_typecheck,
)

__all__ = [
    "CheckOutcome",
    "CheckResult",
    "ExecutionPolicy",
    "SourceMapIndex",
    "Toolchain",
    "ToolchainIndex",
    "ValidationReport",
    "detect_toolchain",
    "detect_toolchains",
    "parse_node_output",
    "parse_tsc_output",
    "run_syntax_check",
    "run_typecheck",
    "validate_project",
]

#: Tools CodeWeaver looks for when validating each target's output.
TYPESCRIPT_TOOLS: tuple[str, ...] = ("tsc", "node", "npm")
JAVASCRIPT_TOOLS: tuple[str, ...] = ("node", "npm")

#: target -> (toolchains to look for, the check to run).
#:
#: A target absent from this table is reported as having no validation support,
#: which is why a new backend cannot quietly produce unverified output that the
#: report calls a pass.
_VALIDATORS: dict[str, tuple[tuple[str, ...], Callable[..., CheckResult]]] = {
    "typescript": (TYPESCRIPT_TOOLS, run_typecheck),
    "javascript": (JAVASCRIPT_TOOLS, run_syntax_check),
}


def validate_project(
    output_dir: Path | str,
    *,
    target: str = "typescript",
    policy: ExecutionPolicy | None = None,
    mappings: dict[str, list[dict[str, Any]]] | None = None,
) -> ValidationReport:
    """Run every applicable check against the generated project.

    Checks that cannot run are recorded as skipped with the reason.  A report
    where nothing ran is therefore distinguishable from a report where
    everything passed — :attr:`ValidationReport.ran_any_check` is the
    difference, and the migration status depends on it.
    """
    policy = policy or ExecutionPolicy()
    root = Path(output_dir)
    report = ValidationReport(output_dir=str(root))

    if target not in _VALIDATORS:
        report.add(
            CheckResult.skipped(
                f"{target} validation", f"no validation support for target {target!r}"
            )
        )
        return report

    tools, runner = _VALIDATORS[target]
    toolchains = detect_toolchains(tools, project_root=root)
    report.tool_versions = toolchains.versions()
    source_map = SourceMapIndex.from_mappings(mappings or {})
    report.add(runner(root, policy=policy, toolchains=toolchains, source_map=source_map))

    # Tests and installs are separate classes of execution; both are reported
    # as skipped rather than silently omitted so the report stays complete.
    if not policy.permits("install"):
        report.add(CheckResult.skipped("dependency install", policy.refusal_reason("install")))
    if not policy.permits("tests"):
        report.add(CheckResult.skipped("migrated tests", policy.refusal_reason("tests")))
    else:
        report.add(
            CheckResult.skipped(
                "migrated tests",
                "no test harness has been generated for this language pair yet",
            )
        )
    return report
