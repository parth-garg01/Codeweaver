"""Local persistence for migration metadata and checkpoints.

What is stored and what is not is a deliberate choice:

* **stored**: the project root, the file list with each file's SHA-256, the
  stage reached, the plan summary and the last report. All small, all
  derivable, all useful for resuming.
* **not stored**: source code, generated code, symbol names or any other
  content from the project. A checkpoint is metadata about a migration, not a
  copy of the user's work.

A checkpoint records the hash of every input file.  Resuming compares those
hashes against the files on disk, so a checkpoint whose inputs have changed is
reported as stale rather than being resumed against the wrong source.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codeweaver.errors import CodeWeaverError

__all__ = [
    "STORE_SCHEMA_VERSION",
    "Checkpoint",
    "CheckpointStore",
    "StaleCheckpointError",
    "default_store_dir",
]

#: Version of the on-disk record format.  Bump when a field changes meaning.
STORE_SCHEMA_VERSION = "1.0.0"

#: Directory name used inside the store root.
_STORE_DIRNAME = ".codeweaver"


class StaleCheckpointError(CodeWeaverError):
    """A checkpoint no longer matches the project it was taken from."""


def default_store_dir(project_root: Path | str) -> Path:
    """Where checkpoints live by default.

    Beside the project, not inside the user's home, so that deleting a checkout
    takes its migration metadata with it.  The directory is in CodeWeaver's
    default ignore list, so a later scan never treats it as source.
    """
    override = os.environ.get("CODEWEAVER_STORE_DIR")
    if override:
        return Path(override).expanduser()
    return Path(project_root).expanduser().resolve() / _STORE_DIRNAME


@dataclass
class Checkpoint:
    """One saved point in a migration."""

    project_root: str
    stage: str
    #: relative path -> sha256 of the file when the checkpoint was taken.
    file_hashes: dict[str, str] = field(default_factory=dict)
    target: str = ""
    source_language: str = ""
    ir_schema_version: str = ""
    created_at: str = ""
    #: Small, content-free summaries. Never source or generated code.
    plan_summary: dict[str, Any] = field(default_factory=dict)
    report_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "storeSchemaVersion": STORE_SCHEMA_VERSION,
            "projectRoot": self.project_root,
            "stage": self.stage,
            "fileHashes": dict(sorted(self.file_hashes.items())),
            "target": self.target,
            "sourceLanguage": self.source_language,
            "irSchemaVersion": self.ir_schema_version,
            "createdAt": self.created_at,
            "planSummary": self.plan_summary,
            "reportSummary": self.report_summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        found = str(data.get("storeSchemaVersion", ""))
        if found != STORE_SCHEMA_VERSION:
            raise StaleCheckpointError(
                f"checkpoint uses store schema {found or '<missing>'}, "
                f"this build reads {STORE_SCHEMA_VERSION}; re-run the analysis"
            )
        return cls(
            project_root=str(data.get("projectRoot", "")),
            stage=str(data.get("stage", "")),
            file_hashes={str(k): str(v) for k, v in (data.get("fileHashes") or {}).items()},
            target=str(data.get("target", "")),
            source_language=str(data.get("sourceLanguage", "")),
            ir_schema_version=str(data.get("irSchemaVersion", "")),
            created_at=str(data.get("createdAt", "")),
            plan_summary=dict(data.get("planSummary") or {}),
            report_summary=dict(data.get("reportSummary") or {}),
        )

    def changed_files(self, current: dict[str, str]) -> dict[str, str]:
        """Which inputs differ from when the checkpoint was taken.

        Returns ``path -> reason`` for added, removed and modified files.
        """
        differences: dict[str, str] = {}
        for path, digest in sorted(self.file_hashes.items()):
            if path not in current:
                differences[path] = "removed"
            elif current[path] != digest:
                differences[path] = "modified"
        for path in sorted(current):
            if path not in self.file_hashes:
                differences[path] = "added"
        return differences

    def is_current_for(self, current: dict[str, str]) -> bool:
        return not self.changed_files(current)


@dataclass
class CheckpointStore:
    """Reads and writes checkpoints under a store directory."""

    directory: Path

    @classmethod
    def for_project(cls, project_root: Path | str) -> CheckpointStore:
        return cls(default_store_dir(project_root))

    @property
    def checkpoint_path(self) -> Path:
        return self.directory / "checkpoint.json"

    @property
    def report_path(self) -> Path:
        return self.directory / "last-report.json"

    def exists(self) -> bool:
        return self.checkpoint_path.is_file()

    def save(self, checkpoint: Checkpoint) -> Path:
        """Write *checkpoint* atomically."""
        if not checkpoint.created_at:
            checkpoint.created_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            self.checkpoint_path,
            json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True) + "\n",
        )
        return self.checkpoint_path

    def load(self) -> Checkpoint | None:
        """Read the checkpoint, or ``None`` when there is none."""
        if not self.exists():
            return None
        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StaleCheckpointError(f"checkpoint is unreadable: {exc}") from exc
        if not isinstance(payload, dict):
            raise StaleCheckpointError("checkpoint is not a JSON object")
        return Checkpoint.from_dict(payload)

    def save_report(self, report: dict[str, Any]) -> Path:
        """Store the last report as JSON so ``codeweaver report`` can show it."""
        self.directory.mkdir(parents=True, exist_ok=True)
        _write_atomic(self.report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
        return self.report_path

    def load_report(self) -> dict[str, Any] | None:
        if not self.report_path.is_file():
            return None
        try:
            payload = json.loads(self.report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def clear(self) -> list[str]:
        """Delete stored metadata.  Returns the paths that were removed.

        Only files CodeWeaver wrote are removed, and the directory itself goes
        only if it ends up empty — so an unrelated file placed there survives.
        """
        removed: list[str] = []
        for path in (self.checkpoint_path, self.report_path):
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        if self.directory.is_dir() and not any(self.directory.iterdir()):
            self.directory.rmdir()
        return removed


def _write_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* via a temporary file and a rename.

    An interrupted write must not leave a truncated checkpoint behind, because
    a truncated checkpoint is harder to recover from than a missing one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp", text=True)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
