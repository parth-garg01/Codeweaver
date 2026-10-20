"""Repository scanning.

The scanner turns a directory into a reviewable list of files.  It never reads
file contents beyond what is needed to classify them, never follows a symlink
out of the project root, and records *why* each skipped path was skipped so the
UI and CLI can show the user the decision instead of a silent omission.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.project_intake.ignore import IgnoreRules, load_ignore_rules
from codeweaver.project_intake.paths import (
    PathSafetyError,
    contains_path,
    normalise_root,
    safe_relative_path,
)

__all__ = [
    "FileRecord",
    "ScanOptions",
    "ScanResult",
    "SkippedPath",
    "scan_project",
]

#: Hard upper bound on a single source file.  Parsers are given bounded input
#: so that a pathological file cannot exhaust memory during a scan.
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024

#: Extensions the scanner recognises as source input.  Recognition is not the
#: same as support: the language registry decides what can actually be parsed.
SOURCE_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
}


@dataclass(frozen=True, slots=True)
class FileRecord:
    """One file accepted by the scan."""

    path: str
    language: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "language": self.language,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class SkippedPath:
    """One path the scan deliberately left out, with the reason."""

    path: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ScanOptions:
    """Knobs for a scan.  Defaults are the safe choice for an unknown repo."""

    use_default_ignores: bool = True
    follow_symlinks: bool = False
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_files: int = 20_000
    include_languages: tuple[str, ...] = ()


@dataclass
class ScanResult:
    """Everything the scan learned about a project root."""

    root: Path
    files: list[FileRecord] = field(default_factory=list)
    skipped: list[SkippedPath] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    ignore_rule_count: int = 0

    @property
    def language_counts(self) -> dict[str, int]:
        return dict(Counter(record.language for record in self.files).most_common())

    @property
    def total_bytes(self) -> int:
        return sum(record.size_bytes for record in self.files)

    def files_for(self, language: str) -> list[FileRecord]:
        return [record for record in self.files if record.language == language]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "files": [record.to_dict() for record in self.files],
            "skipped": [item.to_dict() for item in self.skipped],
            "diagnostics": self.diagnostics.to_list(),
            "languageCounts": self.language_counts,
            "totalBytes": self.total_bytes,
            "ignoreRuleCount": self.ignore_rule_count,
        }


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_project(
    root: Path | str,
    options: ScanOptions | None = None,
    *,
    ignore_rules: IgnoreRules | None = None,
) -> ScanResult:
    """Walk *root* and return the files that may be used as migration input."""
    options = options or ScanOptions()
    resolved_root = normalise_root(root)
    rules = ignore_rules or load_ignore_rules(
        resolved_root, use_defaults=options.use_default_ignores
    )
    result = ScanResult(root=resolved_root, ignore_rule_count=len(rules))

    for dirpath, dirnames, filenames in os.walk(resolved_root, followlinks=False):
        current = Path(dirpath)
        dirnames.sort()
        filenames.sort()
        kept_dirs: list[str] = []
        for name in dirnames:
            child = current / name
            relative = _relative_or_none(resolved_root, child, result)
            if relative is None:
                continue
            if rules.is_ignored(relative, is_dir=True):
                result.skipped.append(
                    SkippedPath(relative, "ignored", _rule_source(rules, relative))
                )
                continue
            if child.is_symlink() and not options.follow_symlinks:
                result.skipped.append(SkippedPath(relative, "symlink", "symlinked directory"))
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in filenames:
            child = current / name
            relative = _relative_or_none(resolved_root, child, result)
            if relative is None:
                continue
            _consider_file(child, relative, rules, options, result)

        if len(result.files) >= options.max_files:
            result.diagnostics.warning(
                "CW0103",
                f"scan stopped after {options.max_files} files",
                stage="scan",
                hint="raise --max-files or narrow the project root",
            )
            break

    return result


def _relative_or_none(root: Path, child: Path, result: ScanResult) -> str | None:
    try:
        return safe_relative_path(root, child)
    except PathSafetyError as exc:
        name = child.name
        result.skipped.append(SkippedPath(name, "escapes-root", str(exc)))
        result.diagnostics.warning(
            "CW0101",
            f"path escapes the project root and was skipped: {name}",
            stage="scan",
            hint="remove the symlink or select a different project root",
        )
        return None


def _rule_source(rules: IgnoreRules, relative: str) -> str:
    decision = rules.decide(relative, is_dir=True) or rules.decide(relative, is_dir=False)
    return f"matched '{decision.pattern}' from {decision.source}" if decision else ""


def _consider_file(
    path: Path,
    relative: str,
    rules: IgnoreRules,
    options: ScanOptions,
    result: ScanResult,
) -> None:
    if rules.is_ignored(relative, is_dir=False):
        result.skipped.append(SkippedPath(relative, "ignored", _rule_source(rules, relative)))
        return
    if path.is_symlink():
        if not options.follow_symlinks:
            result.skipped.append(SkippedPath(relative, "symlink", "symlinked file"))
            return
        if not contains_path(result.root, path):
            result.skipped.append(SkippedPath(relative, "escapes-root", "symlink target"))
            return
    language = SOURCE_EXTENSIONS.get(path.suffix.lower())
    if language is None:
        result.skipped.append(SkippedPath(relative, "unrecognised-extension", path.suffix))
        return
    if options.include_languages and language not in options.include_languages:
        result.skipped.append(SkippedPath(relative, "language-filtered", language))
        return
    try:
        size = path.stat().st_size
    except OSError as exc:
        result.diagnostics.warning(
            "CW0102",
            f"could not stat file: {exc.strerror or exc}",
            stage="scan",
            span=SourceSpan(relative),
        )
        result.skipped.append(SkippedPath(relative, "unreadable", str(exc)))
        return
    if size > options.max_file_bytes:
        result.skipped.append(
            SkippedPath(relative, "too-large", f"{size} bytes > {options.max_file_bytes}")
        )
        result.diagnostics.warning(
            "CW0104",
            f"file exceeds the size limit and was skipped ({size} bytes)",
            stage="scan",
            span=SourceSpan(relative),
            hint="raise --max-file-bytes to include it",
        )
        return
    try:
        digest = _sha256_of(path)
    except OSError as exc:
        result.skipped.append(SkippedPath(relative, "unreadable", str(exc)))
        return
    result.files.append(FileRecord(relative, language, size, digest))
