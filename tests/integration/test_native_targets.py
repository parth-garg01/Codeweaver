"""Compile the generated native projects and run them against Python.

For each target with a local compiler, the ``native_project`` fixture is
translated, built with optimisation, and executed; its standard output must be
byte-for-byte what CPython prints.  This is the strongest evidence of
behavioural equivalence the test suite can produce, so it is the gate for every
new backend feature.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.pipeline import MigrationOptions, run_migration
from tests.native_support import NATIVE_PROJECT, python_output, run, tool


def _build_cpp(out: Path) -> list[str]:
    compiler = tool("c++") or tool("clang++") or tool("g++")
    if compiler is None:
        raise unittest.SkipTest("no C++ compiler on PATH")
    sources = [str(path.relative_to(out)) for path in sorted((out / "src").rglob("*.cpp"))]
    built = run([compiler, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc", *sources, "-o", "app"], out)
    if built.returncode != 0:
        raise AssertionError(built.stdout + built.stderr)
    return [str(out / "app")]


BUILDERS = {"cpp": _build_cpp}


class NativeEquivalenceTests(unittest.TestCase):
    expected: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = python_output(NATIVE_PROJECT, "app.main")

    def check(self, target: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            outcome = run_migration(NATIVE_PROJECT, out, MigrationOptions(target=target))
            report = outcome.report
            self.assertEqual(report.status.value, "passed", report.to_dict())
            command = BUILDERS[target](out)
            completed = run(command, out)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, self.expected)

    def test_cpp_matches_python(self) -> None:
        self.check("cpp")


if __name__ == "__main__":
    unittest.main()
