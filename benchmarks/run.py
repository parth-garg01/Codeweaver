"""Benchmark CodeWeaver: conversion time per target, and generated-code speed.

Usage::

    PYTHONPATH=src python benchmarks/run.py            # both benchmarks
    PYTHONPATH=src python benchmarks/run.py --convert  # conversion only
    PYTHONPATH=src python benchmarks/run.py --runtime  # runtime only

*Conversion* synthesises a project of ``--modules`` modules (default 300),
each with classes, functions and loops, and reports the best of ``--repeat``
wall-clock runs of the full pipeline (scan, parse, transforms, inference,
generation) with compiler validation switched off.

*Runtime* translates ``benchmarks/workload`` (CPU-bound kernels in the subset
all native targets support), builds each target with optimisation, checks that
its output is identical to CPython's, and reports the best of ``--repeat``
runs.  Targets whose toolchain is missing are skipped.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from codeweaver.pipeline import MigrationOptions, run_migration

HERE = Path(__file__).resolve().parent
WORKLOAD = HERE / "workload"
TARGETS = ("typescript", "c", "cpp", "java")

_MODULE_TEMPLATE = '''"""Synthetic module {index}."""

from pkg.m{previous} import Shape{previous}, scale{previous}


class Shape{index}(Shape{previous}):
    def __init__(self, width: float, height: float) -> None:
        super().__init__(width, height)
        self.label = "shape{index}"

    def area(self) -> float:
        return self.width * self.height * {factor}


def scale{index}(values: list[float], factor: float) -> list[float]:
    out: list[float] = []
    for value in values:
        if value > 0:
            out.append(value * factor)
        else:
            out.append(-value)
    return scale{previous}(out, 1.0)


def total{index}(count: int) -> float:
    total = 0.0
    for i in range(count):
        total += Shape{index}(i * 0.5, 2.0).area()
    return total
'''

_ROOT_MODULE = """class Shape0:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height

    def area(self) -> float:
        return self.width * self.height


def scale0(values: list[float], factor: float) -> list[float]:
    return [value * factor for value in values]
"""


def synthesise(root: Path, modules: int) -> None:
    package = root / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "m0.py").write_text(_ROOT_MODULE)
    for index in range(1, modules):
        text = _MODULE_TEMPLATE.format(index=index, previous=index - 1, factor=1 + index % 5)
        (package / f"m{index}.py").write_text(text)


def bench_conversion(modules: int, repeat: int) -> None:
    print(f"Conversion: {modules} modules, best of {repeat}, validation off")
    print(f"  {'target':<11} {'seconds':>8} {'modules/s':>10} {'lines out':>10}")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "project"
        synthesise(source, modules)
        lines_in = sum(len(path.read_text().splitlines()) for path in source.rglob("*.py"))
        for target in TARGETS:
            best = float("inf")
            out = Path(tmp) / f"out-{target}"
            for _ in range(repeat):
                options = MigrationOptions(target=target, overwrite=True, run_validation=False)
                started = time.perf_counter()
                run_migration(source, out, options)
                best = min(best, time.perf_counter() - started)
            lines_out = sum(
                len(path.read_text().splitlines())
                for path in out.rglob("*")
                if path.is_file()
                and "runtime" not in path.parts
                and path.name not in ("Py.java", "cw.h")
            )
            print(f"  {target:<11} {best:>8.3f} {modules / best:>10.0f} {lines_out:>10}")
        print(f"  ({lines_in} lines of Python in)")


def _tool(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _check(completed: subprocess.CompletedProcess[str], what: str) -> None:
    if completed.returncode != 0:
        raise SystemExit(f"{what} failed:\n{completed.stdout}{completed.stderr}")


def _build(target: str, out: Path) -> list[str] | None:
    def run(argv: list[str]) -> None:
        _check(
            subprocess.run(argv, cwd=out, capture_output=True, text=True, check=False),
            " ".join(argv[:2]),
        )

    if target == "c":
        compiler = _tool("cc", "clang", "gcc")
        if compiler is None:
            return None
        sources = [str(path.relative_to(out)) for path in sorted((out / "src").rglob("*.c"))]
        run([compiler, "-std=c11", "-O3", "-DNDEBUG", "-Isrc", *sources, "-o", "app", "-lm"])
        return [str(out / "app")]
    if target == "cpp":
        compiler = _tool("c++", "clang++", "g++")
        if compiler is None:
            return None
        sources = [str(path.relative_to(out)) for path in sorted((out / "src").rglob("*.cpp"))]
        run([compiler, "-std=c++20", "-O3", "-DNDEBUG", "-Isrc", *sources, "-o", "app"])
        return [str(out / "app")]
    if target == "java":
        javac, java = _tool("javac"), _tool("java")
        if javac is None or java is None:
            return None
        sources = [
            str(path.relative_to(out)) for path in sorted((out / "src/main/java").rglob("*.java"))
        ]
        run([javac, "-encoding", "UTF-8", "-d", "classes", *sources])
        return [java, "-cp", str(out / "classes"), "bench.Main"]
    return None


def _time(command: list[str], cwd: Path, repeat: int) -> tuple[float, str]:
    best, output = float("inf"), ""
    for _ in range(repeat):
        started = time.perf_counter()
        completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        best = min(best, time.perf_counter() - started)
        _check(completed, command[0])
        output = completed.stdout
    return best, output


def bench_runtime(repeat: int) -> None:
    print(f"Runtime: benchmarks/workload, best of {repeat}")
    baseline, expected = _time([sys.executable, "-m", "bench.main"], WORKLOAD, repeat)
    print(f"  {'target':<11} {'seconds':>8} {'speed-up':>9}  output")
    print(f"  {'python':<11} {baseline:>8.3f} {1.0:>8.1f}x  reference ({sys.version.split()[0]})")
    with tempfile.TemporaryDirectory() as tmp:
        for target in ("c", "cpp", "java"):
            out = Path(tmp) / target
            report = run_migration(
                WORKLOAD, out, MigrationOptions(target=target, run_validation=False)
            ).report
            if report.unsupported_total:
                print(
                    f"  {target:<11} skipped: {report.unsupported_total} unsupported construct(s)"
                )
                continue
            command = _build(target, out)
            if command is None:
                print(f"  {target:<11} skipped: toolchain not found")
                continue
            seconds, output = _time(command, out, repeat)
            verdict = "identical" if output == expected else "DIFFERENT"
            print(f"  {target:<11} {seconds:>8.3f} {baseline / seconds:>8.1f}x  {verdict}")
            if output != expected:
                raise SystemExit(f"{target} output differs from CPython:\n{output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--convert", action="store_true", help="only the conversion benchmark")
    parser.add_argument("--runtime", action="store_true", help="only the runtime benchmark")
    parser.add_argument("--modules", type=int, default=300, help="synthetic project size")
    parser.add_argument("--repeat", type=int, default=3, help="runs per measurement")
    args = parser.parse_args()
    both = not args.convert and not args.runtime
    if args.convert or both:
        bench_conversion(args.modules, args.repeat)
    if args.runtime or both:
        if both:
            print()
        bench_runtime(args.repeat)


if __name__ == "__main__":
    main()
