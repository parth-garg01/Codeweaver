"""Validating generated Go with the local toolchain.

``go build ./...`` is the right check: Go's compiler is strict about things a
translation gets wrong — an unused import or variable, a type mismatch, a
missing return — so a passing build is meaningful evidence that the generated
package is internally consistent.

``go vet`` is run afterwards when the build succeeded. It catches a further
class of mistakes (a format string that does not match its arguments, for
example) that a translation from f-strings can plausibly produce.

Neither command executes the translated program. ``go build`` compiles it, and
compiling is not running, which is why both are allowed under the default
execution policy. Nothing invokes ``go run`` or ``go test``.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from codeweaver.diagnostics import Diagnostic, Severity, SourceSpan
from codeweaver.validation.policy import ExecutionPolicy
from codeweaver.validation.results import CheckOutcome, CheckResult
from codeweaver.validation.toolchain import ToolchainIndex, detect_toolchains
from codeweaver.validation.typescript import SourceMapIndex

__all__ = ["parse_go_output", "run_build"]

#: `pkg/models.go:12:5: undefined: Foo` — the column is optional.
_LINE = re.compile(
    r"^(?P<file>[^\s:][^:]*\.go):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<message>.+)$"
)


def parse_go_output(
    text: str, *, source_map: SourceMapIndex | None = None, stage: str = "validate/go"
) -> list[Diagnostic]:
    """Parse ``go build`` or ``go vet`` output into diagnostics."""
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            # `# codeweaver/output/pkg` headers name the package being built.
            continue
        match = _LINE.match(line)
        if match is None:
            continue
        path = match.group("file")
        number = int(match.group("line"))
        column = int(match.group("col") or 1)
        hint = ""
        if source_map is not None:
            resolved = source_map.resolve(path, number)
            if resolved is not None:
                hint = f"generated from {resolved[0]}:{resolved[1]}"
        diagnostics.append(
            Diagnostic(
                code="GO",
                message=match.group("message").strip(),
                severity=Severity.ERROR,
                span=SourceSpan(path, number, max(column - 1, 0)),
                stage=stage,
                hint=hint,
            )
        )
    return diagnostics


def run_build(
    output_dir: Path | str,
    *,
    policy: ExecutionPolicy | None = None,
    toolchains: ToolchainIndex | None = None,
    source_map: SourceMapIndex | None = None,
) -> CheckResult:
    """Build the generated Go module, then vet it."""
    policy = policy or ExecutionPolicy()
    root = Path(output_dir)
    name = "go build"

    if not policy.permits("typecheck"):
        return CheckResult.skipped(name, policy.refusal_reason("typecheck"))
    if not root.is_dir():
        return CheckResult(
            name, CheckOutcome.ERRORED, reason=f"output directory does not exist: {root}"
        )
    if not (root / "go.mod").is_file():
        return CheckResult(name, CheckOutcome.ERRORED, reason="the generated project has no go.mod")

    index = toolchains or detect_toolchains(("go",), project_root=root)
    tool = index.get("go")
    if tool is None or not tool.available:
        return CheckResult.skipped(
            name,
            f"go is not available ({tool.error if tool else 'not detected'}); "
            "install Go to build generated output",
        )

    started = time.monotonic()
    command = [tool.path, "build", "./..."]
    build = _run(command, root, policy)
    if isinstance(build, CheckResult):
        return build
    text = f"{build.stdout}{build.stderr}"
    if build.returncode != 0:
        return CheckResult(
            name=name,
            outcome=CheckOutcome.FAILED,
            command=command,
            exit_code=build.returncode,
            duration_seconds=time.monotonic() - started,
            diagnostics=parse_go_output(text, source_map=source_map),
            output=text[: policy.max_output_bytes],
        )

    # The build passed, so vet can add information rather than noise.
    vet_command = [tool.path, "vet", "./..."]
    vet = _run(vet_command, root, policy)
    if isinstance(vet, CheckResult):
        return vet
    vet_text = f"{vet.stdout}{vet.stderr}"
    diagnostics = parse_go_output(vet_text, source_map=source_map, stage="validate/go-vet")
    return CheckResult(
        name=name,
        outcome=CheckOutcome.FAILED if vet.returncode != 0 else CheckOutcome.PASSED,
        command=command + ["&&", *vet_command[1:]],
        exit_code=vet.returncode,
        duration_seconds=time.monotonic() - started,
        diagnostics=diagnostics,
        output=(text + vet_text)[: policy.max_output_bytes],
        reason="" if vet.returncode != 0 else "built and vetted; the program was not run",
    )


def _run(command: list[str], root: Path, policy: ExecutionPolicy):
    """Run *command*, or return a CheckResult describing why it could not."""
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=policy.timeout_seconds,
            check=False,
            # A generated project has no dependencies, so the build must never
            # reach the network; this makes that explicit rather than hopeful.
            env={
                "GOFLAGS": "-mod=mod",
                "GOPROXY": "off",
                "HOME": str(Path.home()),
                "PATH": str(Path(command[0]).parent),
            },
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "go build",
            CheckOutcome.ERRORED,
            command=command,
            reason=f"{' '.join(command[1:])} timed out after {policy.timeout_seconds}s",
        )
    except OSError as exc:
        return CheckResult(
            "go build", CheckOutcome.ERRORED, command=command, reason=f"could not run go: {exc}"
        )
