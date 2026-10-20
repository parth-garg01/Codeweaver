"""The two outcomes a migration must get right, end to end.

A clean project must translate into output the target toolchain accepts. A
project built from unsupported constructs must be *reported*, not silently
approximated — and the report must say so loudly enough that nobody mistakes it
for a success.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from codeweaver.pipeline import MigrationOptions, run_migration
from codeweaver.reporting import render_markdown, render_text
from codeweaver.validation.results import CheckOutcome

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLEAN = FIXTURES / "sample_project"
UNSUPPORTED = FIXTURES / "unsupported_project"
HAS_TSC = shutil.which("tsc") is not None


class CleanProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        self.outcome = run_migration(CLEAN, self.out)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_nothing_is_unsupported(self) -> None:
        report = self.outcome.report
        self.assertEqual(
            report.unsupported_total,
            0,
            msg=f"unexpected gaps: {[e.source_kind for e in report.unsupported]}",
        )

    def test_every_module_is_translated(self) -> None:
        counts = self.outcome.report.counts
        self.assertEqual(counts.discovered, counts.parsed)
        self.assertEqual(counts.parsed, counts.translated)
        self.assertEqual(counts.failed, 0)

    def test_the_import_cycle_is_reported_and_handled(self) -> None:
        report = self.outcome.report
        self.assertEqual(len(report.dependency_cycles), 1)
        self.assertIn("generated together", report.cycle_handling)

    @unittest.skipUnless(HAS_TSC, "tsc is not installed")
    def test_generated_output_passes_the_target_type_checker(self) -> None:
        check = self.outcome.report.validation.checks[0]
        self.assertEqual(
            check.outcome,
            CheckOutcome.PASSED,
            msg=f"tsc reported:\n{check.output}",
        )

    @unittest.skipUnless(HAS_TSC, "tsc is not installed")
    def test_status_is_a_pass_but_never_claims_equivalence(self) -> None:
        report = self.outcome.report
        self.assertIn(report.status.value, ("passed", "passed with warnings"))
        self.assertEqual(report.status.exit_code, 0)
        self.assertIn("does not verify behavioural equivalence", render_text(report))

    def test_rerunning_into_the_same_directory_needs_overwrite(self) -> None:
        from codeweaver.pipeline import OutputDirectoryError

        with self.assertRaises(OutputDirectoryError):
            run_migration(CLEAN, self.out)

    def test_rerunning_with_overwrite_reproduces_the_same_files(self) -> None:
        before = {
            path.relative_to(self.out): path.read_text(encoding="utf-8")
            for path in self.out.rglob("*")
            if path.is_file()
        }
        run_migration(CLEAN, self.out, MigrationOptions(overwrite=True))
        after = {
            path.relative_to(self.out): path.read_text(encoding="utf-8")
            for path in self.out.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)


class UnsupportedProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        self.outcome = run_migration(UNSUPPORTED, self.out, MigrationOptions(run_validation=False))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_each_unsupported_construct_is_reported(self) -> None:
        kinds = {entry.source_kind for entry in self.outcome.report.unsupported}
        self.assertEqual(
            kinds,
            {"With", "GeneratorExp", "For", "Match", "FunctionDef"},
        )

    def test_every_report_entry_has_a_location_and_a_hint(self) -> None:
        for entry in self.outcome.report.unsupported:
            self.assertTrue(entry.locations, entry.source_kind)
            self.assertTrue(entry.hint, entry.source_kind)

    def test_gaps_are_marked_in_the_generated_source(self) -> None:
        contents = (self.out / "pkg" / "unsupported.ts").read_text(encoding="utf-8")
        self.assertEqual(
            contents.count("CODEWEAVER-UNSUPPORTED"), self.outcome.report.unsupported_total
        )

    def test_status_is_incomplete_with_a_non_zero_exit_code(self) -> None:
        report = self.outcome.report
        self.assertEqual(report.status.value, "incomplete")
        self.assertEqual(report.status.exit_code, 3)

    def test_the_external_dependency_is_reported(self) -> None:
        self.assertIn("os", self.outcome.report.unresolved_imports)

    def test_markdown_report_lists_the_gaps(self) -> None:
        markdown = render_markdown(self.outcome.report)
        self.assertIn("`With`", markdown)
        self.assertIn("## Unsupported constructs", markdown)

    def test_the_report_never_claims_a_pass(self) -> None:
        text = render_text(self.outcome.report)
        self.assertIn("INCOMPLETE", text)
        self.assertNotIn("PASSED", text)


if __name__ == "__main__":
    unittest.main()
