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


STRESS_PROJECT = FIXTURES / "stress_project"
C_STRESS_PROJECT = FIXTURES / "c_stress_project"


class StressEquivalenceTests(unittest.TestCase):
    """Programs written to break the backends, each fixed bug kept as a check.

    They cover annotated fields, polymorphism and aliasing, f-string format
    specifications, generator arguments, enumerate/zip and nested unpacking
    in loops, exceptions, round(), float accumulation (no FMA contraction),
    argument evaluation order and Java lambda scoping.
    """

    def check(self, project: Path, target: str) -> None:
        expected = python_output(project, "app.main")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            outcome = run_migration(project, out, MigrationOptions(target=target))
            # The formatting module has non-ASCII text, which C and C++ warn about.
            self.assertIn(
                outcome.report.status.value,
                ("passed", "passed with warnings"),
                outcome.report.to_dict(),
            )
            self.assertEqual(outcome.report.unsupported_total, 0)
            completed = run(BUILDERS[target](out), out)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, expected)

    def test_cpp_matches_python(self) -> None:
        self.check(STRESS_PROJECT, "cpp")

    def test_java_matches_python(self) -> None:
        self.check(STRESS_PROJECT, "java")

    def test_c_subset_matches_python_in_c(self) -> None:
        self.check(C_STRESS_PROJECT, "c")

    def test_c_subset_matches_python_in_cpp(self) -> None:
        self.check(C_STRESS_PROJECT, "cpp")

    def test_c_subset_matches_python_in_java(self) -> None:
        self.check(C_STRESS_PROJECT, "java")


_OVERFLOW_SOURCE = """def fact(n: int) -> int:
    r = 1
    for i in range(2, n + 1):
        r *= i
    return r


if __name__ == "__main__":
    print(fact(20))
    print(fact(25))
"""


class IntOverflowTests(unittest.TestCase):
    """Python's int is unbounded; a 64-bit result that does not fit must
    raise OverflowError, never print a wrapped value."""

    def check(self, target: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "app").mkdir(parents=True)
            (project / "app" / "__init__.py").write_text("")
            (project / "app" / "main.py").write_text(_OVERFLOW_SOURCE)
            out = Path(tmp) / "out"
            run_migration(project, out, MigrationOptions(target=target, run_validation=False))
            completed = run(BUILDERS[target](out), out)
            self.assertEqual(completed.stdout, "2432902008176640000\n")
            self.assertEqual(completed.returncode, 1)
            self.assertIn("OverflowError", completed.stderr)

    def test_c_raises(self) -> None:
        self.check("c")

    def test_cpp_raises(self) -> None:
        self.check("cpp")

    def test_java_raises(self) -> None:
        self.check("java")


if __name__ == "__main__":
    unittest.main()
