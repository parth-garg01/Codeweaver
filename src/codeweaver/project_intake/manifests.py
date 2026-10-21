"""Project manifest detection.

CodeWeaver reads manifests for two reasons: to name the project, and to learn
which third-party packages the source relies on.  A declared dependency that
has no mapping in the target ecosystem is a migration risk the user must see,
so detection is deliberately conservative — an unparseable manifest is reported
rather than guessed at.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.project_intake.textio import read_project_text

__all__ = [
    "MANIFEST_FILENAMES",
    "Manifest",
    "detect_manifests",
]

#: Manifest filename -> ecosystem.
MANIFEST_FILENAMES: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "package.json": "node",
    "tsconfig.json": "node",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java",
    "Gemfile": "ruby",
}

_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass
class Manifest:
    """A detected manifest and what could be read from it."""

    path: str
    ecosystem: str
    project_name: str = ""
    version: str = ""
    dependencies: list[str] = field(default_factory=list)
    parsed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ecosystem": self.ecosystem,
            "projectName": self.project_name,
            "version": self.version,
            "dependencies": sorted(self.dependencies),
            "parsed": self.parsed,
        }


def detect_manifests(root: Path, diagnostics: DiagnosticBag | None = None) -> list[Manifest]:
    """Return manifests found directly under *root*, in a stable order."""
    bag = diagnostics if diagnostics is not None else DiagnosticBag()
    manifests: list[Manifest] = []
    for filename in sorted(MANIFEST_FILENAMES):
        path = root / filename
        if not path.is_file():
            continue
        manifests.append(_read_manifest(path, MANIFEST_FILENAMES[filename], bag))
    return manifests


def _read_manifest(path: Path, ecosystem: str, bag: DiagnosticBag) -> Manifest:
    manifest = Manifest(path=path.name, ecosystem=ecosystem)
    reader = {
        "pyproject.toml": _read_pyproject,
        "requirements.txt": _read_requirements,
        "package.json": _read_package_json,
        "go.mod": _read_go_mod,
        "Cargo.toml": _read_cargo_toml,
    }.get(path.name)
    if reader is None:
        return manifest
    try:
        reader(path, manifest)
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        manifest.parsed = False
        bag.warning(
            "CW0201",
            f"could not read manifest {path.name}: {exc}",
            stage="intake",
            span=SourceSpan(path.name),
            hint="dependency risks from this manifest will not be reported",
        )
    return manifest


def _read_pyproject(path: Path, manifest: Manifest) -> None:
    data = tomllib.loads(read_project_text(path))
    project = data.get("project") or {}
    manifest.project_name = str(project.get("name", ""))
    manifest.version = str(project.get("version", ""))
    for requirement in project.get("dependencies") or []:
        name = _requirement_name(str(requirement))
        if name:
            manifest.dependencies.append(name)
    poetry = ((data.get("tool") or {}).get("poetry")) or {}
    manifest.project_name = manifest.project_name or str(poetry.get("name", ""))
    manifest.version = manifest.version or str(poetry.get("version", ""))
    for name in poetry.get("dependencies") or {}:
        if name != "python":
            manifest.dependencies.append(name)


def _read_requirements(path: Path, manifest: Manifest) -> None:
    for line in read_project_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        name = _requirement_name(stripped)
        if name:
            manifest.dependencies.append(name)


def _read_package_json(path: Path, manifest: Manifest) -> None:
    data = json.loads(read_project_text(path))
    if not isinstance(data, dict):
        raise ValueError("package.json must contain an object")
    manifest.project_name = str(data.get("name", ""))
    manifest.version = str(data.get("version", ""))
    for section in ("dependencies", "peerDependencies"):
        manifest.dependencies.extend((data.get(section) or {}).keys())


def _read_go_mod(path: Path, manifest: Manifest) -> None:
    for line in read_project_text(path).splitlines():
        stripped = line.strip()
        if stripped.startswith("module "):
            manifest.project_name = stripped.removeprefix("module ").strip()
        elif stripped.startswith("require "):
            parts = stripped.removeprefix("require ").split()
            if parts:
                manifest.dependencies.append(parts[0])


def _read_cargo_toml(path: Path, manifest: Manifest) -> None:
    data = tomllib.loads(read_project_text(path))
    package = data.get("package") or {}
    manifest.project_name = str(package.get("name", ""))
    manifest.version = str(package.get("version", ""))
    manifest.dependencies.extend((data.get("dependencies") or {}).keys())


def _requirement_name(requirement: str) -> str:
    match = _REQUIREMENT_NAME.match(requirement)
    return match.group(1) if match else ""
