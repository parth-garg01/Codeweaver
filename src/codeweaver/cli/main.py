"""The CodeWeaver command line interface.

The CLI is a thin layer over :mod:`codeweaver.pipeline`: it parses arguments,
prints, and chooses an exit code.  Everything it reports comes from the same
records the UI uses, so the two cannot disagree.

Exit codes:

* ``0`` — the command succeeded (for ``migrate``: passed, or passed with
  warnings);
* ``1`` — the command failed, or a validation check failed;
* ``2`` — the command was used incorrectly, or the environment prevents it;
* ``3`` — the migration is incomplete: unsupported constructs were left in the
  output, or no validation check actually ran.

The distinction between ``0`` and ``3`` is the point: a migration that produced
files but verified nothing is not a success.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import codeweaver.adapters  # noqa: F401  (registers the bundled adapters)
from codeweaver import __version__
from codeweaver.errors import CodeWeaverError
from codeweaver.language_registry import registry
from codeweaver.persistence import Checkpoint, CheckpointStore, StaleCheckpointError
from codeweaver.pipeline import (
    MigrationOptions,
    analyse_project,
    plan_migration,
    run_migration,
)
from codeweaver.project_intake.scanner import ScanOptions, scan_project
from codeweaver.reporting import MigrationReport, render_json, render_markdown, render_text
from codeweaver.validation import ExecutionPolicy, validate_project

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INCOMPLETE = 3


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Kept separate from :func:`main` so that the help text can be tested and
    rendered without running anything.
    """
    parser = argparse.ArgumentParser(
        prog="codeweaver",
        description=(
            "Local-first, LLM-free code migration through a shared intermediate "
            "representation. Nothing is uploaded and no model is called."
        ),
        epilog="Exit codes: 0 ok, 1 failed, 2 usage error, 3 incomplete (unverified).",
    )
    parser.add_argument("--version", action="version", version=f"codeweaver {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    def with_project(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("project", type=Path, help="path to the local source project")
        sub.add_argument(
            "--from",
            dest="source_language",
            default="python",
            help="source language (default: python)",
        )
        sub.add_argument(
            "--max-file-bytes",
            type=int,
            default=ScanOptions().max_file_bytes,
            help="skip source files larger than this many bytes",
        )
        sub.add_argument(
            "--no-default-ignores",
            action="store_true",
            help="do not apply the built-in ignore rules",
        )
        sub.add_argument(
            "--json", action="store_true", help="print machine-readable JSON instead of text"
        )
        return sub

    with_project("scan", "Inspect a repository and show the files that would be used.")
    with_project("analyze", "Parse the source and build the project model.")

    plan_parser = with_project("plan", "Produce a migration plan for review.")
    plan_parser.add_argument(
        "--to", dest="target", default="typescript", help="target language (default: typescript)"
    )

    migrate_parser = with_project("migrate", "Generate the target project.")
    migrate_parser.add_argument(
        "--to", dest="target", default="typescript", help="target language (default: typescript)"
    )
    migrate_parser.add_argument(
        "--out", type=Path, required=True, help="output directory (must be outside the project)"
    )
    migrate_parser.add_argument(
        "--name", default="codeweaver-output", help="name for the generated project manifest"
    )
    migrate_parser.add_argument(
        "--overwrite", action="store_true", help="replace files in a non-empty output directory"
    )
    migrate_parser.add_argument(
        "--no-validate", action="store_true", help="generate without running any checks"
    )
    migrate_parser.add_argument(
        "--allow-tests",
        action="store_true",
        help="run the migrated tests; this executes generated code",
    )
    migrate_parser.add_argument(
        "--allow-install",
        action="store_true",
        help="install target dependencies; this needs the network",
    )
    migrate_parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="report format (default: text)",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="Run checks on an existing generated project.",
        description="Run checks on an existing generated project.",
    )
    validate_parser.add_argument("output", type=Path, help="the generated project directory")
    validate_parser.add_argument(
        "--to", dest="target", default="typescript", help="target language (default: typescript)"
    )
    validate_parser.add_argument(
        "--allow-tests", action="store_true", help="run tests; this executes generated code"
    )
    validate_parser.add_argument(
        "--allow-install", action="store_true", help="install dependencies; needs the network"
    )
    validate_parser.add_argument("--json", action="store_true", help="print JSON")

    report_parser = subparsers.add_parser(
        "report",
        help="Show or export the last migration report.",
        description="Show or export the last migration report stored for a project.",
    )
    report_parser.add_argument("project", type=Path, help="the source project")
    report_parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="report format (default: text)",
    )
    report_parser.add_argument("--out", type=Path, help="write the report to this file")

    languages_parser = subparsers.add_parser(
        "languages",
        help="List supported languages and source/target pairs.",
        description="List the language pairs this build actually implements.",
    )
    languages_parser.add_argument("--json", action="store_true", help="print JSON")

    ui_parser = subparsers.add_parser(
        "ui",
        help="Serve the local web UI.",
        description=(
            "Serve the local web UI on the loopback interface. The UI exposes the "
            "local filesystem to the browser page it serves, so it refuses to bind "
            "to a routable address."
        ),
    )
    ui_parser.add_argument("--port", type=int, default=8731, help="port (default: 8731)")
    ui_parser.add_argument(
        "--host", default="127.0.0.1", help="loopback address to bind (default: 127.0.0.1)"
    )
    ui_parser.add_argument(
        "--allow-tests",
        action="store_true",
        help="let the UI run migrated tests; this executes generated code",
    )
    ui_parser.add_argument(
        "--allow-install",
        action="store_true",
        help="let the UI install target dependencies; this needs the network",
    )

    clear_parser = subparsers.add_parser(
        "clear",
        help="Delete locally stored migration metadata for a project.",
        description="Delete the checkpoint and stored report for a project.",
    )
    clear_parser.add_argument("project", type=Path, help="the source project")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point.  Returns the process exit code rather than exiting."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE
    handler = {
        "scan": _cmd_scan,
        "analyze": _cmd_analyze,
        "plan": _cmd_plan,
        "migrate": _cmd_migrate,
        "validate": _cmd_validate,
        "report": _cmd_report,
        "languages": _cmd_languages,
        "clear": _cmd_clear,
        "ui": _cmd_ui,
    }[args.command]
    try:
        return handler(args)
    except CodeWeaverError as exc:
        print(f"codeweaver: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("codeweaver: interrupted", file=sys.stderr)
        return EXIT_USAGE


def _scan_options(args: argparse.Namespace) -> ScanOptions:
    return ScanOptions(
        use_default_ignores=not args.no_default_ignores,
        max_file_bytes=args.max_file_bytes,
    )


def _policy(args: argparse.Namespace) -> ExecutionPolicy:
    return ExecutionPolicy(
        allow_typecheck=True,
        allow_tests=getattr(args, "allow_tests", False),
        allow_install=getattr(args, "allow_install", False),
    )


def _cmd_scan(args: argparse.Namespace) -> int:
    result = scan_project(args.project, _scan_options(args))
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK
    print(f"Project: {result.root}")
    print(f"Ignore rules applied: {result.ignore_rule_count}")
    print(f"Files: {len(result.files)}  ({result.total_bytes} bytes)")
    for language, count in result.language_counts.items():
        print(f"  {language}: {count}")
    for record in result.files:
        print(f"    {record.path}")
    if result.skipped:
        print(f"Skipped: {len(result.skipped)}")
        for item in result.skipped[:25]:
            detail = f" - {item.detail}" if item.detail else ""
            print(f"    {item.path} [{item.reason}]{detail}")
        if len(result.skipped) > 25:
            print(f"    ... and {len(result.skipped) - 25} more")
    for diagnostic in result.diagnostics:
        print(f"  {diagnostic.format()}")
    return EXIT_OK


def _cmd_analyze(args: argparse.Namespace) -> int:
    analysis = analyse_project(
        args.project, source_language=args.source_language, options=_scan_options(args)
    )
    if args.json:
        print(json.dumps(analysis.to_dict(), indent=2, sort_keys=True))
        return EXIT_FAILED if analysis.diagnostics.has_errors else EXIT_OK
    counts = analysis.counts
    print(f"Project: {analysis.root}")
    print(
        f"Files: discovered {counts.discovered}, parsed {counts.parsed}, "
        f"skipped {counts.skipped}, failed {counts.failed}"
    )
    print(f"Symbols: {len(analysis.graph.symbols)}")
    print(f"Graph: {len(analysis.graph.nodes)} nodes, {len(analysis.graph.edges)} edges")
    for manifest in analysis.manifests:
        print(
            f"Manifest {manifest.path}: {manifest.project_name or '<unnamed>'} "
            f"({len(manifest.dependencies)} dependency/ies)"
        )
    for module in analysis.modules:
        gaps = len(module.unsupported_nodes())
        suffix = f", {gaps} unsupported" if gaps else ""
        print(
            f"  {module.name} ({module.path}): {len(module.functions)} function(s), "
            f"{len(module.classes)} class(es){suffix}"
        )
    for path in analysis.parse_failures:
        print(f"  FAILED {path}")
    errors = analysis.diagnostics.errors
    warnings = analysis.diagnostics.warnings
    if errors or warnings:
        print(f"Diagnostics: {len(errors)} error(s), {len(warnings)} warning(s)")
        for diagnostic in list(analysis.diagnostics)[:40]:
            print(f"  {diagnostic.format()}")
    return EXIT_FAILED if errors else EXIT_OK


def _cmd_plan(args: argparse.Namespace) -> int:
    analysis = analyse_project(
        args.project, source_language=args.source_language, options=_scan_options(args)
    )
    plan = plan_migration(analysis, target=args.target)
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK
    print(f"Plan: {plan.source_language} -> {plan.target_language} (support: {plan.support_level})")
    print(f"Highest risk: {plan.highest_risk.value}")
    print(f"Groups: {len(plan.groups)}")
    for group in plan.groups:
        marker = " [cycle]" if group.is_cycle else ""
        depends = (
            f" after {', '.join(str(index) for index in group.depends_on)}"
            if group.depends_on
            else ""
        )
        print(f"  {group.index}. {', '.join(group.modules)}{marker}{depends}")
        print(f"     risk {group.risk.value}: {'; '.join(group.risk_reasons)}")
    if plan.unsupported:
        print(f"Unsupported constructs: {plan.unsupported_total} occurrence(s)")
        for entry in plan.unsupported:
            print(f"  {entry.source_kind} x{entry.count}: {entry.reason}")
            for location in entry.locations[:5]:
                print(f"    at {location}")
    if plan.unresolved_imports:
        print("Unresolved imports:")
        for name in plan.unresolved_imports:
            print(f"  {name}")
    print(f"Required toolchains: {', '.join(plan.required_toolchains) or 'none'}")
    if plan.missing_toolchains:
        print(f"MISSING toolchains: {', '.join(plan.missing_toolchains)}")
        print("  validation will be skipped and the result reported as unverified")
    print("Review this plan before running 'codeweaver migrate'.")
    return EXIT_OK


def _cmd_migrate(args: argparse.Namespace) -> int:
    options = MigrationOptions(
        target=args.target,
        project_name=args.name,
        overwrite=args.overwrite,
        scan=_scan_options(args),
        policy=_policy(args),
        run_validation=not args.no_validate,
    )
    outcome = run_migration(args.project, args.out, options, source_language=args.source_language)
    report = outcome.report
    _store_report(args.project, outcome)
    print(_render(report, args.format), end="")
    if outcome.cancelled:
        return EXIT_INCOMPLETE
    return report.status.exit_code


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_project(args.output, target=args.target, policy=_policy(args))
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"Validating {report.output_dir}")
        for name, version in sorted(report.tool_versions.items()):
            print(f"  {name}: {version}")
        for check in report.checks:
            print(f"  [{check.outcome.value}] {check.name}")
            if check.command:
                print(f"      $ {check.command_line}")
            if check.reason:
                print(f"      {check.reason}")
            for diagnostic in check.diagnostics[:40]:
                print(f"      {diagnostic.format()}")
        summary = report.summary()
        print(
            f"  {summary['passed']} passed, {summary['failed']} failed, "
            f"{summary['skipped']} skipped, {summary['errored']} errored"
        )
        if not report.ran_any_check:
            print("  no check actually ran: the output is unverified")
    if report.has_failures:
        return EXIT_FAILED
    return EXIT_OK if report.ran_any_check else EXIT_INCOMPLETE


