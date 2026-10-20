"""What CodeWeaver is allowed to execute.

A migration's input is untrusted: the source project, its manifests and its
build scripts all come from somewhere else.  So does the generated output, in
the sense that nobody has reviewed it yet.  The policy makes the distinction
explicit:

* **Type checking** reads files and reports.  ``tsc --noEmit`` does not execute
  the project's code, so it is allowed by default.
* **Running tests** executes the generated code, and transitively anything it
  imports.  Off by default.
* **Installing packages** runs third-party lifecycle scripts and needs the
  network.  Off by default.

Nothing in the pipeline may execute anything the policy has not enabled, and a
check that the policy forbids is reported as skipped with the policy as the
reason — never as a pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ExecutionPolicy"]


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Which classes of command may run, and under what limits."""

    #: Run parsers, formatters, compilers and type checkers.  These read the
    #: generated sources; they do not run them.
    allow_typecheck: bool = True
    #: Run the migrated test suite.  This executes generated code.
    allow_tests: bool = False
    #: Install dependencies.  This needs the network and runs package scripts.
    allow_install: bool = False
    #: Wall-clock limit for any single command.
    timeout_seconds: float = 300.0
    #: Bytes of command output kept per check.
    max_output_bytes: int = 64 * 1024

    @classmethod
    def read_only(cls) -> ExecutionPolicy:
        """A policy that runs nothing at all."""
        return cls(allow_typecheck=False, allow_tests=False, allow_install=False)

    @classmethod
    def permissive(cls, *, timeout_seconds: float = 300.0) -> ExecutionPolicy:
        """A policy that allows every class of command.

        Only appropriate when the user has explicitly asked for it; the CLI
        requires a flag per class rather than offering this as a default.
        """
        return cls(
            allow_typecheck=True,
            allow_tests=True,
            allow_install=True,
            timeout_seconds=timeout_seconds,
        )

    def permits(self, kind: str) -> bool:
        return {
            "typecheck": self.allow_typecheck,
            "tests": self.allow_tests,
            "install": self.allow_install,
        }.get(kind, False)

    def refusal_reason(self, kind: str) -> str:
        return {
            "typecheck": "type checking is disabled by the execution policy",
            "tests": "running tests executes generated code; enable it explicitly",
            "install": "installing packages needs the network; enable it explicitly",
        }.get(kind, f"{kind} is not permitted by the execution policy")

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowTypecheck": self.allow_typecheck,
            "allowTests": self.allow_tests,
            "allowInstall": self.allow_install,
            "timeoutSeconds": self.timeout_seconds,
            "maxOutputBytes": self.max_output_bytes,
        }
