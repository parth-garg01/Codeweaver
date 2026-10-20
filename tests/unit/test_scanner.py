from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.project_intake.paths import PathSafetyError
from codeweaver.project_intake.scanner import ScanOptions, scan_project


class ScannerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, text: str = "x = 1\n") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class ScanProjectTests(ScannerTestCase):
    def test_collects_recognised_source_files(self) -> None:
        self.write("a.py")
        self.write("pkg/b.py")
        result = scan_project(self.root)
        self.assertEqual([r.path for r in result.files], ["a.py", "pkg/b.py"])
        self.assertEqual(result.language_counts, {"python": 2})

    def test_results_are_sorted_deterministically(self) -> None:
        for name in ("z.py", "a.py", "m.py"):
            self.write(name)
        first = [r.path for r in scan_project(self.root).files]
        second = [r.path for r in scan_project(self.root).files]
        self.assertEqual(first, ["a.py", "m.py", "z.py"])
        self.assertEqual(first, second)

    def test_records_sha256_and_size(self) -> None:
        self.write("a.py", "print(1)\n")
        record = scan_project(self.root).files[0]
        self.assertEqual(record.size_bytes, 9)
        self.assertEqual(len(record.sha256), 64)

    def test_default_ignores_prune_caches_and_venvs(self) -> None:
        self.write("__pycache__/a.py")
        self.write(".venv/lib/b.py")
        self.write("node_modules/c.js")
        self.write("keep.py")
        result = scan_project(self.root)
        self.assertEqual([r.path for r in result.files], ["keep.py"])

    def test_gitignore_is_honoured(self) -> None:
        (self.root / ".gitignore").write_text("generated/\n", encoding="utf-8")
        self.write("generated/a.py")
        self.write("keep.py")
        result = scan_project(self.root)
        self.assertEqual([r.path for r in result.files], ["keep.py"])
        self.assertTrue(any(s.reason == "ignored" for s in result.skipped))

    def test_codeweaverignore_can_re_include(self) -> None:
        (self.root / ".gitignore").write_text("*.py\n", encoding="utf-8")
        (self.root / ".codeweaverignore").write_text("!keep.py\n", encoding="utf-8")
        self.write("keep.py")
        self.write("drop.py")
        result = scan_project(self.root)
        self.assertEqual([r.path for r in result.files], ["keep.py"])

    def test_unrecognised_extensions_are_skipped_with_a_reason(self) -> None:
        self.write("notes.txt", "hello\n")
        result = scan_project(self.root)
        self.assertEqual(result.files, [])
        reasons = {s.reason for s in result.skipped}
        self.assertIn("unrecognised-extension", reasons)

    def test_symlinked_file_is_skipped_by_default(self) -> None:
        self.write("real.py")
        (self.root / "link.py").symlink_to(self.root / "real.py")
        result = scan_project(self.root)
        self.assertEqual([r.path for r in result.files], ["real.py"])
        self.assertTrue(any(s.reason == "symlink" for s in result.skipped))

    def test_symlink_escaping_root_is_never_followed(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.py").write_text("TOKEN = 1\n", encoding="utf-8")
        (self.root / "escape").symlink_to(outside, target_is_directory=True)
        self.write("keep.py")
        result = scan_project(self.root, ScanOptions(follow_symlinks=True))
        self.assertNotIn("escape/secret.py", [r.path for r in result.files])

    def test_oversized_files_are_skipped_and_warned_about(self) -> None:
        self.write("big.py", "x = 1\n" * 100)
        result = scan_project(self.root, ScanOptions(max_file_bytes=10))
        self.assertEqual(result.files, [])
        self.assertTrue(any(d.code == "CW0104" for d in result.diagnostics))

    def test_language_filter_restricts_results(self) -> None:
        self.write("a.py")
        self.write("b.ts", "const b = 1;\n")
        result = scan_project(self.root, ScanOptions(include_languages=("python",)))
        self.assertEqual([r.path for r in result.files], ["a.py"])

    def test_total_bytes_and_files_for_language(self) -> None:
        self.write("a.py", "x\n")
        self.write("b.ts", "y\n")
        result = scan_project(self.root)
        self.assertEqual(result.total_bytes, 4)
        self.assertEqual(len(result.files_for("typescript")), 1)

    def test_missing_root_is_rejected(self) -> None:
        with self.assertRaises(PathSafetyError):
            scan_project(self.root / "nope")

    def test_to_dict_is_json_serialisable(self) -> None:
        import json

        self.write("a.py")
        payload = json.dumps(scan_project(self.root).to_dict())
        self.assertIn('"languageCounts"', payload)


if __name__ == "__main__":
    unittest.main()
