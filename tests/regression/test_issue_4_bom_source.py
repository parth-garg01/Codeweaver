"""Regression test for issue #4.

A UTF-8 byte-order mark made a source file unparseable even though CPython
accepts such files. These tests fail on the code before the fix.

https://github.com/parth-garg01/Codeweaver/issues/4
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from codeweaver.pipeline import MigrationOptions, analyse_project, run_migration
from codeweaver.project_intake.ignore import load_ignore_rules
from codeweaver.project_intake.manifests import detect_manifests
from codeweaver.project_intake.textio import decode_source, read_project_text

BOM = b"\xef\xbb\xbf"
SOURCE = b"def add(a: int, b: int) -> int:\n    return a + b\n"


class DecodingTests(unittest.TestCase):
    def test_a_byte_order_mark_is_stripped(self) -> None:
        self.assertEqual(decode_source(BOM + SOURCE), SOURCE.decode("utf-8"))

    def test_plain_utf8_is_unchanged(self) -> None:
        self.assertEqual(decode_source(SOURCE), SOURCE.decode("utf-8"))

    def test_non_ascii_without_a_mark_is_unchanged(self) -> None:
        raw = "café = 1\n".encode()
        self.assertEqual(decode_source(raw), "café = 1\n")

    def test_the_decoded_source_is_parseable(self) -> None:
        # The exact failure from the issue: ast.parse on a str that still
        # carries U+FEFF raises "invalid non-printable character U+FEFF".
        ast.parse(decode_source(BOM + SOURCE))

    def test_cpython_accepts_the_same_bytes(self) -> None:
        # Establishes the premise: the file is valid Python, so CodeWeaver
        # rejecting it was a defect rather than a strictness choice.
        ast.parse(BOM + SOURCE)


class ProjectFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "p"
        (self.root / "pkg").mkdir(parents=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, raw: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_read_project_text_strips_the_mark(self) -> None:
        path = self.write("pkg/m.py", BOM + SOURCE)
        self.assertEqual(read_project_text(path), SOURCE.decode("utf-8"))

    def test_a_bom_prefixed_module_parses(self) -> None:
        self.write("pkg/__init__.py", b"")
        self.write("pkg/bom.py", BOM + SOURCE)
        analysis = analyse_project(self.root)
        self.assertEqual(analysis.parse_failures, [])
        self.assertIn("pkg.bom", [module.name for module in analysis.modules])
        self.assertFalse(analysis.diagnostics.has_errors)

    def test_a_bom_prefixed_module_is_translated(self) -> None:
        self.write("pkg/__init__.py", b"")
        self.write("pkg/bom.py", BOM + SOURCE)
        with tempfile.TemporaryDirectory() as out:
            outcome = run_migration(
                self.root, Path(out) / "ts", MigrationOptions(run_validation=False)
            )
            self.assertIn("pkg/bom.ts", outcome.written_files)
            contents = (Path(out) / "ts" / "pkg" / "bom.ts").read_text(encoding="utf-8")
            self.assertIn("export function add", contents)
            self.assertNotIn("﻿", contents)

    def test_a_bom_prefixed_ignore_file_keeps_its_first_pattern(self) -> None:
        self.write(".codeweaverignore", BOM + b"generated/\n")
        rules = load_ignore_rules(self.root)
        self.assertTrue(rules.is_ignored("generated", is_dir=True))

    def test_a_bom_prefixed_manifest_is_read(self) -> None:
        self.write(
            "pyproject.toml",
            BOM + b'[project]\nname = "bom-project"\nversion = "1.0.0"\n',
        )
        manifest = detect_manifests(self.root)[0]
        self.assertTrue(manifest.parsed)
        self.assertEqual(manifest.project_name, "bom-project")


class SyntaxErrorHintTests(unittest.TestCase):
    def test_a_real_syntax_error_still_reports_an_accurate_hint(self) -> None:
        from codeweaver.frontends.python import parse_source

        result = parse_source("def broken(:\n    pass\n", "m.py")
        self.assertFalse(result.ok)
        diagnostic = result.diagnostics.errors[0]
        self.assertEqual(diagnostic.code, "CW0410")
        self.assertRegex(diagnostic.hint, r"Python version it runs on \(\d+\.\d+\)")


if __name__ == "__main__":
    unittest.main()
