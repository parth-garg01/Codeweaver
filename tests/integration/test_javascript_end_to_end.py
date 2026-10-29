"""The JavaScript target, end to end against the real Node runtime."""

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
HAS_NODE = shutil.which("node") is not None


class JavaScriptMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def migrate(self, project: Path = CLEAN, *, validate: bool = False):
        return run_migration(
            project,
            self.out,
            MigrationOptions(target="javascript", run_validation=validate),
        )

    def test_every_module_becomes_a_js_file(self) -> None:
        outcome = self.migrate()
        generated = {path for path in outcome.written_files if path.endswith(".js")}
        self.assertEqual(
            generated,
            {
                "pkg/index.js",
                "pkg/cycle_a.js",
                "pkg/cycle_b.js",
                "pkg/ledger.js",
                "pkg/models.js",
            },
        )

    def test_the_manifest_declares_an_es_module_project(self) -> None:
        import json

        self.migrate()
        payload = json.loads((self.out / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["type"], "module")

    def test_no_tsconfig_is_written(self) -> None:
        self.migrate()
        self.assertFalse((self.out / "tsconfig.json").exists())

    def test_relative_imports_point_at_js_modules(self) -> None:
        self.migrate()
        ledger = (self.out / "pkg" / "ledger.js").read_text(encoding="utf-8")
        self.assertIn('from "./models"', ledger)

    def test_the_translated_count_uses_the_target_extension(self) -> None:
        self.assertEqual(self.migrate().report.counts.translated, 5)

    def test_generation_is_deterministic(self) -> None:
        first = {
            path: (self.out / path).read_text(encoding="utf-8")
            for path in self.migrate().written_files
        }
        second_dir = Path(self._tmp.name) / "out2"
        second_outcome = run_migration(
            CLEAN, second_dir, MigrationOptions(target="javascript", run_validation=False)
        )
        second = {
            path: (second_dir / path).read_text(encoding="utf-8")
            for path in second_outcome.written_files
        }
        self.assertEqual(first, second)

    @unittest.skipUnless(HAS_NODE, "node is not installed")
    def test_the_generated_project_parses(self) -> None:
        outcome = self.migrate(validate=True)
        check = outcome.report.validation.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.PASSED, msg=check.output)
        self.assertEqual(check.name, "javascript syntax check")

    @unittest.skipUnless(HAS_NODE, "node is not installed")
    def test_the_check_says_what_it_cannot_catch(self) -> None:
        check = self.migrate(validate=True).report.validation.checks[0]
        self.assertIn("cannot detect type errors", check.reason)

    @unittest.skipUnless(HAS_NODE, "node is not installed")
    def test_the_report_records_the_node_version(self) -> None:
        self.assertIn("node", self.migrate(validate=True).report.tool_versions)

    @unittest.skipUnless(HAS_NODE, "node is not installed")
    def test_a_syntax_error_is_reported_against_the_original_source(self) -> None:
        # Nothing CodeWeaver generates should be unparseable, so the failure
        # path is exercised by corrupting the output after generation.
        self.migrate()
        target = self.out / "pkg" / "models.js"
        target.write_text(target.read_text(encoding="utf-8") + "\nclass {\n", encoding="utf-8")
        from codeweaver.validation import validate_project

        report = validate_project(self.out, target="javascript")
        check = report.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.FAILED)
        self.assertTrue(check.diagnostics)
        self.assertEqual(check.diagnostics[0].span.file, "pkg/models.js")

    def test_unsupported_constructs_are_still_reported_for_this_target(self) -> None:
        outcome = self.migrate(UNSUPPORTED)
        self.assertGreater(outcome.report.unsupported_total, 0)
        self.assertEqual(outcome.report.status.value, "incomplete")

    def test_the_report_names_the_javascript_target(self) -> None:
        self.assertEqual(self.migrate().report.target_language, "javascript")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_cli_can_target_javascript(self) -> None:
        import io
        import os
        from contextlib import redirect_stdout

        from codeweaver.cli import main

        os.environ["CODEWEAVER_STORE_DIR"] = str(Path(self._tmp.name) / "store")
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = main(
                    [
                        "migrate",
                        str(CLEAN),
                        "--to",
                        "javascript",
                        "--out",
                        str(self.out),
                        "--no-validate",
                    ]
                )
        finally:
            del os.environ["CODEWEAVER_STORE_DIR"]
        self.assertEqual(code, 3, msg=buffer.getvalue())
        self.assertIn("javascript", buffer.getvalue())

    def test_languages_lists_the_new_pair(self) -> None:
        import io
        from contextlib import redirect_stdout

        from codeweaver.cli import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main(["languages"])
        self.assertIn("python -> javascript", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
