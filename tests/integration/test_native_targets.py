"""Compile the generated native projects and run them against Python.

For each target with a local compiler, a fixture is translated, built with
optimisation and warnings as errors, and executed; its standard output and exit
status must be exactly what CPython produces.  ``native_project`` covers the
C++ and Java subset; ``c_project`` covers the narrower C subset and is run on
all three native targets.  This is the strongest evidence of
behavioural equivalence the test suite can produce, so it is the gate for every
new backend feature.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from codeweaver.pipeline import MigrationOptions, run_migration
from tests.native_support import FIXTURES, NATIVE_PROJECT, python_output, run, tool

C_PROJECT = FIXTURES / "c_project"


def _build_c(out: Path) -> list[str]:
    compiler = tool("cc") or tool("clang") or tool("gcc")
    if compiler is None:
        raise unittest.SkipTest("no C compiler on PATH")
    sources = [str(path.relative_to(out)) for path in sorted((out / "src").rglob("*.c"))]
    built = run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Isrc",
            *sources,
            "-o",
            "app",
            "-lm",
        ],
        out,
    )
    if built.returncode != 0:
        raise AssertionError(built.stdout + built.stderr)
    return [str(out / "app")]


def _build_cpp(out: Path) -> list[str]:
    compiler = tool("c++") or tool("clang++") or tool("g++")
    if compiler is None:
        raise unittest.SkipTest("no C++ compiler on PATH")
    sources = [str(path.relative_to(out)) for path in sorted((out / "src").rglob("*.cpp"))]
    built = run(
        [
            compiler,
            "-std=c++20",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Isrc",
            *sources,
            "-o",
            "app",
        ],
        out,
    )
    if built.returncode != 0:
        raise AssertionError(built.stdout + built.stderr)
    return [str(out / "app")]


def _build_java(out: Path) -> list[str]:
    javac, java = tool("javac"), tool("java")
    if javac is None or java is None:
        raise unittest.SkipTest("no JDK on PATH")
    sources = [
        str(path.relative_to(out)) for path in sorted((out / "src/main/java").rglob("*.java"))
    ]
    built = run(
        [javac, "-encoding", "UTF-8", "-Xlint:all", "-Werror", "-d", "classes", *sources], out
    )
    if built.returncode != 0:
        raise AssertionError(built.stdout + built.stderr)
    return [java, "-cp", str(out / "classes"), "app.Main"]


BUILDERS = {"c": _build_c, "cpp": _build_cpp, "java": _build_java}


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

    def test_java_matches_python(self) -> None:
        self.check("java")


class CSubsetEquivalenceTests(unittest.TestCase):
    """The C subset fixture ends with an uncaught ValueError, as in Python."""

    expected: str

    @classmethod
    def setUpClass(cls) -> None:
        completed = run([sys.executable, "-m", "app.main"], C_PROJECT)
        assert completed.returncode == 1, completed.stderr
        assert completed.stderr.rstrip().endswith("ValueError: no values"), completed.stderr
        cls.expected = completed.stdout

    def check(self, target: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            outcome = run_migration(C_PROJECT, out, MigrationOptions(target=target))
            self.assertEqual(outcome.report.status.value, "passed", outcome.report.to_dict())
            completed = run(BUILDERS[target](out), out)
            self.assertEqual(completed.stdout, self.expected)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("no values", completed.stderr)

    def test_c_matches_python(self) -> None:
        self.check("c")

    def test_cpp_matches_python(self) -> None:
        self.check("cpp")

    def test_java_matches_python(self) -> None:
        self.check("java")


if __name__ == "__main__":
    unittest.main()
