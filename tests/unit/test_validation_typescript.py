from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.diagnostics import Severity
from codeweaver.validation.policy import ExecutionPolicy
from codeweaver.validation.results import CheckOutcome
from codeweaver.validation.toolchain import Toolchain, ToolchainIndex
from codeweaver.validation.typescript import (
    SourceMapIndex,
    parse_tsc_output,
    run_typecheck,
)

TSC_OUTPUT = (
    "pkg/account.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    "pkg/account.ts(20,1): warning TS6133: 'unused' is declared but never read.\n"
    "    Related information here\n"
    "error TS5083: Cannot read file 'tsconfig.json'.\n"
    "\n"
    "Found 2 errors.\n"
)


class PolicyTests(unittest.TestCase):
    def test_typechecking_is_allowed_by_default(self) -> None:
        policy = ExecutionPolicy()
        self.assertTrue(policy.permits("typecheck"))

    def test_running_tests_and_installing_are_off_by_default(self) -> None:
        policy = ExecutionPolicy()
        self.assertFalse(policy.permits("tests"))
        self.assertFalse(policy.permits("install"))

    def test_read_only_policy_permits_nothing(self) -> None:
        policy = ExecutionPolicy.read_only()
        self.assertFalse(policy.permits("typecheck"))

    def test_permissive_policy_permits_everything(self) -> None:
        policy = ExecutionPolicy.permissive()
        for kind in ("typecheck", "tests", "install"):
            self.assertTrue(policy.permits(kind), kind)

    def test_unknown_kind_is_refused(self) -> None:
        self.assertFalse(ExecutionPolicy.permissive().permits("launch-rockets"))

    def test_refusal_reasons_explain_the_risk(self) -> None:
        policy = ExecutionPolicy()
        self.assertIn("executes generated code", policy.refusal_reason("tests"))
        self.assertIn("network", policy.refusal_reason("install"))

    def test_to_dict_is_json_friendly(self) -> None:
        import json

        json.dumps(ExecutionPolicy().to_dict())


class TscParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.diagnostics = parse_tsc_output(TSC_OUTPUT)

    def test_each_reported_entry_becomes_a_diagnostic(self) -> None:
        self.assertEqual(len(self.diagnostics), 3)

    def test_positions_are_parsed(self) -> None:
        first = self.diagnostics[0]
        self.assertEqual(first.code, "TS2322")
        self.assertEqual(first.span.file, "pkg/account.ts")
        self.assertEqual(first.span.start_line, 12)
        self.assertEqual(first.span.start_col, 4)

    def test_severity_is_parsed(self) -> None:
        self.assertEqual(self.diagnostics[0].severity, Severity.ERROR)
        self.assertEqual(self.diagnostics[1].severity, Severity.WARNING)

    def test_message_text_is_kept(self) -> None:
        self.assertIn("not assignable", self.diagnostics[0].message)

    def test_positionless_diagnostic_is_kept(self) -> None:
        self.assertEqual(self.diagnostics[2].code, "TS5083")
        self.assertIsNone(self.diagnostics[2].span)

    def test_continuation_and_summary_lines_are_not_diagnostics(self) -> None:
        codes = [item.code for item in self.diagnostics]
        self.assertEqual(codes, ["TS2322", "TS6133", "TS5083"])

    def test_empty_output_produces_nothing(self) -> None:
        self.assertEqual(parse_tsc_output(""), [])

    def test_unparseable_output_is_ignored_rather_than_guessed_at(self) -> None:
        self.assertEqual(parse_tsc_output("something unexpected\n"), [])


class SourceMapTests(unittest.TestCase):
    def index(self) -> SourceMapIndex:
        return SourceMapIndex.from_mappings(
            {
                "pkg/account.ts": [
                    {"outputLine": 4, "sourceFile": "pkg/account.py", "sourceLine": 1},
                    {"outputLine": 10, "sourceFile": "pkg/account.py", "sourceLine": 7},
                ]
            }
        )

    def test_exact_line_resolves(self) -> None:
        self.assertEqual(self.index().resolve("pkg/account.ts", 10), ("pkg/account.py", 7))

    def test_unmapped_line_resolves_to_the_nearest_statement_above(self) -> None:
        self.assertEqual(self.index().resolve("pkg/account.ts", 12), ("pkg/account.py", 7))

    def test_line_above_every_mapping_is_unresolved(self) -> None:
        self.assertIsNone(self.index().resolve("pkg/account.ts", 2))

    def test_unknown_file_is_unresolved(self) -> None:
        self.assertIsNone(self.index().resolve("other.ts", 10))

    def test_incomplete_rows_are_discarded(self) -> None:
        index = SourceMapIndex.from_mappings(
            {"a.ts": [{"outputLine": 0, "sourceFile": "", "sourceLine": 0}]}
        )
        self.assertIsNone(index.resolve("a.ts", 1))

    def test_diagnostics_gain_a_source_hint(self) -> None:
        diagnostics = parse_tsc_output(TSC_OUTPUT, source_map=self.index())
        self.assertIn("generated from pkg/account.py:7", diagnostics[0].hint)

    def test_diagnostic_without_a_mapping_has_no_hint(self) -> None:
        diagnostics = parse_tsc_output(
            "other.ts(3,1): error TS1005: ';' expected.\n", source_map=self.index()
        )
        self.assertEqual(diagnostics[0].hint, "")


class RunTypecheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_config(self) -> None:
        (self.out / "tsconfig.json").write_text("{}", encoding="utf-8")

    def unavailable(self) -> ToolchainIndex:
        index = ToolchainIndex()
        index.add(Toolchain("tsc", error="not found on PATH"))
        return index

    def test_policy_refusal_skips_the_check(self) -> None:
        self.write_config()
        result = run_typecheck(self.out, policy=ExecutionPolicy.read_only())
        self.assertEqual(result.outcome, CheckOutcome.SKIPPED)
        self.assertIn("execution policy", result.reason)

    def test_missing_output_directory_errors(self) -> None:
        result = run_typecheck(self.out / "nope")
        self.assertEqual(result.outcome, CheckOutcome.ERRORED)
        self.assertIn("does not exist", result.reason)

    def test_missing_tsconfig_errors(self) -> None:
        result = run_typecheck(self.out)
        self.assertEqual(result.outcome, CheckOutcome.ERRORED)
        self.assertIn("tsconfig.json", result.reason)

    def test_missing_tsc_is_skipped_with_an_actionable_reason(self) -> None:
        self.write_config()
        result = run_typecheck(self.out, toolchains=self.unavailable())
        self.assertEqual(result.outcome, CheckOutcome.SKIPPED)
        self.assertIn("install TypeScript", result.reason)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()


class SourceMapSuffixTests(unittest.TestCase):
    def test_absolute_reported_path_matches_a_relative_mapping(self) -> None:
        index = SourceMapIndex.from_mappings(
            {"pkg/a.ts": [{"outputLine": 3, "sourceFile": "pkg/a.py", "sourceLine": 2}]}
        )
        resolved = index.resolve("/tmp/build/pkg/a.ts", 3)
        self.assertEqual(resolved, ("pkg/a.py", 2))

    def test_unrelated_path_still_does_not_resolve(self) -> None:
        index = SourceMapIndex.from_mappings(
            {"pkg/a.ts": [{"outputLine": 3, "sourceFile": "pkg/a.py", "sourceLine": 2}]}
        )
        self.assertIsNone(index.resolve("/tmp/build/other/b.ts", 3))
