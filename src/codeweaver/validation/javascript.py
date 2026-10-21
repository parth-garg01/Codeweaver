"""Validating generated JavaScript with the local Node runtime.

``node --check`` parses a file and reports syntax errors without executing it,
which is why it is allowed under the default execution policy.  It is a much
weaker check than ``tsc``: it cannot see a type error, a misspelled property or
a wrong arity.  The check's name says "syntax check" for that reason, and the
backend's limitation list says it too — a passing syntax check must not read as
a passing validation.

Every file is checked separately because ``--check`` takes one file at a time,
and the per-file results are folded into one :class:`CheckResult` so the report
has a single entry with every diagnostic.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import time
from pathlib import Path

from codeweaver.diagnostics import Diagnostic, Severity, SourceSpan
from codeweaver.validation.policy import ExecutionPolicy
from codeweaver.validation.results import CheckOutcome, CheckResult
from codeweaver.validation.toolchain import ToolchainIndex, detect_toolchains
from codeweaver.validation.typescript import SourceMapIndex

__all__ = ["parse_node_output", "run_syntax_check"]

#: Node reports `/path/to/file.js:12` then the offending line, then a caret,
#: then a blank line, then `SyntaxError: message`.
_LOCATION = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+)$")
_ERROR = re.compile(r"^(?P<kind>[A-Za-z]*Error): (?P<message>.*)$")


def _relative_path(reported: str, root: Path) -> str:
    """Make a path Node printed relative to the output directory.

    Both sides are resolved first.  Node prints a fully resolved path, while
    the output directory may contain a symlink — on macOS a temporary directory
    is ``/var/...`` where ``/var`` links to ``/private/var`` — and comparing
    the two unresolved makes every lookup miss.
    """
    candidate = Path(reported)
    for base in (root, root.resolve()):
        for target in (candidate, _resolved(candidate)):
            with contextlib.suppress(ValueError):
                return str(target.relative_to(base))
    return reported


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return path


def parse_node_output(
    text: str, *, relative_to: Path | None = None, source_map: SourceMapIndex | None = None
) -> list[Diagnostic]:
    """Parse ``node --check`` stderr into diagnostics."""
    diagnostics: list[Diagnostic] = []
    pending: tuple[str, int] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        location = _LOCATION.match(line)
        if location is not None:
            path = location.group("file")
            if relative_to is not None:
                path = _relative_path(path, relative_to)
            pending = (path, int(location.group("line")))
            continue
        failure = _ERROR.match(line)
        if failure is None:
            continue
        path, number = pending or ("", 0)
        hint = ""
        if source_map is not None and path:
            resolved = source_map.resolve(path, number)
            if resolved is not None:
                hint = f"generated from {resolved[0]}:{resolved[1]}"
        diagnostics.append(
            Diagnostic(
                code=failure.group("kind") or "SyntaxError",
                message=failure.group("message").strip(),
                severity=Severity.ERROR,
                span=SourceSpan(path, number, 0) if path else None,
                stage="validate/javascript",
                hint=hint,
            )
        )
        pending = None
    return diagnostics


def run_syntax_check(
    output_dir: Path | str,
    *,
    policy: ExecutionPolicy | None = None,
    toolchains: ToolchainIndex | None = None,
    source_map: SourceMapIndex | None = None,
) -> CheckResult:
    """Syntax-check every generated ``.js`` file with ``node --check``."""
    policy = policy or ExecutionPolicy()
    root = Path(output_dir)
    name = "javascript syntax check"

    if not policy.permits("typecheck"):
        return CheckResult.skipped(name, policy.refusal_reason("typecheck"))
    if not root.is_dir():
        return CheckResult(
            name, CheckOutcome.ERRORED, reason=f"output directory does not exist: {root}"
        )

    index = toolchains or detect_toolchains(("node",), project_root=root)
    tool = index.get("node")
    if tool is None or not tool.available:
        return CheckResult.skipped(
            name,
            f"node is not available ({tool.error if tool else 'not detected'}); "
            "install Node.js to syntax-check generated JavaScript",
        )

    sources = sorted(path for path in root.rglob("*.js") if path.is_file())
    if not sources:
        return CheckResult.skipped(name, "the generated project has no .js files")

    command = [tool.path, "--check", "<each generated file>"]
    started = time.monotonic()
    diagnostics: list[Diagnostic] = []
    output_parts: list[str] = []
    failed = False
    for source in sources:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [tool.path, "--check", str(source)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=policy.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CheckResult(
                name,
                CheckOutcome.ERRORED,
                command=command,
                duration_seconds=time.monotonic() - started,
                reason=f"node --check timed out after {policy.timeout_seconds}s",
            )
        except OSError as exc:
            return CheckResult(
                name,
                CheckOutcome.ERRORED,
                command=command,
                duration_seconds=time.monotonic() - started,
                reason=f"could not run node: {exc}",
            )
        if completed.returncode != 0:
            failed = True
            text = f"{completed.stdout}{completed.stderr}"
            output_parts.append(text)
            diagnostics.extend(parse_node_output(text, relative_to=root, source_map=source_map))

    return CheckResult(
        name=name,
        outcome=CheckOutcome.FAILED if failed else CheckOutcome.PASSED,
        command=command,
        exit_code=1 if failed else 0,
        duration_seconds=time.monotonic() - started,
        diagnostics=diagnostics,
        output="".join(output_parts)[: policy.max_output_bytes],
        reason=(
            ""
            if failed
            else f"{len(sources)} file(s) parsed; a syntax check cannot detect type errors"
        ),
    )
