"""Running the TypeScript compiler over generated output.

Two things make the result useful rather than just a wall of compiler text:

* every ``tsc`` diagnostic is parsed into a :class:`Diagnostic` with the
  generated file and position it refers to;
* the generated-line position is translated back to the original Python line
  using the mapping metadata the backend recorded, so the report can say
  "``pkg/account.py:14``" instead of only "``pkg/account.ts:22``".

``tsc --noEmit`` reads and type-checks; it does not execute the project. That
is why it is the one check the default execution policy allows.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeweaver.diagnostics import Diagnostic, Severity, SourceSpan
from codeweaver.validation.policy import ExecutionPolicy
from codeweaver.validation.results import CheckOutcome, CheckResult
from codeweaver.validation.toolchain import ToolchainIndex, detect_toolchains

__all__ = ["SourceMapIndex", "parse_tsc_output", "run_typecheck"]

#: `path/to/file.ts(12,5): error TS2322: message`
_TSC_LINE = re.compile(
    r"^(?P<file>[^(]+)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<severity>error|warning)\s+(?P<code>TS\d+):\s+(?P<message>.*)$"
)

#: A diagnostic with no position, e.g. a configuration error.
_TSC_GLOBAL = re.compile(r"^(?P<severity>error|warning)\s+(?P<code>TS\d+):\s+(?P<message>.*)$")


@dataclass
class SourceMapIndex:
    """Generated-file line numbers back to original source positions."""

    #: output path -> {output line -> (source file, source line)}
    entries: dict[str, dict[int, tuple[str, int]]] = field(default_factory=dict)

    @classmethod
    def from_mappings(cls, mappings: dict[str, list[dict[str, Any]]]) -> SourceMapIndex:
        index = cls()
        for output_path, rows in mappings.items():
            table: dict[int, tuple[str, int]] = {}
            for row in rows:
                output_line = int(row.get("outputLine", 0))
                source_line = int(row.get("sourceLine", 0))
                source_file = str(row.get("sourceFile", ""))
                if output_line and source_line and source_file:
                    table[output_line] = (source_file, source_line)
            index.entries[output_path] = table
        return index

    def _table_for_suffix(self, output_path: str) -> dict[int, tuple[str, int]] | None:
        """Match a path a compiler reported differently from the key we stored.

        Compilers normalise paths in their own way, so an absolute or
        dot-prefixed path can refer to a file we recorded relatively.  Matching
        on the path suffix recovers the mapping instead of losing the link back
        to the original source.
        """
        normalised = output_path.replace("\\", "/")
        for key, table in self.entries.items():
            if normalised.endswith(f"/{key}") or key.endswith(f"/{normalised}"):
                return table
        return None

    def resolve(self, output_path: str, output_line: int) -> tuple[str, int] | None:
        """The source position for *output_line*, or the nearest one above it.

        ``tsc`` reports the position of an expression, which is often a line
        that carries no mapping of its own (a closing brace, a continuation).
        Walking upwards finds the statement the line belongs to, which is the
        position a reader wants.
        """
        table = self.entries.get(output_path)
        if table is None:
            table = self._table_for_suffix(output_path)
        if not table:
            return None
        for candidate in range(output_line, 0, -1):
            found = table.get(candidate)
            if found is not None:
                return found
        return None


def parse_tsc_output(text: str, *, source_map: SourceMapIndex | None = None) -> list[Diagnostic]:
    """Parse ``tsc`` output into diagnostics, in the order reported."""
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith(" "):
            # Indented lines are continuation detail for the previous entry.
            continue
        match = _TSC_LINE.match(line)
        if match is not None:
            diagnostics.append(_positioned_diagnostic(match, source_map))
            continue
        global_match = _TSC_GLOBAL.match(line)
        if global_match is not None:
            diagnostics.append(
                Diagnostic(
                    code=global_match.group("code"),
                    message=global_match.group("message").strip(),
                    severity=_severity_of(global_match.group("severity")),
                    stage="validate/typescript",
                )
            )
    return diagnostics


def _positioned_diagnostic(match: re.Match[str], source_map: SourceMapIndex | None) -> Diagnostic:
    output_path = match.group("file").strip()
    output_line = int(match.group("line"))
    output_col = int(match.group("col"))
    span = SourceSpan(output_path, output_line, max(output_col - 1, 0))
    hint = ""
    if source_map is not None:
        resolved = source_map.resolve(output_path, output_line)
        if resolved is not None:
            source_file, source_line = resolved
            hint = f"generated from {source_file}:{source_line}"
    return Diagnostic(
        code=match.group("code"),
        message=match.group("message").strip(),
        severity=_severity_of(match.group("severity")),
        span=span,
        stage="validate/typescript",
        hint=hint,
    )


def _severity_of(word: str) -> Severity:
    return Severity.ERROR if word == "error" else Severity.WARNING


def run_typecheck(
    output_dir: Path | str,
    *,
    policy: ExecutionPolicy | None = None,
    toolchains: ToolchainIndex | None = None,
    source_map: SourceMapIndex | None = None,
) -> CheckResult:
    """Type-check the generated project in *output_dir* with ``tsc``."""
    policy = policy or ExecutionPolicy()
    root = Path(output_dir)
    name = "typescript typecheck"

    if not policy.permits("typecheck"):
        return CheckResult.skipped(name, policy.refusal_reason("typecheck"))
    if not root.is_dir():
        return CheckResult(
            name, CheckOutcome.ERRORED, reason=f"output directory does not exist: {root}"
        )
    config = root / "tsconfig.json"
    if not config.is_file():
        return CheckResult(
            name, CheckOutcome.ERRORED, reason="generated project has no tsconfig.json"
        )

    index = toolchains or detect_toolchains(("tsc",), project_root=root)
    tool = index.get("tsc")
    if tool is None or not tool.available:
        return CheckResult.skipped(
            name,
            f"tsc is not available ({tool.error if tool else 'not detected'}); "
            "install TypeScript to type-check generated output",
        )

    # A relative --project keeps tsc's reported paths relative to the output
    # directory, which is what the mapping metadata is keyed by.
    command = [tool.path, "--noEmit", "--pretty", "false", "--project", config.name]
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
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
            reason=f"tsc timed out after {policy.timeout_seconds}s",
        )
    except OSError as exc:
        return CheckResult(
            name,
            CheckOutcome.ERRORED,
            command=command,
            duration_seconds=time.monotonic() - started,
            reason=f"could not run tsc: {exc}",
        )

    duration = time.monotonic() - started
    text = f"{completed.stdout}{completed.stderr}"
    diagnostics = parse_tsc_output(text, source_map=source_map)
    outcome = CheckOutcome.PASSED if completed.returncode == 0 else CheckOutcome.FAILED
    return CheckResult(
        name=name,
        outcome=outcome,
        command=command,
        exit_code=completed.returncode,
        duration_seconds=duration,
        diagnostics=diagnostics,
        output=text[: policy.max_output_bytes],
    )
