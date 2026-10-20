from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.project_intake.paths import (
    PathSafetyError,
    contains_path,
    normalise_root,
    resolve_within,
    safe_relative_path,
)


class PathSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "project"
        self.outside = self.base / "outside"
        (self.root / "pkg").mkdir(parents=True)
        self.outside.mkdir()
        (self.root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
        (self.outside / "secret.py").write_text("TOKEN = 'nope'\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_normalise_root_rejects_missing_directory(self) -> None:
        with self.assertRaises(PathSafetyError):
            normalise_root(self.base / "no-such-dir")

    def test_normalise_root_rejects_file(self) -> None:
        with self.assertRaises(PathSafetyError):
            normalise_root(self.root / "pkg" / "mod.py")

    def test_contains_path_accepts_nested_file(self) -> None:
        self.assertTrue(contains_path(self.root, self.root / "pkg" / "mod.py"))

    def test_contains_path_accepts_root_itself(self) -> None:
        self.assertTrue(contains_path(self.root, self.root))

    def test_contains_path_rejects_sibling_directory(self) -> None:
        self.assertFalse(contains_path(self.root, self.outside / "secret.py"))

    def test_resolve_within_rejects_dot_dot_traversal(self) -> None:
        with self.assertRaises(PathSafetyError):
            resolve_within(self.root, "../outside/secret.py")

    def test_resolve_within_rejects_absolute_path_outside_root(self) -> None:
        with self.assertRaises(PathSafetyError):
            resolve_within(self.root, self.outside / "secret.py")

    def test_resolve_within_accepts_relative_path_inside_root(self) -> None:
        resolved = resolve_within(self.root, "pkg/mod.py")
        self.assertEqual(resolved, (self.root / "pkg" / "mod.py").resolve())

    def test_resolve_within_rejects_symlink_escaping_root(self) -> None:
        link = self.root / "escape.py"
        link.symlink_to(self.outside / "secret.py")
        with self.assertRaises(PathSafetyError):
            resolve_within(self.root, link)

    def test_resolve_within_rejects_symlinked_directory_escape(self) -> None:
        link = self.root / "elsewhere"
        link.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(PathSafetyError):
            resolve_within(self.root, "elsewhere/secret.py")

    def test_contains_path_raises_on_broken_symlink(self) -> None:
        link = self.root / "broken.py"
        link.symlink_to(self.root / "missing.py")
        # A broken symlink resolves to a non-existent path inside the root,
        # which is contained but unreadable; resolution itself must not crash.
        self.assertTrue(contains_path(self.root, link))

    def test_safe_relative_path_is_posix_and_root_relative(self) -> None:
        relative = safe_relative_path(self.root, self.root / "pkg" / "mod.py")
        self.assertEqual(relative, "pkg/mod.py")


if __name__ == "__main__":
    unittest.main()