def _cmd_report(args: argparse.Namespace) -> int:
    store = CheckpointStore.for_project(args.project)
    payload = store.load_report()
    if payload is None:
        print(
            f"codeweaver: no stored report for {args.project}; run 'codeweaver migrate' first",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.format == "json":
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    else:
        text = _render_stored(payload, args.format)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Report written to {args.out}")
    else:
        print(text, end="")
    status = str(payload.get("status", ""))
    return int(payload.get("exitCode", EXIT_OK)) if status else EXIT_OK


def _cmd_languages(args: argparse.Namespace) -> int:
    matrix = registry.support_matrix()
    if args.json:
        print(json.dumps(matrix, indent=2, sort_keys=True))
        return EXIT_OK
    print("Source languages")
    for entry in matrix["sources"]:
        print(f"  {entry['language']} ({entry['displayName']}): {', '.join(entry['extensions'])}")
        for limitation in entry["knownLimitations"]:
            print(f"    limitation: {limitation}")
    print("Target languages")
    for entry in matrix["targets"]:
        toolchains = ", ".join(entry["requiredToolchains"]) or "none"
        print(f"  {entry['language']} ({entry['displayName']}): needs {toolchains}")
        for limitation in entry["knownLimitations"]:
            print(f"    limitation: {limitation}")
    print("Pairs")
    for pair in matrix["pairs"]:
        state = "available" if pair["runnable"] else "unavailable"
        print(f"  {pair['source']} -> {pair['target']}: {pair['level']} ({state})")
        if pair["notes"]:
            print(f"    {pair['notes']}")
    if not matrix["pairs"]:
        print("  none")
    return EXIT_OK


def _cmd_ui(args: argparse.Namespace) -> int:
    from codeweaver.ui import serve

    try:
        serve(
            host=args.host,
            port=args.port,
            allow_tests=args.allow_tests,
            allow_install=args.allow_install,
        )
    except ValueError as exc:
        print(f"codeweaver: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"codeweaver: could not start the UI: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


def _cmd_clear(args: argparse.Namespace) -> int:
    store = CheckpointStore.for_project(args.project)
    removed = store.clear()
    if not removed:
        print(f"Nothing stored for {args.project}")
        return EXIT_OK
    for path in removed:
        print(f"Removed {path}")
    return EXIT_OK


def _render(report: MigrationReport, fmt: str) -> str:
    if fmt == "json":
        return render_json(report) + "\n"
    if fmt == "markdown":
        return render_markdown(report)
    return render_text(report, use_colour=sys.stdout.isatty())


def _render_stored(payload: dict, fmt: str) -> str:
    """Render a stored report payload without rebuilding the whole object.

    A stored report is data, so it is printed as its own summary rather than
    being reconstructed into a :class:`MigrationReport` — which would risk the
    printout disagreeing with what was actually recorded.
    """
    lines = [
        f"Stored report: {payload.get('status', 'unknown')}",
        str(payload.get("statusReason", "")),
        "",
        f"  {payload.get('sourceLanguage', '?')} -> {payload.get('targetLanguage', '?')}",
        f"  source: {payload.get('sourceRoot', '')}",
        f"  output: {payload.get('outputDir', '')}",
        f"  started: {payload.get('startedAt', '')}",
    ]
    counts = payload.get("counts") or {}
    if counts:
        lines.append(
            "  files: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))
        )
    for entry in payload.get("unsupported") or []:
        lines.append(f"  unsupported {entry.get('sourceKind')} x{entry.get('count')}")
    for check in (payload.get("validation") or {}).get("checks") or []:
        lines.append(f"  check {check.get('name')}: {check.get('outcome')}")
    if fmt == "markdown":
        body = "\n".join(f"- {line}" for line in lines if line)
        return (
            f"# Stored CodeWeaver report\n\n{body}\n\n{payload.get('equivalenceStatement', '')}\n"
        )
    lines.append("")
    lines.append(str(payload.get("equivalenceStatement", "")))
    return "\n".join(lines) + "\n"


def _store_report(project: Path, outcome) -> None:
    """Persist the report and a checkpoint, without failing the command."""
    try:
        store = CheckpointStore.for_project(project)
        store.save_report(outcome.report.to_dict())
        store.save(
            Checkpoint(
                project_root=str(outcome.analysis.root),
                stage="migrate",
                file_hashes={record.path: record.sha256 for record in outcome.analysis.scan.files},
                target=outcome.report.target_language,
                source_language=outcome.report.source_language,
                ir_schema_version=outcome.report.ir_schema_version,
                plan_summary={
                    "groups": len(outcome.plan.groups),
                    "cycles": len(outcome.plan.cycles),
                    "unsupportedTotal": outcome.plan.unsupported_total,
                },
                report_summary={
                    "status": outcome.report.status.value,
                    "generatedFiles": len(outcome.report.generated_files),
                },
            )
        )
    except (OSError, StaleCheckpointError) as exc:
        print(f"codeweaver: could not store migration metadata: {exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
