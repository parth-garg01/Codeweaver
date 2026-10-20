"""Generate a project, then validate it with the real local toolchain."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from codeweaver.backends.typescript import TypeScriptBackend
from codeweaver.frontends.python import parse_source
from codeweaver.transforms import default_pipeline
from codeweaver.validation import ExecutionPolicy, validate_project
from codeweaver.validation.results import CheckOutcome

CLEAN_SOURCE = (
    "class Account:\n"
    '    """An account."""\n'
    "\n"
    "    def __init__(self, owner: str, balance: int = 0) -> None:\n"
    "        self.owner = owner\n"
    "        self.balance = balance\n"
    "\n"
    "    def deposit(self, amount: int) -> None:\n"
    "        self.balance += amount\n"
    "\n"
    "    def describe(self) -> str:\n"
    "        return f'{self.owner}: {self.balance}'\n"
    "\n"
    "\n"
    "def total(accounts: list[Account]) -> int:\n"
    "    running: int = 0\n"
    "    for i in range(len(accounts)):\n"
    "        running += accounts[i].balance\n"
    "    return running\n"
)


def generate_project(root: Path, sources: dict[str, str]) -> dict[str, list[dict]]:
    modules = []
    for path, text in sources.items():
        result = parse_source(text, path)
        assert result.module is not None, [d.format() for d in result.diagnostics]
        modules.append(default_pipeline("typescript").run(result.module).module)
    backend = TypeScriptBackend()
    files = backend.generate_project(modules, project_name="integration")
    mappings: dict[str, list[dict]] = {}
    for module in modules:
        generated = backend.generate_module(module)
        mappings[generated.path] = generated.mappings
    for relative, contents in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    return mappings


class ValidateGeneratedProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        self.out.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reports_skipped_checks_for_tests_and_install(self) -> None:
        generate_project(self.out, {"pkg/account.py": CLEAN_SOURCE})
        report = validate_project(self.out)
        names = {check.name for check in report.skipped_checks}
        self.assertIn("migrated tests", names)
        self.assertIn("dependency install", names)

    def test_unsupported_target_is_reported_not_silently_passed(self) -> None:
        report = validate_project(self.out, target="rust")
        self.assertFalse(report.ran_any_check)
        self.assertIn("no validation support", report.skipped_checks[0].reason)

    def test_read_only_policy_runs_nothing(self) -> None:
        generate_project(self.out, {"pkg/account.py": CLEAN_SOURCE})
        report = validate_project(self.out, policy=ExecutionPolicy.read_only())
        self.assertFalse(report.ran_any_check)

    @unittest.skipIf(shutil.which("tsc") is None, "tsc is not installed")
    def test_clean_translation_type_checks(self) -> None:
        mappings = generate_project(self.out, {"pkg/account.py": CLEAN_SOURCE})
        report = validate_project(self.out, mappings=mappings)
        check = report.checks[0]
        self.assertEqual(
            check.outcome,
            CheckOutcome.PASSED,
            msg=f"tsc output:\n{check.output}\n\n"
            + (self.out / "pkg" / "account.ts").read_text(encoding="utf-8"),
        )
        self.assertIn("tsc", report.tool_versions)

    @unittest.skipIf(shutil.which("tsc") is None, "tsc is not installed")
    def test_a_type_error_is_reported_against_the_original_source(self) -> None:
        broken = "def f(n: int) -> str:\n    return n\n"
        mappings = generate_project(self.out, {"broken.py": broken})
        report = validate_project(self.out, mappings=mappings)
        check = report.checks[0]
        self.assertEqual(check.outcome, CheckOutcome.FAILED)
        self.assertTrue(check.diagnostics)
        hints = " ".join(item.hint for item in check.diagnostics)
        self.assertIn("broken.py", hints)


if __name__ == "__main__":
    unittest.main()
