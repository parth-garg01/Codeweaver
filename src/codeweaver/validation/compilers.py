"""Compiling generated C, C++ and Java output with local compilers.

The check is the analogue of ``tsc --noEmit``: it proves the generated project
parses and type-checks, without running it.

* C and C++ are checked with ``-fsyntax-only``, one translation unit per
  process, in parallel across every CPU core.  That is both the fastest way to
  check a project and the closest to how a real build parallelises.
* Java is checked with one ``javac`` invocation over every source file,
  because the JVM's start-up cost dominates and ``javac`` resolves the whole
  project at once anyway.  Class files go to a temporary directory that is
  removed afterwards, so the output directory is left untouched.

Compiler diagnostics are parsed into :class:`Diagnostic` records and mapped
back to the original Python line through the backend's mapping metadata.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from codeweaver.diagnostics import Diagnostic, Severity, SourceSpan
from codeweaver.validation.policy import ExecutionPolicy
from codeweaver.validation.results import CheckOutcome, CheckResult
from codeweaver.validation.toolchain import Toolchain, detect_toolchain
from codeweaver.validation.typescript import SourceMapIndex

__all__ = ["PROFILES", "CompilerProfile", "parse_compiler_output", "run_compile_check"]

#: ``file:line:col: error: message`` (gcc, clang) and ``file:line: error:
#: message`` (javac).
_DIAGNOSTIC = re.compile(
    r"^(?P<file>[^:\n]+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<severity>fatal error|error|warning):\s*(?P<message>.*)$"
)


@dataclass(frozen=True, slots=True)
class CompilerProfile:
    """How to type-check one target language."""

    target: str
    check_name: str
    #: Executables to try, in order, when the environment variable is unset.
    candidates: tuple[str, ...]
    #: Environment variable that overrides the compiler (``CXX``, ``CC``).
    env_var: str
    flags: tuple[str, ...]
    #: Glob patterns, relative to the output directory, of files to compile.
    patterns: tuple[str, ...]
    #: Compile each file in its own process (in parallel), or all at once.
    per_file: bool = True
    #: Pass a temporary class-file directory with ``-d`` (javac).
    output_dir_flag: str = ""
    stage: str = "validate"


PROFILES: dict[str, CompilerProfile] = {
    "cpp": CompilerProfile(
        target="cpp",
        check_name="c++ compile",
        candidates=("c++", "clang++", "g++"),
        env_var="CXX",
        flags=(
            "-std=c++20",
            "-fsyntax-only",
            "-Isrc",
            "-Wall",
            "-Wno-unused-variable",
            "-Wno-unused-parameter",
        ),
        patterns=("src/**/*.cpp",),
        stage="validate/cpp",
    ),
    "c": CompilerProfile(
        target="c",
        check_name="c compile",
        candidates=("cc", "clang", "gcc"),
        env_var="CC",
        flags=(
            "-std=c11",
            "-fsyntax-only",
            "-Isrc",
            "-Wall",
            "-Wno-unused-variable",
            "-Wno-unused-function",
        ),
        patterns=("src/**/*.c",),
        stage="validate/c",
    ),
    "java": CompilerProfile(
        target="java",
        check_name="java compile",
        candidates=("javac",),
        env_var="JAVAC",
        flags=(
            "-encoding",
            "UTF-8",
            "-proc:none",
            "-implicit:none",
            "-Xmaxerrs",
            "1000",
            "-nowarn",
        ),
        patterns=("src/main/java/**/*.java",),
        per_file=False,
        output_dir_flag="-d",
        stage="validate/java",
    ),
}


def parse_compiler_output(
    text: str, *, stage: str, source_map: SourceMapIndex | None = None
) -> list[Diagnostic]:
    """Parse compiler output into diagnostics, in the order reported."""
    found: list[Diagnostic] = []
    seen: set[tuple[str, int, int, str]] = set()
    for raw in text.splitlines():
        match = _DIAGNOSTIC.match(raw.strip())
        if match is None:
            continue
        file = match.group("file").strip()
        line = int(match.group("line"))
        col = int(match.group("col") or 1)
        message = match.group("message").strip()
        key = (file, line, col, message)
        if key in seen:
            continue
        seen.add(key)
        hint = ""
        if source_map is not None:
            resolved = source_map.resolve(file, line)
            if resolved is not None:
                hint = f"generated from {resolved[0]}:{resolved[1]}"
        severity = Severity.WARNING if match.group("severity") == "warning" else Severity.ERROR
        found.append(
            Diagnostic(
                code=f"{stage.split('/')[-1].upper()}-{severity.value.upper()}",
                message=message,
                severity=severity,
                span=SourceSpan(file, line, max(col - 1, 0)),
                stage=stage,
                hint=hint,
            )
        )
    return found


def _find_compiler(profile: CompilerProfile) -> Toolchain:
    override = os.environ.get(profile.env_var, "").strip()
    if override:
        located = shutil.which(override) or override
        return detect_toolchain(located if Path(located).is_absolute() else override)
    last = Toolchain(name=profile.candidates[0], error="not found on PATH")
    for candidate in profile.candidates:
        tool = detect_toolchain(candidate)
        if tool.available:
            return tool
        last = tool
    return last


def _sources(root: Path, profile: CompilerProfile) -> list[Path]:
    found: set[Path] = set()
    for pattern in profile.patterns:
        found.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found)


def _run(command: list[str], root: Path, timeout: float) -> tuple[int | None, str, str]:
    try:
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout}s"
    except OSError as exc:
        return None, "", f"could not run: {exc}"
    return completed.returncode, f"{completed.stdout}{completed.stderr}", ""


def run_compile_check(
    output_dir: Path | str,
    profile: CompilerProfile,
    *,
    policy: ExecutionPolicy | None = None,
    source_map: SourceMapIndex | None = None,
    tool: Toolchain | None = None,
) -> CheckResult:
    """Compile the generated project in *output_dir* without producing a binary."""
    policy = policy or ExecutionPolicy()
    root = Path(output_dir)
    name = profile.check_name
    if not policy.permits("typecheck"):
        return CheckResult.skipped(name, policy.refusal_reason("typecheck"))
    if not root.is_dir():
        return CheckResult(
            name, CheckOutcome.ERRORED, reason=f"output directory does not exist: {root}"
        )
    compiler = tool or _find_compiler(profile)
    if not compiler.available:
        return CheckResult.skipped(
            name,
            f"{compiler.name} is not available ({compiler.error}); install a {profile.target} "
            f"compiler or set {profile.env_var} to type-check generated output",
        )
    sources = _sources(root, profile)
    if not sources:
        return CheckResult(
            name, CheckOutcome.ERRORED, reason="the generated project has no source files"
        )
    relative = [str(path.relative_to(root)) for path in sources]

    started = time.monotonic()
    outputs: list[str] = []
    exit_codes: list[int] = []
    errors: list[str] = []
    if profile.per_file:
        commands = [[compiler.path, *profile.flags, path] for path in relative]
        workers = max(1, min(len(commands), os.cpu_count() or 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(
                pool.map(lambda argv: _run(argv, root, policy.timeout_seconds), commands)
            )
        for code, text, error in results:
            outputs.append(text)
            if error:
                errors.append(error)
            elif code is not None:
                exit_codes.append(code)
        command = [compiler.path, *profile.flags, "<each file>"]
    else:
        with tempfile.TemporaryDirectory(prefix="codeweaver-classes-") as scratch:
            flags = [*profile.flags]
            if profile.output_dir_flag:
                flags += [profile.output_dir_flag, scratch]
            command = [compiler.path, *flags, *relative]
            code, text, error = _run(command, root, policy.timeout_seconds)
            outputs.append(text)
            if error:
                errors.append(error)
            elif code is not None:
                exit_codes.append(code)
    duration = time.monotonic() - started
    text = "".join(outputs)
    if errors:
        return CheckResult(
            name,
            CheckOutcome.ERRORED,
            command=command,
            duration_seconds=duration,
            reason=f"{compiler.name} {errors[0]}",
            output=text[: policy.max_output_bytes],
        )
    diagnostics = parse_compiler_output(text, stage=profile.stage, source_map=source_map)
    failed = any(code != 0 for code in exit_codes)
    return CheckResult(
        name=name,
        outcome=CheckOutcome.FAILED if failed else CheckOutcome.PASSED,
        command=command,
        exit_code=max(exit_codes, default=0),
        duration_seconds=duration,
        diagnostics=diagnostics,
        output=text[: policy.max_output_bytes],
    )
