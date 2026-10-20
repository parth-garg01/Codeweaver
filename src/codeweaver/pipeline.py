"""The migration pipeline.

This is the one place that knows the order of the stages, so the CLI and the UI
cannot drift apart: both call the same functions and get the same records.

Three safety rules are enforced here rather than left to callers:

* output never lands inside the source project unless the user explicitly
  passes a path that does, and even then the source files themselves are never
  rewritten;
* an existing non-empty output directory is refused unless overwriting was
  asked for;
* generation is cancellable, and a cancelled run leaves whatever it had already
  written in place rather than deleting a half-finished tree.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Importing the adapters module is what populates the language registry; the
# pipeline is the lowest layer that needs a populated registry, so it owns the
# import rather than relying on a caller having done it.
import codeweaver.adapters  # noqa: F401
from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.errors import CodeWeaverError, UnsupportedPairError
from codeweaver.graph import DependencyGraph, build_dependency_graph, build_symbol_index
from codeweaver.ir import IR_SCHEMA_VERSION
from codeweaver.ir.declarations import IRModule
from codeweaver.ir.schema import validate_module
from codeweaver.language_registry import registry
from codeweaver.planner import MigrationPlan, build_plan
from codeweaver.project_intake.manifests import Manifest, detect_manifests
from codeweaver.project_intake.paths import normalise_root
from codeweaver.project_intake.scanner import ScanOptions, ScanResult, scan_project
from codeweaver.reporting import FileCounts, MigrationReport
from codeweaver.transforms import default_pipeline
from codeweaver.transforms.construction import ClassConstructionPass
from codeweaver.transforms.inheritance import collect_inherited_fields
from codeweaver.validation import ExecutionPolicy, detect_toolchains, validate_project

__all__ = [
    "AnalysisResult",
    "MigrationOptions",
    "MigrationOutcome",
    "OutputDirectoryError",
    "analyse_project",
    "plan_migration",
    "run_migration",
]

#: Called with (stage, current, total, detail) as work progresses.
ProgressCallback = Callable[[str, int, int, str], None]

#: Called between units of work; returning ``True`` cancels the run.
CancelCallback = Callable[[], bool]


class OutputDirectoryError(CodeWeaverError):
    """The requested output directory cannot be written to safely."""


@dataclass(frozen=True, slots=True)
class MigrationOptions:
    """Everything the caller can decide about one run."""

    target: str = "typescript"
    project_name: str = "codeweaver-output"
    overwrite: bool = False
    scan: ScanOptions = field(default_factory=ScanOptions)
    policy: ExecutionPolicy = field(default_factory=ExecutionPolicy)
    run_validation: bool = True


@dataclass
class AnalysisResult:
    """What ``analyze`` learned: IR modules, symbols, graph and diagnostics."""

    root: Path
    scan: ScanResult
    manifests: list[Manifest] = field(default_factory=list)
    modules: list[IRModule] = field(default_factory=list)
    graph: DependencyGraph = field(default_factory=DependencyGraph)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    parse_failures: list[str] = field(default_factory=list)
    source_language: str = ""

    @property
    def counts(self) -> FileCounts:
        return FileCounts(
            discovered=len(self.scan.files),
            parsed=len(self.modules),
            skipped=len(self.scan.skipped),
            failed=len(self.parse_failures),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "sourceLanguage": self.source_language,
            "scan": self.scan.to_dict(),
            "manifests": [item.to_dict() for item in self.manifests],
            "modules": [
                {
                    "name": module.name,
                    "path": module.path,
                    "functions": len(module.functions),
                    "classes": len(module.classes),
                    "unsupported": len(module.unsupported_nodes()),
                }
                for module in self.modules
            ],
            "graph": self.graph.to_dict(),
            "symbols": len(self.graph.symbols),
            "diagnostics": self.diagnostics.to_list(),
            "parseFailures": list(self.parse_failures),
            "counts": self.counts.to_dict(),
        }


@dataclass
class MigrationOutcome:
    """The result of a full run."""

    report: MigrationReport
    analysis: AnalysisResult
    plan: MigrationPlan
    written_files: list[str] = field(default_factory=list)
    cancelled: bool = False


def analyse_project(
    root: Path | str,
    *,
    source_language: str = "python",
    options: ScanOptions | None = None,
    progress: ProgressCallback | None = None,
) -> AnalysisResult:
    """Scan *root*, parse every supported file and build the project model."""
    resolved = normalise_root(root)
    scan_options = options or ScanOptions()
    scan = scan_project(resolved, scan_options)
    diagnostics = DiagnosticBag()
    diagnostics.extend(list(scan.diagnostics))
    result = AnalysisResult(
        root=resolved,
        scan=scan,
        manifests=detect_manifests(resolved, diagnostics),
        diagnostics=diagnostics,
        source_language=source_language,
    )

    frontend = registry.frontends.get(source_language)
    if frontend is None:
        diagnostics.error(
            "CW0800",
            f"no frontend is registered for source language {source_language!r}",
            stage="analyze",
        )
        return result

    candidates = scan.files_for(source_language)
    total = len(candidates)
    for index, record in enumerate(candidates, start=1):
        if progress is not None:
            progress("analyze", index, total, record.path)
        try:
            text = (resolved / record.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            diagnostics.error(
                "CW0801",
                f"could not read source file: {exc}",
                stage="analyze",
                span=SourceSpan(record.path),
            )
            result.parse_failures.append(record.path)
            continue
        parsed = frontend.parse_module(text, record.path)
        diagnostics.extend(list(parsed.diagnostics))
        if parsed.module is None:
            result.parse_failures.append(record.path)
            continue
        schema_issues = validate_module(parsed.module)
        if schema_issues.has_errors:
            diagnostics.extend(schema_issues.errors)
            result.parse_failures.append(record.path)
            continue
        result.modules.append(parsed.module)

    result.modules.sort(key=lambda module: module.name)
    symbols = build_symbol_index(result.modules)
    result.graph = build_dependency_graph(result.modules, symbols=symbols, diagnostics=diagnostics)
    return result


def plan_migration(analysis: AnalysisResult, *, target: str = "typescript") -> MigrationPlan:
    """Build the migration plan for an analysed project."""
    frontend, backend, pair = registry.resolve(analysis.source_language, target)
    toolchains = detect_toolchains(backend.required_toolchains)
    return build_plan(
        analysis.modules,
        analysis.graph,
        source_language=analysis.source_language,
        target_language=target,
        support_level=pair.level.value,
        required_toolchains=backend.required_toolchains,
        missing_toolchains=tuple(toolchains.missing(backend.required_toolchains)),
        known_limitations=tuple(frontend.known_limitations) + tuple(backend.known_limitations),
    )


def prepare_output_dir(
    output_dir: Path | str, source_root: Path, *, overwrite: bool = False
) -> Path:
    """Validate and create the output directory.

    Refuses to write into the source project, and refuses a non-empty
    destination unless *overwrite* was requested.  Nothing is deleted even
    then: existing files are replaced individually as they are written, so an
    unrelated file in the directory survives.
    """
    output = Path(output_dir).expanduser()
    resolved_source = source_root.resolve()
    candidate = output.resolve() if output.exists() else output.absolute()
    if candidate == resolved_source or resolved_source in candidate.parents:
        raise OutputDirectoryError(
            f"output directory {candidate} is inside the source project "
            f"{resolved_source}; choose a directory outside it"
        )
    if candidate.exists():
        if not candidate.is_dir():
            raise OutputDirectoryError(f"output path is not a directory: {candidate}")
        if any(candidate.iterdir()) and not overwrite:
            raise OutputDirectoryError(
                f"output directory {candidate} is not empty; pass overwrite to replace files in it"
            )
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def run_migration(
    root: Path | str,
    output_dir: Path | str,
    options: MigrationOptions | None = None,
    *,
    source_language: str = "python",
    progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> MigrationOutcome:
    """Run the whole pipeline and return the report."""
    options = options or MigrationOptions()
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")

    analysis = analyse_project(
        root, source_language=source_language, options=options.scan, progress=progress
    )
    resolved_root = analysis.root

    try:
        frontend, backend, pair = registry.resolve(source_language, options.target)
    except UnsupportedPairError as exc:
        analysis.diagnostics.error("CW0802", str(exc), stage="plan")
        report = _report_for(analysis, None, started_at, time.monotonic() - started, options)
        return MigrationOutcome(report=report, analysis=analysis, plan=MigrationPlan())

    plan = plan_migration(analysis, target=options.target)
    output = prepare_output_dir(output_dir, resolved_root, overwrite=options.overwrite)

    transformed: list[IRModule] = []
    applied: list[str] = []
    skipped: list[str] = []
    inherited_fields = collect_inherited_fields(analysis.modules)
    for module in analysis.modules:
        # The construction pass needs project-wide knowledge, so it is built
        # per module from the symbol index rather than bundled with the
        # module-local default passes.
        pipeline = default_pipeline(options.target, inherited_fields=inherited_fields)
        pipeline.passes.insert(0, ClassConstructionPass.for_module(module, analysis.graph.symbols))
        result = pipeline.run(module, analysis.diagnostics)
        transformed.append(result.module)
        for name in result.applied_passes:
            if name not in applied:
                applied.append(name)
        for name in result.skipped_passes:
            if name not in skipped:
                skipped.append(name)
    # A pass that had work to do in any module counts as applied; listing it as
    # both applied and skipped would tell the reader nothing.
    skipped = [name for name in skipped if name not in applied]

    written: list[str] = []
    cancelled = False
    mappings: dict[str, list[dict[str, Any]]] = {}
    ordered = _ordered_modules(transformed, plan)
    total = len(ordered)
    from codeweaver.backends.typescript import ModuleLayout

    layout = ModuleLayout.for_modules(transformed)
    for index, module in enumerate(ordered, start=1):
        if should_cancel is not None and should_cancel():
            cancelled = True
            analysis.diagnostics.warning(
                "CW0803",
                f"migration was cancelled after {len(written)} file(s)",
                stage="migrate",
                hint="already generated files were left in place",
            )
            break
        if progress is not None:
            progress("migrate", index, total, module.name)
        generated = backend.generate_module(module, layout, diagnostics=analysis.diagnostics)
        target_path = output / generated.path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(generated.contents, encoding="utf-8")
        written.append(generated.path)
        mappings[generated.path] = generated.mappings

    if not cancelled:
        from codeweaver.backends.typescript import project_files

        for relative, contents in project_files(options.project_name).items():
            (output / relative).write_text(contents, encoding="utf-8")
            written.append(relative)

    report = _report_for(
        analysis,
        plan,
        started_at,
        time.monotonic() - started,
        options,
        output=output,
        written=written,
        applied=applied,
        skipped=skipped,
        support_level=pair.level.value,
        supported_constructs=list(frontend.supported_constructs),
        mappings=mappings,
        cancelled=cancelled,
    )
    return MigrationOutcome(
        report=report, analysis=analysis, plan=plan, written_files=written, cancelled=cancelled
    )


def _ordered_modules(modules: list[IRModule], plan: MigrationPlan) -> list[IRModule]:
    by_name = {module.name: module for module in modules}
    ordered = [by_name[name] for name in plan.module_order if name in by_name]
    remaining = [module for module in modules if module.name not in plan.module_order]
    return [*ordered, *remaining]


def _report_for(
    analysis: AnalysisResult,
    plan: MigrationPlan | None,
    started_at: str,
    duration: float,
    options: MigrationOptions,
    *,
    output: Path | None = None,
    written: list[str] | None = None,
    applied: list[str] | None = None,
    skipped: list[str] | None = None,
    support_level: str = "",
    supported_constructs: list[str] | None = None,
    mappings: dict[str, list[dict[str, Any]]] | None = None,
    cancelled: bool = False,
) -> MigrationReport:
    counts = analysis.counts
    counts.translated = len([path for path in (written or []) if path.endswith(".ts")])
    report = MigrationReport(
        source_language=analysis.source_language,
        target_language=options.target,
        support_level=support_level or (plan.support_level if plan else ""),
        source_root=str(analysis.root),
        output_dir=str(output) if output is not None else "",
        started_at=started_at,
        duration_seconds=duration,
        ir_schema_version=IR_SCHEMA_VERSION,
        counts=counts,
        generated_files=sorted(written or []),
        supported_constructs=supported_constructs or [],
        unsupported=plan.unsupported if plan else [],
        unresolved_imports=plan.unresolved_imports if plan else [],
        dependency_cycles=plan.cycles if plan else [],
        cycle_handling=plan.cycle_handling if plan else "",
        applied_passes=applied or [],
        skipped_passes=skipped or [],
        diagnostics=list(analysis.diagnostics),
    )
    if output is not None and options.run_validation and not cancelled:
        report.validation = validate_project(
            output, target=options.target, policy=options.policy, mappings=mappings or {}
        )
        report.tool_versions = dict(report.validation.tool_versions)
    return report
