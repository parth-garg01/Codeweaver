"""Finding and describing local toolchains.

CodeWeaver never installs anything.  It looks for executables the user already
has, records the version it found, and reports a missing toolchain as a skipped
check rather than a failure — a project cannot be blamed for the absence of
``tsc`` on the machine running the migration.

Version probes run with a short timeout and no shell, and their output is only
ever read, never evaluated.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Toolchain", "ToolchainIndex", "detect_toolchain", "detect_toolchains"]

#: Seconds allowed for a ``--version`` probe.
VERSION_TIMEOUT = 15.0

#: tool name -> arguments that print its version.
#:
#: Not every tool accepts ``--version``: ``go`` wants a ``version``
#: subcommand and rejects the flag outright, which made it look uninstalled.
#: The default is ``--version`` for anything not listed.
_VERSION_ARGS: dict[str, tuple[str, ...]] = {
    "tsc": ("--version",),
    "node": ("--version",),
    "npx": ("--version",),
    "npm": ("--version",),
    "prettier": ("--version",),
    "go": ("version",),
    "gofmt": ("-h",),
    "java": ("-version",),
    "javac": ("-version",),
    "rustc": ("--version",),
    "cargo": ("--version",),
    "g++": ("--version",),
    "gcc": ("--version",),
}


@dataclass(frozen=True, slots=True)
class Toolchain:
    """One external tool, found or not."""

    name: str
    path: str = ""
    version: str = ""
    error: str = ""

    @property
    def available(self) -> bool:
        return bool(self.path) and not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "version": self.version,
            "available": self.available,
            "error": self.error,
        }


@dataclass
class ToolchainIndex:
    """Every toolchain CodeWeaver looked for."""

    tools: dict[str, Toolchain] = field(default_factory=dict)

    def add(self, tool: Toolchain) -> Toolchain:
        self.tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Toolchain | None:
        return self.tools.get(name)

    def available(self, name: str) -> bool:
        tool = self.tools.get(name)
        return bool(tool and tool.available)

    def missing(self, names: tuple[str, ...]) -> list[str]:
        return [name for name in names if not self.available(name)]

    def versions(self) -> dict[str, str]:
        return {name: tool.version for name, tool in sorted(self.tools.items()) if tool.available}

    def to_dict(self) -> dict[str, Any]:
        return {"tools": [self.tools[key].to_dict() for key in sorted(self.tools)]}


def detect_toolchain(
    name: str,
    *,
    search_path: str | None = None,
    extra_dirs: tuple[Path, ...] = (),
) -> Toolchain:
    """Look for *name* on the path (and in *extra_dirs*) and probe its version.

    *extra_dirs* is how a project-local ``node_modules/.bin`` is preferred over
    a global install, which matters because the generated project's
    ``tsconfig.json`` may need a specific compiler version.
    """
    located = ""
    for directory in extra_dirs:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            located = str(candidate)
            break
    if not located:
        found = shutil.which(name, path=search_path)
        if found is None:
            return Toolchain(name=name, error="not found on PATH")
        located = found

    args = _VERSION_ARGS.get(name, ("--version",))
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [located, *args],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT,
            check=False,
        )
    except OSError as exc:
        return Toolchain(name=name, path=located, error=f"could not run: {exc}")
    except subprocess.TimeoutExpired:
        return Toolchain(
            name=name, path=located, error=f"version probe timed out after {VERSION_TIMEOUT}s"
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        return Toolchain(
            name=name,
            path=located,
            error=f"version probe exited {completed.returncode}: {detail[0] if detail else ''}",
        )
    version = (completed.stdout or completed.stderr or "").strip().splitlines()
    return Toolchain(name=name, path=located, version=version[0].strip() if version else "")


def detect_toolchains(
    names: tuple[str, ...],
    *,
    project_root: Path | None = None,
    search_path: str | None = None,
) -> ToolchainIndex:
    """Detect every tool in *names*, preferring a project-local install."""
    extra: tuple[Path, ...] = ()
    if project_root is not None:
        local_bin = project_root / "node_modules" / ".bin"
        if local_bin.is_dir():
            extra = (local_bin,)
    index = ToolchainIndex()
    for name in names:
        index.add(detect_toolchain(name, search_path=search_path, extra_dirs=extra))
    return index
