"""The migration report.

The report is the product's main honest-reporting surface, so its status rule
is deliberately strict:

* ``failed`` — generation or a validation check failed;
* ``incomplete`` — unsupported constructs were left in the output, or no
  validation check actually ran;
* ``passed with warnings`` — everything ran and passed, but behaviour
  differences were reported;
* ``passed`` — everything ran, everything passed, nothing was flagged.

``passed`` therefore cannot be reached by skipping checks, and it still does not
claim behavioural equivalence: :attr:`MigrationReport.equivalence_statement`
says so in every report.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import Diagnostic, Severity
from codeweaver.validation.results import ValidationReport

__all__ = ["FileCounts", "MigrationReport", "MigrationStatus", "UnsupportedEntry"]

#: Printed in every report.  Compilation is not equivalence, and the tool must
#: not let a green run imply otherwise.
EQUIVALENCE_STATEMENT = (
    "CodeWeaver does not verify behavioural equivalence. Passing checks mean "
    "the generated project parses and type-checks, not that it behaves like "
    "the source."
)


class MigrationStatus(enum.StrEnum):
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed with warnings"
    INCOMPLETE = "incomplete"
    FAILED = "failed"

    @property
    def exit_code(self) -> int:
        """Process exit code for this status."""
        return {
            "passed": 0,
            "passed with warnings": 0,
            "incomplete": 3,
            "failed": 1,
        }[self.value]


@dataclass
class FileCounts:
    """How many files each stage handled."""

    discovered: int = 0
    parsed: int = 0
    translated: int = 0
    skipped: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "discovered": self.discovered,
            "parsed": self.parsed,
            "translated": self.translated,
            "skipped": self.skipped,
            "failed": self.failed,
        }


@dataclass(frozen=True, slots=True)
class UnsupportedEntry:
    """One unsupported construct, with where it was and how often."""

    source_kind: str
    reason: str
    count: int
    locations: tuple[str, ...] = ()
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceKind": self.source_kind,
            "reason": self.reason,
            "count": self.count,
            "locations": list(self.locations),
            "hint": self.hint,
        }


@dataclass
class MigrationReport:
    """Everything one migration run produced."""

    source_language: str = ""
    target_language: str = ""
    support_level: str = ""
    source_root: str = ""
    output_dir: str = ""
    started_at: str = ""
    duration_seconds: float = 0.0
    ir_schema_version: str = ""
    tool_versions: dict[str, str] = field(default_factory=dict)

    counts: FileCounts = field(default_factory=FileCounts)
    generated_files: list[str] = field(default_factory=list)
    supported_constructs: list[str] = field(default_factory=list)
    unsupported: list[UnsupportedEntry] = field(default_factory=list)
    unresolved_imports: list[str] = field(default_factory=list)
    unresolved_symbols: list[str] = field(default_factory=list)
    dependency_cycles: list[list[str]] = field(default_factory=list)
    cycle_handling: str = ""
    applied_passes: list[str] = field(default_factory=list)
    skipped_passes: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    validation: ValidationReport = field(default_factory=ValidationReport)

    @property
    def equivalence_statement(self) -> str:
        return EQUIVALENCE_STATEMENT

    @property
    def errors(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.severity is Severity.WARNING]

    @property
    def unsupported_total(self) -> int:
        return sum(entry.count for entry in self.unsupported)

    @property
    def status(self) -> MigrationStatus:
        if self.errors or self.counts.failed or self.validation.has_failures:
            return MigrationStatus.FAILED
        if self.unsupported_total or not self.validation.ran_any_check:
            return MigrationStatus.INCOMPLETE
        if self.warnings or self.validation.all_diagnostics:
            return MigrationStatus.PASSED_WITH_WARNINGS
        return MigrationStatus.PASSED

    @property
    def status_reason(self) -> str:
        """One sentence explaining the status, for the top of the report."""
        if self.errors:
            return f"{len(self.errors)} error(s) were reported during migration."
        if self.counts.failed:
            return f"{self.counts.failed} file(s) could not be translated."
        if self.validation.has_failures:
            failed = ", ".join(check.name for check in self.validation.failed)
            errored = ", ".join(check.name for check in self.validation.errored)
            detail = " and ".join(part for part in (failed, errored) if part)
            return f"Validation reported problems in: {detail}."
        if self.unsupported_total:
            return (
                f"{self.unsupported_total} unsupported construct(s) were left as "
                "markers in the generated output."
            )
        if not self.validation.ran_any_check:
            return "No validation check actually ran, so the generated output is unverified."
        if self.warnings or self.validation.all_diagnostics:
            return "Every check passed, but behaviour differences were reported."
        return "Every applicable check ran and passed."

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceLanguage": self.source_language,
            "targetLanguage": self.target_language,
            "supportLevel": self.support_level,
            "sourceRoot": self.source_root,
            "outputDir": self.output_dir,
            "startedAt": self.started_at,
            "durationSeconds": round(self.duration_seconds, 3),
            "irSchemaVersion": self.ir_schema_version,
            "toolVersions": dict(sorted(self.tool_versions.items())),
            "counts": self.counts.to_dict(),
            "generatedFiles": list(self.generated_files),
            "supportedConstructs": list(self.supported_constructs),
            "unsupported": [entry.to_dict() for entry in self.unsupported],
            "unresolvedImports": list(self.unresolved_imports),
            "unresolvedSymbols": list(self.unresolved_symbols),
            "dependencyCycles": [list(cycle) for cycle in self.dependency_cycles],
            "cycleHandling": self.cycle_handling,
            "appliedPasses": list(self.applied_passes),
            "skippedPasses": list(self.skipped_passes),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "validation": self.validation.to_dict(),
            "status": self.status.value,
            "statusReason": self.status_reason,
            "exitCode": self.status.exit_code,
            "equivalenceStatement": self.equivalence_statement,
        }
