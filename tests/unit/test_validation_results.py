from __future__ import annotations

import unittest

from codeweaver.diagnostics import Diagnostic, Severity
from codeweaver.validation.results import CheckOutcome, CheckResult, ValidationReport


def passed(name: str = "typecheck") -> CheckResult:
    return CheckResult(name, CheckOutcome.PASSED, ["tsc", "--noEmit"], exit_code=0)


def failed(name: str = "typecheck") -> CheckResult:
    return CheckResult(
        name,
        CheckOutcome.FAILED,
        ["tsc", "--noEmit"],
        exit_code=2,
        diagnostics=[Diagnostic("TS2322", "type mismatch", Severity.ERROR)],
    )


class CheckResultTests(unittest.TestCase):
    def test_only_a_passed_check_is_ok(self) -> None:
        self.assertTrue(passed().ok)
        self.assertFalse(failed().ok)
        self.assertFalse(CheckResult.skipped("x", "no tsc").ok)
        self.assertFalse(CheckResult("x", CheckOutcome.ERRORED).ok)

    def test_skipped_records_its_reason(self) -> None:
        check = CheckResult.skipped("typecheck", "tsc not installed")
        self.assertEqual(check.outcome, CheckOutcome.SKIPPED)
        self.assertEqual(check.reason, "tsc not installed")
        self.assertIsNone(check.exit_code)

    def test_command_line_is_reproducible(self) -> None:
        self.assertEqual(passed().command_line, "tsc --noEmit")

    def test_to_dict_is_json_friendly(self) -> None:
        import json

        json.dumps(failed().to_dict())

    def test_to_dict_rounds_the_duration(self) -> None:
        check = CheckResult("x", CheckOutcome.PASSED, duration_seconds=1.23456)
        self.assertEqual(check.to_dict()["durationSeconds"], 1.235)


class ValidationReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = ValidationReport(output_dir="out")

    def test_empty_report_ran_nothing_and_has_no_failures(self) -> None:
        self.assertFalse(self.report.ran_any_check)
        self.assertFalse(self.report.has_failures)

    def test_a_report_of_only_skipped_checks_has_not_run_anything(self) -> None:
        self.report.add(CheckResult.skipped("typecheck", "tsc not installed"))
        self.assertFalse(self.report.ran_any_check)
        self.assertFalse(self.report.has_failures)

    def test_partitions_by_outcome(self) -> None:
        self.report.add(passed("a"))
        self.report.add(failed("b"))
        self.report.add(CheckResult.skipped("c", "r"))
        self.report.add(CheckResult("d", CheckOutcome.ERRORED, reason="timeout"))
        self.assertEqual([c.name for c in self.report.passed], ["a"])
        self.assertEqual([c.name for c in self.report.failed], ["b"])
        self.assertEqual([c.name for c in self.report.skipped_checks], ["c"])
        self.assertEqual([c.name for c in self.report.errored], ["d"])

    def test_errored_counts_as_a_failure(self) -> None:
        self.report.add(CheckResult("d", CheckOutcome.ERRORED, reason="timeout"))
        self.assertTrue(self.report.has_failures)

    def test_summary_counts_every_outcome(self) -> None:
        self.report.add(passed("a"))
        self.report.add(failed("b"))
        self.report.add(CheckResult.skipped("c", "r"))
        self.assertEqual(
            self.report.summary(), {"passed": 1, "failed": 1, "skipped": 1, "errored": 0}
        )

    def test_diagnostics_are_collected_across_checks(self) -> None:
        self.report.add(failed("a"))
        self.report.add(failed("b"))
        self.assertEqual(len(self.report.all_diagnostics), 2)

    def test_tool_versions_are_sorted_in_the_payload(self) -> None:
        self.report.tool_versions = {"tsc": "5.8.2", "node": "v22.0.0"}
        self.assertEqual(list(self.report.to_dict()["toolVersions"]), ["node", "tsc"])

    def test_to_dict_is_json_friendly(self) -> None:
        import json

        self.report.add(passed())
        json.dumps(self.report.to_dict())


if __name__ == "__main__":
    unittest.main()
