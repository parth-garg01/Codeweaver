"""CLI behaviour, including the exit codes other tools depend on."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from codeweaver.cli import build_parser, main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLEAN = FIXTURES / "sample_project"
UNSUPPORTED = FIXTURES / "unsupported_project"
HAS_TSC = shutil.which("tsc") is not None


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ParserTests(unittest.TestCase):
    def test_every_documented_command_exists(self) -> None:
        parser = build_parser()
        actions = [action for action in parser._actions if getattr(action, "choices", None)]
        commands = set(actions[-1].choices)
        self.assertTrue(
            {"scan", "analyze", "plan", "migrate", "validate", "report", "languages"} <= commands
        )

    def test_help_mentions_the_exit_codes(self) -> None:
        self.assertIn("Exit codes", build_parser().format_help())

    def test_no_command_prints_help_and_reports_a_usage_error(self) -> None:
        code, out, _ = run()
        self.assertEqual(code, 2)
        self.assertIn("usage:", out)

    def test_migrate_requires_an_output_directory(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run("migrate", str(CLEAN))
        self.assertEqual(ctx.exception.code, 2)


class LanguagesTests(unittest.TestCase):
    def test_lists_the_implemented_pair(self) -> None:
        code, out, _ = run("languages")
        self.assertEqual(code, 0)
        self.assertIn("python -> typescript", out)
        self.assertIn("available", out)

    def test_states_the_required_toolchain(self) -> None:
        _, out, _ = run("languages")
        self.assertIn("needs tsc", out)

    def test_lists_known_limitations(self) -> None:
        _, out, _ = run("languages")
        self.assertIn("context managers", out)

    def test_json_output_is_machine_readable(self) -> None:
        code, out, _ = run("languages", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["pairs"][0]["source"], "python")


class ScanTests(unittest.TestCase):
    def test_lists_files_and_skips(self) -> None:
        code, out, _ = run("scan", str(CLEAN))
        self.assertEqual(code, 0)
        self.assertIn("pkg/models.py", out)
        self.assertIn("Skipped:", out)

    def test_ignored_directory_is_not_listed_as_a_file(self) -> None:
        _, out, _ = run("scan", str(CLEAN))
        self.assertNotIn("scratch/notes.py", out)

    def test_json_output(self) -> None:
        _, out, _ = run("scan", str(CLEAN), "--json")
        payload = json.loads(out)
        self.assertEqual(payload["languageCounts"], {"python": 5})

    def test_a_missing_project_is_a_usage_error(self) -> None:
        code, _, err = run("scan", str(CLEAN / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("codeweaver:", err)


class AnalyzeTests(unittest.TestCase):
    def test_reports_counts_and_symbols(self) -> None:
        code, out, _ = run("analyze", str(CLEAN))
        self.assertEqual(code, 0)
        self.assertIn("parsed 5", out)
        self.assertIn("Symbols:", out)

    def test_reports_the_manifest(self) -> None:
        _, out, _ = run("analyze", str(CLEAN))
        self.assertIn("sample-project", out)

    def test_json_output(self) -> None:
        _, out, _ = run("analyze", str(CLEAN), "--json")
        payload = json.loads(out)
        self.assertEqual(payload["counts"]["parsed"], 5)


class PlanTests(unittest.TestCase):
    def test_shows_groups_in_order_with_risks(self) -> None:
        code, out, _ = run("plan", str(CLEAN), "--to", "typescript")
        self.assertEqual(code, 0)
        self.assertIn("Groups:", out)
        self.assertIn("risk", out)

    def test_marks_the_cycle(self) -> None:
        _, out, _ = run("plan", str(CLEAN))
        self.assertIn("[cycle]", out)

    def test_asks_for_review_before_migrating(self) -> None:
        _, out, _ = run("plan", str(CLEAN))
        self.assertIn("Review this plan", out)

    def test_lists_unsupported_constructs(self) -> None:
        _, out, _ = run("plan", str(UNSUPPORTED))
        self.assertIn("Unsupported constructs:", out)
        self.assertIn("With", out)

    def test_an_unavailable_pair_is_a_usage_error(self) -> None:
        code, _, err = run("plan", str(CLEAN), "--to", "rust")
        self.assertEqual(code, 2)
        self.assertIn("no declared migration path", err)

    def test_json_output(self) -> None:
        _, out, _ = run("plan", str(CLEAN), "--json")
        payload = json.loads(out)
        self.assertEqual(payload["targetLanguage"], "typescript")


class MigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.out = self.base / "out"
        self.store = self.base / "store"
        self._previous = None

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def migrate(self, project: Path, *extra: str) -> tuple[int, str, str]:
        import os

        os.environ["CODEWEAVER_STORE_DIR"] = str(self.store)
        try:
            return run("migrate", str(project), "--out", str(self.out), *extra)
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]

    def test_unsupported_project_exits_three_not_zero(self) -> None:
        code, out, _ = self.migrate(UNSUPPORTED, "--no-validate")
        self.assertEqual(code, 3)
        self.assertIn("INCOMPLETE", out)

    def test_report_always_states_the_equivalence_caveat(self) -> None:
        _, out, _ = self.migrate(UNSUPPORTED, "--no-validate")
        self.assertIn("does not verify behavioural equivalence", out)

    def test_generated_files_are_written(self) -> None:
        self.migrate(CLEAN, "--no-validate")
        self.assertTrue((self.out / "pkg" / "models.ts").is_file())
        self.assertTrue((self.out / "tsconfig.json").is_file())

    def test_writing_into_the_project_is_refused(self) -> None:
        import os

        os.environ["CODEWEAVER_STORE_DIR"] = str(self.store)
        try:
            code, _, err = run(
                "migrate", str(CLEAN), "--out", str(CLEAN / "generated"), "--no-validate"
            )
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]
        self.assertEqual(code, 2)
        self.assertIn("inside the source project", err)

    def test_a_non_empty_output_needs_overwrite(self) -> None:
        self.migrate(CLEAN, "--no-validate")
        code, _, err = self.migrate(CLEAN, "--no-validate")
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)
        code, _, _ = self.migrate(CLEAN, "--no-validate", "--overwrite")
        self.assertEqual(code, 3)

    def test_without_validation_the_result_is_incomplete(self) -> None:
        code, out, _ = self.migrate(CLEAN, "--no-validate")
        self.assertEqual(code, 3)
        self.assertIn("No validation check actually ran", out)

    @unittest.skipUnless(HAS_TSC, "tsc is not installed")
    def test_a_clean_project_with_validation_exits_zero(self) -> None:
        code, out, _ = self.migrate(CLEAN)
        self.assertEqual(code, 0, msg=out)

    def test_markdown_and_json_formats(self) -> None:
        _, out, _ = self.migrate(CLEAN, "--no-validate", "--format", "markdown")
        self.assertIn("# CodeWeaver migration report", out)
        shutil.rmtree(self.out)
        _, out, _ = self.migrate(CLEAN, "--no-validate", "--format", "json")
        self.assertEqual(json.loads(out)["targetLanguage"], "typescript")

    def test_report_command_reads_the_stored_report(self) -> None:
        import os

        self.migrate(CLEAN, "--no-validate")
        os.environ["CODEWEAVER_STORE_DIR"] = str(self.store)
        try:
            code, out, _ = run("report", str(CLEAN))
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]
        self.assertEqual(code, 3)
        self.assertIn("Stored report:", out)

    def test_report_can_be_exported_to_a_file(self) -> None:
        import os

        self.migrate(CLEAN, "--no-validate")
        target = self.base / "report.md"
        os.environ["CODEWEAVER_STORE_DIR"] = str(self.store)
        try:
            code, out, _ = run("report", str(CLEAN), "--format", "markdown", "--out", str(target))
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]
        self.assertEqual(code, 3)
        self.assertIn("Report written to", out)
        self.assertIn("Stored CodeWeaver report", target.read_text(encoding="utf-8"))

    def test_report_without_a_stored_run_is_a_usage_error(self) -> None:
        import os

        os.environ["CODEWEAVER_STORE_DIR"] = str(self.store)
        try:
            code, _, err = run("report", str(CLEAN))
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]
        self.assertEqual(code, 2)
        self.assertIn("no stored report", err)

    def test_clear_removes_stored_metadata(self) -> None:
        import os

        self.migrate(CLEAN, "--no-validate")
        os.environ["CODEWEAVER_STORE_DIR"] = str(self.store)
        try:
            code, out, _ = run("clear", str(CLEAN))
            self.assertEqual(code, 0)
            self.assertIn("Removed", out)
            code, out, _ = run("clear", str(CLEAN))
            self.assertIn("Nothing stored", out)
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_validating_a_missing_directory_fails(self) -> None:
        code, out, _ = run("validate", str(self.out))
        self.assertEqual(code, 1)
        self.assertIn("errored", out)

    def test_an_unsupported_target_reports_no_checks_ran(self) -> None:
        self.out.mkdir(parents=True)
        code, out, _ = run("validate", str(self.out), "--to", "rust")
        self.assertEqual(code, 3)
        self.assertIn("no check actually ran", out)

    def test_tests_and_install_are_off_unless_requested(self) -> None:
        self.out.mkdir(parents=True)
        (self.out / "tsconfig.json").write_text("{}", encoding="utf-8")
        _, out, _ = run("validate", str(self.out))
        self.assertIn("executes generated code", out)
        self.assertIn("needs the network", out)


if __name__ == "__main__":
    unittest.main()
