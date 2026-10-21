"""The Go target, end to end against the real Go toolchain."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from codeweaver.pipeline import MigrationOptions, run_migration
from codeweaver.validation.results import CheckOutcome

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLEAN = FIXTURES / "sample_project"
UNSUPPORTED = FIXTURES / "unsupported_project"
HAS_GO = shutil.which("go") is not None


class GoMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def migrate(self, project: Path = CLEAN, *, validate: bool = False):
        return run_migration(
            project, self.out, MigrationOptions(target="go", run_validation=validate)
        )

    def test_every_module_becomes_a_go_file(self) -> None:
        outcome = self.migrate()
        generated = {path for path in outcome.written_files if path.endswith(".go")}
        self.assertEqual(
            generated,
            {
                "pkg/pkg.go",
                "pkg/cycle_a.go",
                "pkg/cycle_b.go",
                "pkg/ledger.go",
                "pkg/models.go",
            },
        )

    def test_a_go_mod_is_written(self) -> None:
        self.migrate()
        self.assertIn("module codeweaver/output", (self.out / "go.mod").read_text(encoding="utf-8"))

    def test_every_file_in_a_directory_shares_one_package(self) -> None:
        self.migrate()
        clauses = {
            line
            for path in (self.out / "pkg").glob("*.go")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("package ")
        }
        self.assertEqual(clauses, {"package pkg"})

    def test_a_dependency_cycle_does_not_become_an_import_cycle(self) -> None:
        # Both halves land in the same Go package, so the cycle disappears
        # rather than becoming an illegal import cycle.
        self.migrate()
        cycle_a = (self.out / "pkg" / "cycle_a.go").read_text(encoding="utf-8")
        # Checked on statement lines: the module docstring mentions the word
        # "import", which a substring match would trip over.
        statements = [
            line for line in cycle_a.splitlines() if line.startswith(("import", "\timport"))
        ]
        self.assertEqual(statements, [])
        # The cross-module call still resolves, because both halves share a
        # package rather than importing each other.
        self.assertIn("return Beta(n - 1)", cycle_a)

    def test_renames_are_reported(self) -> None:
        outcome = self.migrate()
        codes = {item.code for item in outcome.report.diagnostics}
        self.assertIn("CW0730", codes)

    def test_generation_is_deterministic(self) -> None:
        first = {
            path: (self.out / path).read_text(encoding="utf-8")
            for path in self.migrate().written_files
        }
        second_dir = Path(self._tmp.name) / "out2"
        outcome = run_migration(
            CLEAN, second_dir, MigrationOptions(target="go", run_validation=False)
        )
        second = {
            path: (second_dir / path).read_text(encoding="utf-8") for path in outcome.written_files
        }
        self.assertEqual(first, second)

    @unittest.skipUnless(HAS_GO, "go is not installed")
    def test_the_generated_module_builds_and_vets(self) -> None:
        outcome = self.migrate(validate=True)
        check = outcome.report.validation.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.PASSED, msg=check.output)
        self.assertIn("the program was not run", check.reason)

    @unittest.skipUnless(HAS_GO, "go is not installed")
    def test_the_report_records_the_go_version(self) -> None:
        self.assertIn("go", self.migrate(validate=True).report.tool_versions)

    @unittest.skipUnless(HAS_GO, "go is not installed")
    def test_a_build_error_is_reported_against_the_original_source(self) -> None:
        self.migrate()
        target = self.out / "pkg" / "models.go"
        target.write_text(
            target.read_text(encoding="utf-8") + '\nfunc Broken() int { return "no" }\n',
            encoding="utf-8",
        )
        from codeweaver.validation import validate_project

        report = validate_project(self.out, target="go")
        check = report.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.FAILED)
        self.assertTrue(check.diagnostics)
        self.assertTrue(check.diagnostics[0].span.file.endswith("models.go"))

    def test_unsupported_constructs_are_reported_for_this_target_too(self) -> None:
        outcome = self.migrate(UNSUPPORTED)
        self.assertGreater(outcome.report.unsupported_total, 0)
        self.assertEqual(outcome.report.status.value, "incomplete")

    def test_the_report_names_the_go_target(self) -> None:
        self.assertEqual(self.migrate().report.target_language, "go")


class CliTests(unittest.TestCase):
    def test_the_cli_can_target_go(self) -> None:
        import io
        import os
        from contextlib import redirect_stdout

        from codeweaver.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CODEWEAVER_STORE_DIR"] = str(Path(tmp) / "store")
            try:
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = main(
                        [
                            "migrate",
                            str(CLEAN),
                            "--to",
                            "go",
                            "--out",
                            str(Path(tmp) / "out"),
                            "--no-validate",
                        ]
                    )
            finally:
                del os.environ["CODEWEAVER_STORE_DIR"]
        self.assertIn(code, (0, 3), msg=buffer.getvalue())
        self.assertIn("go", buffer.getvalue())

    def test_plan_reports_the_go_toolchain_requirement(self) -> None:
        import io
        from contextlib import redirect_stdout

        from codeweaver.cli import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["plan", str(CLEAN), "--to", "go"])
        self.assertIn("Required toolchains: go", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
