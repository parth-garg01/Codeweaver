from __future__ import annotations

import unittest

from codeweaver.diagnostics import Diagnostic, Severity, SourceSpan
from codeweaver.reporting.report import (
    FileCounts,
    MigrationReport,
    MigrationStatus,
    UnsupportedEntry,
)
from codeweaver.validation.results import CheckOutcome, CheckResult, ValidationReport


def passing_validation() -> ValidationReport:
    report = ValidationReport(output_dir="out")
    report.add(CheckResult("typecheck", CheckOutcome.PASSED, exit_code=0))
    return report


def failing_validation() -> ValidationReport:
    report = ValidationReport(output_dir="out")
    report.add(
        CheckResult(
            "typecheck",
            CheckOutcome.FAILED,
            exit_code=2,
            diagnostics=[Diagnostic("TS2322", "bad", Severity.ERROR)],
        )
    )
    return report


def skipped_validation() -> ValidationReport:
    report = ValidationReport(output_dir="out")
    report.add(CheckResult.skipped("typecheck", "tsc not installed"))
    return report


class StatusTests(unittest.TestCase):
    def test_everything_clean_is_passed(self) -> None:
        report = MigrationReport(validation=passing_validation())
        self.assertEqual(report.status, MigrationStatus.PASSED)
        self.assertEqual(report.status.exit_code, 0)

    def test_warnings_downgrade_to_passed_with_warnings(self) -> None:
        report = MigrationReport(
            validation=passing_validation(),
            diagnostics=[Diagnostic("CW1", "chained comparison", Severity.WARNING)],
        )
        self.assertEqual(report.status, MigrationStatus.PASSED_WITH_WARNINGS)
        self.assertEqual(report.status.exit_code, 0)

    def test_unsupported_constructs_make_it_incomplete(self) -> None:
        report = MigrationReport(
            validation=passing_validation(),
            unsupported=[UnsupportedEntry("With", "context managers", 2)],
        )
        self.assertEqual(report.status, MigrationStatus.INCOMPLETE)
        self.assertEqual(report.status.exit_code, 3)

    def test_skipping_every_check_cannot_produce_a_pass(self) -> None:
        report = MigrationReport(validation=skipped_validation())
        self.assertEqual(report.status, MigrationStatus.INCOMPLETE)
        self.assertIn("No validation check actually ran", report.status_reason)

    def test_a_failed_check_makes_it_failed(self) -> None:
        report = MigrationReport(validation=failing_validation())
        self.assertEqual(report.status, MigrationStatus.FAILED)
        self.assertEqual(report.status.exit_code, 1)
        self.assertIn("typecheck", report.status_reason)

    def test_an_errored_check_makes_it_failed(self) -> None:
        validation = ValidationReport()
        validation.add(CheckResult("typecheck", CheckOutcome.ERRORED, reason="timeout"))
        self.assertEqual(MigrationReport(validation=validation).status, MigrationStatus.FAILED)

    def test_migration_errors_make_it_failed(self) -> None:
        report = MigrationReport(
            validation=passing_validation(),
            diagnostics=[Diagnostic("CW0410", "syntax error", Severity.ERROR)],
        )
        self.assertEqual(report.status, MigrationStatus.FAILED)
        self.assertIn("1 error(s)", report.status_reason)

    def test_failed_files_make_it_failed(self) -> None:
        report = MigrationReport(
            validation=passing_validation(), counts=FileCounts(discovered=2, failed=1)
        )
        self.assertEqual(report.status, MigrationStatus.FAILED)
        self.assertIn("could not be translated", report.status_reason)

    def test_validation_diagnostics_without_failure_are_warnings(self) -> None:
        validation = ValidationReport()
        validation.add(
            CheckResult(
                "typecheck",
                CheckOutcome.PASSED,
                exit_code=0,
                diagnostics=[Diagnostic("TS6133", "unused", Severity.WARNING)],
            )
        )
        self.assertEqual(
            MigrationReport(validation=validation).status,
            MigrationStatus.PASSED_WITH_WARNINGS,
        )

    def test_failure_takes_precedence_over_unsupported(self) -> None:
        report = MigrationReport(
            validation=failing_validation(),
            unsupported=[UnsupportedEntry("With", "r", 1)],
        )
        self.assertEqual(report.status, MigrationStatus.FAILED)


class ContentTests(unittest.TestCase):
    def build(self) -> MigrationReport:
        return MigrationReport(
            source_language="python",
            target_language="typescript",
            support_level="partial",
            source_root="/src",
            output_dir="/out",
            ir_schema_version="1.1.0",
            tool_versions={"tsc": "Version 5.8.2", "node": "v22.0.0"},
            counts=FileCounts(discovered=5, parsed=4, translated=4, skipped=1),
            generated_files=["pkg/a.ts", "tsconfig.json"],
            unsupported=[
                UnsupportedEntry("With", "context managers", 2, ("a.py:3", "a.py:9"), "use try")
            ],
            unresolved_imports=["os", "requests"],
            dependency_cycles=[["a", "b"]],
            cycle_handling="generated together in one group",
            applied_passes=["infer-instance-fields"],
            skipped_passes=["lower-range-loops"],
            diagnostics=[
                Diagnostic("CW0600", "unsupported", Severity.WARNING, SourceSpan("a.py", 3, 0))
            ],
            validation=passing_validation(),
        )

    def test_equivalence_statement_is_always_present(self) -> None:
        self.assertIn("does not verify behavioural equivalence", self.build().equivalence_statement)

    def test_unsupported_total_sums_occurrences(self) -> None:
        self.assertEqual(self.build().unsupported_total, 2)

    def test_errors_and_warnings_are_partitioned(self) -> None:
        report = self.build()
        self.assertEqual(report.errors, [])
        self.assertEqual(len(report.warnings), 1)

    def test_to_dict_contains_every_required_section(self) -> None:
        payload = self.build().to_dict()
        for key in (
            "sourceLanguage",
            "targetLanguage",
            "toolVersions",
            "counts",
            "generatedFiles",
            "unsupported",
            "unresolvedImports",
            "unresolvedSymbols",
            "dependencyCycles",
            "cycleHandling",
            "validation",
            "status",
            "statusReason",
            "exitCode",
            "equivalenceStatement",
        ):
            self.assertIn(key, payload, key)

    def test_to_dict_is_json_serialisable(self) -> None:
        import json

        json.dumps(self.build().to_dict())

    def test_tool_versions_are_sorted(self) -> None:
        self.assertEqual(list(self.build().to_dict()["toolVersions"]), ["node", "tsc"])

    def test_exit_code_matches_the_status(self) -> None:
        payload = self.build().to_dict()
        self.assertEqual(payload["exitCode"], MigrationStatus(payload["status"]).exit_code)


if __name__ == "__main__":
    unittest.main()
