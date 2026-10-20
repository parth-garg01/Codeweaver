"""The result model for validation checks.

A check has four possible outcomes and they are kept distinct on purpose:

* ``passed`` — the command ran and reported success;
* ``failed`` — the command ran and reported failure;
* ``skipped`` — the check did not run, and ``reason`` says why;
* ``errored`` — the check could not run to completion (timeout, missing file).

Collapsing ``skipped`` into ``passed`` is the single easiest way to make a
migration look better than it is, so nothing in this module provides a way to
do it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import Diagnostic

__all__ = ["CheckOutcome", "CheckResult", "ValidationReport"]


class CheckOutcome(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"


@dataclass
class CheckResult:
    """One validation check that ran, or explicitly did not."""

    name: str
    outcome: CheckOutcome
    #: The exact argv that was run, for reproduction.
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    duration_seconds: float = 0.0
    #: Why the check was skipped or errored.
    reason: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)
    #: Truncated command output, kept for the report.
    output: str = ""

    @property
    def ok(self) -> bool:
        """``True`` only when the check actually ran and passed."""
        return self.outcome is CheckOutcome.PASSED

    @property
    def command_line(self) -> str:
        return " ".join(self.command)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outcome": self.outcome.value,
            "command": list(self.command),
            "exitCode": self.exit_code,
            "durationSeconds": round(self.duration_seconds, 3),
            "reason": self.reason,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "output": self.output,
        }

    @classmethod
    def skipped(cls, name: str, reason: str) -> CheckResult:
        return cls(name=name, outcome=CheckOutcome.SKIPPED, reason=reason)


@dataclass
class ValidationReport:
    """Every check run against one generated project."""

    output_dir: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    tool_versions: dict[str, str] = field(default_factory=dict)

    def add(self, check: CheckResult) -> CheckResult:
        self.checks.append(check)
        return check

    def of(self, outcome: CheckOutcome) -> list[CheckResult]:
        return [check for check in self.checks if check.outcome is outcome]

    @property
    def passed(self) -> list[CheckResult]:
        return self.of(CheckOutcome.PASSED)

    @property
    def failed(self) -> list[CheckResult]:
        return self.of(CheckOutcome.FAILED)

    @property
    def skipped_checks(self) -> list[CheckResult]:
        return self.of(CheckOutcome.SKIPPED)

    @property
    def errored(self) -> list[CheckResult]:
        return self.of(CheckOutcome.ERRORED)

    @property
    def all_diagnostics(self) -> list[Diagnostic]:
        return [item for check in self.checks for item in check.diagnostics]

    @property
    def has_failures(self) -> bool:
        return bool(self.failed or self.errored)

    @property
    def ran_any_check(self) -> bool:
        return any(
            check.outcome in (CheckOutcome.PASSED, CheckOutcome.FAILED) for check in self.checks
        )

    def summary(self) -> dict[str, int]:
        return {
            "passed": len(self.passed),
            "failed": len(self.failed),
            "skipped": len(self.skipped_checks),
            "errored": len(self.errored),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "outputDir": self.output_dir,
            "checks": [check.to_dict() for check in self.checks],
            "toolVersions": dict(sorted(self.tool_versions.items())),
            "summary": self.summary(),
            "ranAnyCheck": self.ran_any_check,
        }
