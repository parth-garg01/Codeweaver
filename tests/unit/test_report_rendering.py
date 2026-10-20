from __future__ import annotations

import json
import unittest

from codeweaver.diagnostics import Diagnostic, Severity, SourceSpan
from codeweaver.reporting import (
    FileCounts,
    MigrationReport,
    UnsupportedEntry,
    render_json,
    render_markdown,
    render_text,
)
from codeweaver.validation.results import CheckOutcome, CheckResult, ValidationReport


def full_report() -> MigrationReport:
    validation = ValidationReport(output_dir="/out")
    validation.add(
        CheckResult(
            "typescript typecheck",
            CheckOutcome.FAILED,
            ["tsc", "--noEmit"],
            exit_code=2,
            diagnostics=[
                Diagnostic(
                    "TS2322",
                    "type mismatch",
                    Severity.ERROR,
                    SourceSpan("pkg/a.ts", 12, 4),
                    hint="generated from pkg/a.py:7",
                )
            ],
        )
    )
    validation.add(CheckResult.skipped("migrated tests", "execution not enabled"))
    return MigrationReport(
        source_language="python",
        target_language="typescript",
        support_level="partial",
        source_root="/src",
        output_dir="/out",
        ir_schema_version="1.1.0",
        tool_versions={"tsc": "Version 5.8.2"},
        counts=FileCounts(discovered=5, parsed=4, translated=4, skipped=1),
        generated_files=["pkg/a.ts", "tsconfig.json"],
        unsupported=[
            UnsupportedEntry("With", "context managers", 2, ("a.py:3", "a.py:9"), "use try/finally")
        ],
        unresolved_imports=["requests"],
        dependency_cycles=[["pkg.a", "pkg.b"]],
        cycle_handling="generated together in one group",
        applied_passes=["infer-instance-fields"],
        skipped_passes=["lower-range-loops"],
        diagnostics=[
            Diagnostic(
                "CW0600", "unsupported construct", Severity.WARNING, SourceSpan("a.py", 3, 0)
            )
        ],
        validation=validation,
    )


class JsonRenderingTests(unittest.TestCase):
    def test_output_is_valid_json(self) -> None:
        payload = json.loads(render_json(full_report()))
        self.assertEqual(payload["status"], "failed")

    def test_output_is_deterministic(self) -> None:
        self.assertEqual(render_json(full_report()), render_json(full_report()))

    def test_keys_are_sorted(self) -> None:
        text = render_json(full_report())
        self.assertLess(text.index('"counts"'), text.index('"status"'))

    def test_compact_rendering_is_possible(self) -> None:
        self.assertNotIn("\n", render_json(full_report(), indent=None))


class TextRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = render_text(full_report())

    def test_status_and_reason_are_at_the_top(self) -> None:
        first = self.text.splitlines()[0]
        self.assertIn("FAILED", first)
        self.assertIn("typescript typecheck", self.text.splitlines()[1])

    def test_languages_and_support_level_are_shown(self) -> None:
        self.assertIn("python -> typescript", self.text)
        self.assertIn("support: partial", self.text)

    def test_file_counts_are_shown(self) -> None:
        self.assertIn("discovered 5", self.text)
        self.assertIn("failed 0", self.text)

    def test_unsupported_constructs_are_listed_with_locations(self) -> None:
        self.assertIn("With x2: context managers", self.text)
        self.assertIn("at a.py:3", self.text)
        self.assertIn("hint: use try/finally", self.text)

    def test_validation_commands_and_exit_codes_are_shown(self) -> None:
        self.assertIn("[FAIL] typescript typecheck (exit 2)", self.text)
        self.assertIn("$ tsc --noEmit", self.text)

    def test_skipped_checks_show_their_reason(self) -> None:
        self.assertIn("[skip] migrated tests", self.text)
        self.assertIn("execution not enabled", self.text)

    def test_cycles_are_shown_with_their_handling(self) -> None:
        self.assertIn("pkg.a -> pkg.b -> pkg.a", self.text)
        self.assertIn("generated together in one group", self.text)

    def test_unresolved_imports_are_shown(self) -> None:
        self.assertIn("import requests", self.text)

    def test_transform_passes_are_shown(self) -> None:
        self.assertIn("applied infer-instance-fields", self.text)
        self.assertIn("skipped lower-range-loops", self.text)

    def test_equivalence_statement_is_last(self) -> None:
        self.assertIn("does not verify behavioural equivalence", self.text.splitlines()[-1])

    def test_colour_is_off_by_default_and_opt_in(self) -> None:
        self.assertNotIn("\033[", self.text)
        self.assertIn("\033[31m", render_text(full_report(), use_colour=True))

    def test_empty_report_still_renders(self) -> None:
        text = render_text(MigrationReport())
        self.assertIn("no checks were configured", text)
        self.assertIn("does not verify behavioural equivalence", text)

    def test_long_diagnostic_lists_are_truncated(self) -> None:
        report = MigrationReport(
            diagnostics=[
                Diagnostic(f"CW{index:04d}", "warning", Severity.WARNING) for index in range(30)
            ]
        )
        self.assertIn("and 10 more", render_text(report))


class MarkdownRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = render_markdown(full_report())

    def test_has_a_title_and_status(self) -> None:
        self.assertTrue(self.text.startswith("# CodeWeaver migration report"))
        self.assertIn("**Status:** failed", self.text)

    def test_metadata_table(self) -> None:
        self.assertIn("| Source language | python |", self.text)
        self.assertIn("| tsc | Version 5.8.2 |", self.text)

    def test_file_count_table(self) -> None:
        self.assertIn("| 5 | 4 | 4 | 1 | 0 |", self.text)

    def test_unsupported_table(self) -> None:
        self.assertIn("| `With` | 2 | context managers |", self.text)

    def test_validation_table_includes_the_command(self) -> None:
        self.assertIn("`tsc --noEmit`", self.text)
        self.assertIn("| migrated tests | skipped |", self.text)

    def test_cycle_section(self) -> None:
        self.assertIn("- `pkg.a -> pkg.b -> pkg.a`", self.text)

    def test_caveat_section_is_present(self) -> None:
        self.assertIn("## Caveat", self.text)
        self.assertIn("does not verify behavioural equivalence", self.text)

    def test_clean_report_says_nothing_is_unsupported(self) -> None:
        text = render_markdown(MigrationReport())
        self.assertIn("None. Every construct encountered has a mapping.", text)
        self.assertIn("No checks were configured.", text)

    def test_output_ends_with_one_newline(self) -> None:
        self.assertTrue(self.text.endswith("\n"))
        self.assertFalse(self.text.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
