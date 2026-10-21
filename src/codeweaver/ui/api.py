"""The UI's JSON API.

The API is a thin function layer: every endpoint turns a request payload into a
pipeline call and a JSON-serialisable result, with no logic of its own.  That
keeps the UI and the CLI reporting identical facts.

Two rules are enforced at this boundary:

* every filesystem path in a request is resolved and checked before use, so a
  crafted request cannot read outside the directory it names;
* nothing executes unless the request asks for it, and the response always
  says which execution classes were permitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import codeweaver.adapters  # noqa: F401  (registers the bundled adapters)
from codeweaver.errors import CodeWeaverError
from codeweaver.graph import analyse_impact, search_nodes
from codeweaver.language_registry import registry
from codeweaver.persistence import CheckpointStore
from codeweaver.pipeline import (
    MigrationOptions,
    analyse_project,
    plan_migration,
    run_migration,
)
from codeweaver.project_intake.paths import PathSafetyError, normalise_root, resolve_within
from codeweaver.project_intake.scanner import ScanOptions, scan_project
from codeweaver.project_intake.textio import read_project_text
from codeweaver.validation import ExecutionPolicy, validate_project

__all__ = ["ApiError", "ApiRouter", "MAX_PREVIEW_BYTES"]

#: Largest file the API will return for the diff view.
MAX_PREVIEW_BYTES = 512 * 1024


class ApiError(CodeWeaverError):
    """A request could not be served.  Carries the HTTP status to return."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ApiRouter:
    """Dispatches API requests.

    ``allow_execution`` is the server-wide ceiling: a request cannot enable a
    class of execution the server was not started with, so opening the UI
    cannot become a way to run arbitrary project code.
    """

    allow_tests: bool = False
    allow_install: bool = False

    def handle(self, method: str, path: str, query: dict[str, str], body: dict[str, Any]):
        """Route one request and return its JSON payload."""
        key = (method.upper(), path)
        handler = {
            ("GET", "/api/languages"): self.languages,
            ("GET", "/api/report"): self.report,
            ("GET", "/api/file"): self.file,
            ("POST", "/api/scan"): self.scan,
            ("POST", "/api/analyze"): self.analyze,
            ("POST", "/api/plan"): self.plan,
            ("POST", "/api/migrate"): self.migrate,
            ("POST", "/api/validate"): self.validate,
            ("POST", "/api/impact"): self.impact,
            ("POST", "/api/search"): self.search,
        }.get(key)
        if handler is None:
            raise ApiError(f"no such endpoint: {method} {path}", status=404)
        try:
            return handler(query, body)
        except ApiError:
            # Already carries the status the endpoint chose.
            raise
        except PathSafetyError as exc:
            raise ApiError(str(exc), status=400) from exc
        except CodeWeaverError as exc:
            raise ApiError(str(exc), status=400) from exc

    # -- endpoints ---------------------------------------------------------

    def languages(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {
            "matrix": registry.support_matrix(),
            "execution": {
                "allowTests": self.allow_tests,
                "allowInstall": self.allow_install,
            },
        }

    def scan(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        root = self._project(body)
        result = scan_project(root, self._scan_options(body))
        return result.to_dict()

    def analyze(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        root = self._project(body)
        analysis = analyse_project(
            root,
            source_language=str(body.get("sourceLanguage", "python")),
            options=self._scan_options(body),
        )
        return analysis.to_dict()

    def plan(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        root = self._project(body)
        analysis = analyse_project(
            root,
            source_language=str(body.get("sourceLanguage", "python")),
            options=self._scan_options(body),
        )
        plan = plan_migration(analysis, target=str(body.get("target", "typescript")))
        return plan.to_dict()

    def migrate(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        root = self._project(body)
        output = body.get("outputDir")
        if not output:
            raise ApiError("outputDir is required")
        options = MigrationOptions(
            target=str(body.get("target", "typescript")),
            project_name=str(body.get("projectName", "codeweaver-output")),
            overwrite=bool(body.get("overwrite", False)),
            scan=self._scan_options(body),
            policy=self._policy(body),
            run_validation=bool(body.get("validate", True)),
        )
        outcome = run_migration(
            root,
            Path(str(output)).expanduser(),
            options,
            source_language=str(body.get("sourceLanguage", "python")),
        )
        self._store(root, outcome)
        return {
            "report": outcome.report.to_dict(),
            "plan": outcome.plan.to_dict(),
            "writtenFiles": outcome.written_files,
            "cancelled": outcome.cancelled,
        }

    def validate(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        output = body.get("outputDir")
        if not output:
            raise ApiError("outputDir is required")
        root = normalise_root(str(output))
        report = validate_project(
            root, target=str(body.get("target", "typescript")), policy=self._policy(body)
        )
        return report.to_dict()

    def report(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        project = query.get("project")
        if not project:
            raise ApiError("project is required")
        payload = CheckpointStore.for_project(normalise_root(project)).load_report()
        if payload is None:
            raise ApiError("no stored report for this project", status=404)
        return {"report": payload}

    def file(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        root_name = query.get("root")
        relative = query.get("path")
        if not root_name or not relative:
            raise ApiError("root and path are required")
        root = normalise_root(root_name)
        target = resolve_within(root, relative)
        if not target.is_file():
            raise ApiError(f"not a file: {relative}", status=404)
        size = target.stat().st_size
        if size > MAX_PREVIEW_BYTES:
            raise ApiError(f"file is too large to preview ({size} bytes > {MAX_PREVIEW_BYTES})")
        try:
            contents = read_project_text(target)
        except UnicodeDecodeError as exc:
            raise ApiError("file is not UTF-8 text") from exc
        return {"root": str(root), "path": relative, "contents": contents, "sizeBytes": size}

    def impact(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        origin = body.get("origin")
        if not origin:
            raise ApiError("origin is required")
        analysis = analyse_project(self._project(body))
        return analyse_impact(analysis.graph, str(origin)).to_dict()

    def search(self, query: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        analysis = analyse_project(self._project(body))
        return {"matches": search_nodes(analysis.graph, str(body.get("query", "")))}

    # -- helpers -----------------------------------------------------------

    def _project(self, body: dict[str, Any]) -> Path:
        project = body.get("project")
        if not project:
            raise ApiError("project is required")
        return normalise_root(str(project))

    def _scan_options(self, body: dict[str, Any]) -> ScanOptions:
        defaults = ScanOptions()
        return ScanOptions(
            use_default_ignores=bool(body.get("useDefaultIgnores", True)),
            max_file_bytes=int(body.get("maxFileBytes", defaults.max_file_bytes)),
        )

    def _policy(self, body: dict[str, Any]) -> ExecutionPolicy:
        # A request may narrow what runs but never widen it past the ceiling
        # the server was started with.
        return ExecutionPolicy(
            allow_typecheck=bool(body.get("allowTypecheck", True)),
            allow_tests=self.allow_tests and bool(body.get("allowTests", False)),
            allow_install=self.allow_install and bool(body.get("allowInstall", False)),
        )

    def _store(self, project: Path, outcome) -> None:
        from codeweaver.persistence import Checkpoint

        try:
            store = CheckpointStore.for_project(project)
            store.save_report(outcome.report.to_dict())
            store.save(
                Checkpoint(
                    project_root=str(outcome.analysis.root),
                    stage="migrate",
                    file_hashes={
                        record.path: record.sha256 for record in outcome.analysis.scan.files
                    },
                    target=outcome.report.target_language,
                    source_language=outcome.report.source_language,
                    ir_schema_version=outcome.report.ir_schema_version,
                )
            )
        except (OSError, CodeWeaverError):
            # Storing metadata is a convenience; a failure must not discard a
            # migration the user already ran.
            pass
