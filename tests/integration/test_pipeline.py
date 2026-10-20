"""End-to-end pipeline tests over the committed sample project."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeweaver.pipeline import (
    MigrationOptions,
    OutputDirectoryError,
    analyse_project,
    plan_migration,
    prepare_output_dir,
    run_migration,
)
from codeweaver.validation import ExecutionPolicy

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FIXTURE = FIXTURES / "sample_project"
UNSUPPORTED = FIXTURES / "unsupported_project"
NO_VALIDATION = MigrationOptions(run_validation=False)


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis = analyse_project(FIXTURE)

    def test_every_supported_file_is_parsed(self) -> None:
        self.assertEqual(self.analysis.counts.parsed, 5)
        self.assertEqual(self.analysis.parse_failures, [])

    def test_module_names_are_derived_from_paths(self) -> None:
        names = sorted(module.name for module in self.analysis.modules)
        self.assertEqual(
            names,
            ["pkg", "pkg.cycle_a", "pkg.cycle_b", "pkg.ledger", "pkg.models"],
        )

    def test_ignored_directory_is_not_parsed(self) -> None:
        paths = [record.path for record in self.analysis.scan.files]
        self.assertNotIn("scratch/notes.py", paths)

    def test_pycache_is_pruned_even_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "m.py").write_text("x = 1\n", encoding="utf-8")
            cache = root / "pkg" / "__pycache__"
            cache.mkdir()
            (cache / "m.cpython-311.pyc").write_bytes(b"\x00")
            analysis = analyse_project(root)
            self.assertEqual([m.name for m in analysis.modules], ["pkg.m"])

    def test_manifest_is_detected(self) -> None:
        manifest = self.analysis.manifests[0]
        self.assertEqual(manifest.project_name, "sample-project")
        self.assertEqual(manifest.dependencies, ["requests"])

    def test_graph_records_the_import_cycle(self) -> None:
        adjacency = self.analysis.graph.module_adjacency()
        self.assertIn("pkg.cycle_b", adjacency["pkg.cycle_a"])
        self.assertIn("pkg.cycle_a", adjacency["pkg.cycle_b"])

    def test_an_external_import_is_recorded_as_unresolved(self) -> None:
        analysis = analyse_project(UNSUPPORTED)
        labels = [node.label for node in analysis.graph.external_nodes()]
        self.assertIn("os", labels)

    def test_symbols_are_indexed(self) -> None:
        self.assertIn("pkg.models.Account.deposit", self.analysis.graph.symbols)

    def test_to_dict_is_json_friendly(self) -> None:
        import json

        json.dumps(self.analysis.to_dict())


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = plan_migration(analyse_project(FIXTURE))

    def test_models_is_planned_before_ledger(self) -> None:
        order = self.plan.module_order
        self.assertLess(order.index("pkg.models"), order.index("pkg.ledger"))

    def test_cycle_is_one_group(self) -> None:
        self.assertEqual(
            [sorted(cycle) for cycle in self.plan.cycles], [["pkg.cycle_a", "pkg.cycle_b"]]
        )

    def test_a_clean_project_plans_no_unsupported_constructs(self) -> None:
        self.assertEqual(self.plan.unsupported, [])

    def test_unsupported_constructs_are_listed_when_present(self) -> None:
        plan = plan_migration(analyse_project(UNSUPPORTED))
        kinds = {entry.source_kind for entry in plan.unsupported}
        self.assertIn("With", kinds)

    def test_required_toolchain_is_declared(self) -> None:
        self.assertEqual(self.plan.required_toolchains, ("tsc",))

    def test_known_limitations_come_from_both_adapters(self) -> None:
        joined = " ".join(self.plan.known_limitations)
        self.assertIn("context managers", joined)
        self.assertIn("keyword arguments", joined)


class OutputDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_refuses_to_write_inside_the_source_project(self) -> None:
        with self.assertRaises(OutputDirectoryError) as ctx:
            prepare_output_dir(FIXTURE / "out", FIXTURE)
        self.assertIn("inside the source project", str(ctx.exception))

    def test_refuses_the_source_root_itself(self) -> None:
        with self.assertRaises(OutputDirectoryError):
            prepare_output_dir(FIXTURE, FIXTURE)

    def test_refuses_a_non_empty_directory_without_overwrite(self) -> None:
        out = self.base / "out"
        out.mkdir()
        (out / "existing.ts").write_text("x", encoding="utf-8")
        with self.assertRaises(OutputDirectoryError) as ctx:
            prepare_output_dir(out, FIXTURE)
        self.assertIn("not empty", str(ctx.exception))

    def test_accepts_a_non_empty_directory_with_overwrite(self) -> None:
        out = self.base / "out"
        out.mkdir()
        (out / "existing.ts").write_text("x", encoding="utf-8")
        self.assertEqual(prepare_output_dir(out, FIXTURE, overwrite=True), out.resolve())

    def test_overwrite_does_not_delete_unrelated_files(self) -> None:
        out = self.base / "out"
        out.mkdir()
        keep = out / "keep.md"
        keep.write_text("mine", encoding="utf-8")
        prepare_output_dir(out, FIXTURE, overwrite=True)
        self.assertEqual(keep.read_text(encoding="utf-8"), "mine")

    def test_refuses_a_file_as_the_output_path(self) -> None:
        target = self.base / "file.txt"
        target.write_text("x", encoding="utf-8")
        with self.assertRaises(OutputDirectoryError):
            prepare_output_dir(target, FIXTURE)

    def test_creates_a_missing_directory(self) -> None:
        out = self.base / "nested" / "out"
        self.assertTrue(prepare_output_dir(out, FIXTURE).is_dir())


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def migrate(self, options: MigrationOptions = NO_VALIDATION):
        return run_migration(FIXTURE, self.out, options)

    def test_source_project_is_never_modified(self) -> None:
        before = {path: path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
        self.migrate()
        after = {path: path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_every_module_produces_a_file(self) -> None:
        outcome = self.migrate()
        generated = {path for path in outcome.written_files if path.endswith(".ts")}
        self.assertEqual(
            generated,
            {
                "pkg/index.ts",
                "pkg/cycle_a.ts",
                "pkg/cycle_b.ts",
                "pkg/ledger.ts",
                "pkg/models.ts",
            },
        )

    def test_project_manifests_are_written(self) -> None:
        self.migrate()
        self.assertTrue((self.out / "tsconfig.json").is_file())
        self.assertTrue((self.out / "package.json").is_file())

    def test_relative_imports_resolve_between_generated_files(self) -> None:
        self.migrate()
        ledger = (self.out / "pkg" / "ledger.ts").read_text(encoding="utf-8")
        self.assertIn('from "./models"', ledger)

    def test_unsupported_constructs_are_marked_in_the_output(self) -> None:
        run_migration(UNSUPPORTED, self.out, NO_VALIDATION)
        contents = (self.out / "pkg" / "unsupported.ts").read_text(encoding="utf-8")
        self.assertIn("CODEWEAVER-UNSUPPORTED", contents)

    def test_report_is_incomplete_when_constructs_are_unsupported(self) -> None:
        report = run_migration(UNSUPPORTED, self.out, NO_VALIDATION).report
        self.assertEqual(report.status.value, "incomplete")
        self.assertGreater(report.unsupported_total, 0)

    def test_report_records_counts_and_schema_version(self) -> None:
        report = self.migrate().report
        self.assertEqual(report.counts.discovered, 5)
        self.assertEqual(report.counts.parsed, 5)
        self.assertEqual(report.counts.translated, 5)
        self.assertTrue(report.ir_schema_version)

    def test_report_always_carries_the_equivalence_statement(self) -> None:
        self.assertIn(
            "does not verify behavioural equivalence",
            self.migrate().report.equivalence_statement,
        )

    def test_transform_passes_are_recorded(self) -> None:
        report = self.migrate().report
        self.assertIn("infer-instance-fields", report.applied_passes)

    def test_generation_is_deterministic(self) -> None:
        first = self.migrate()
        second_dir = Path(self._tmp.name) / "out2"
        run_migration(FIXTURE, second_dir, NO_VALIDATION)
        for path in first.written_files:
            self.assertEqual(
                (self.out / path).read_text(encoding="utf-8"),
                (second_dir / path).read_text(encoding="utf-8"),
                path,
            )

    def test_cancellation_keeps_what_was_already_written(self) -> None:
        state = {"calls": 0}

        def should_cancel() -> bool:
            state["calls"] += 1
            return state["calls"] > 2

        outcome = run_migration(FIXTURE, self.out, NO_VALIDATION, should_cancel=should_cancel)
        self.assertTrue(outcome.cancelled)
        self.assertTrue(outcome.written_files)
        self.assertLess(len(outcome.written_files), 5)
        for path in outcome.written_files:
            self.assertTrue((self.out / path).is_file())

    def test_cancelled_run_does_not_write_manifests_or_validate(self) -> None:
        outcome = run_migration(FIXTURE, self.out, MigrationOptions(), should_cancel=lambda: True)
        self.assertTrue(outcome.cancelled)
        self.assertEqual(outcome.written_files, [])
        self.assertEqual(outcome.report.validation.checks, [])

    def test_progress_is_reported_for_each_stage(self) -> None:
        stages: list[str] = []
        run_migration(
            FIXTURE,
            self.out,
            NO_VALIDATION,
            progress=lambda stage, current, total, detail: stages.append(stage),
        )
        self.assertIn("analyze", stages)
        self.assertIn("migrate", stages)

    def test_read_only_policy_reports_an_unverified_migration(self) -> None:
        options = MigrationOptions(policy=ExecutionPolicy.read_only())
        report = run_migration(FIXTURE, self.out, options).report
        self.assertFalse(report.validation.ran_any_check)
        self.assertEqual(report.status.value, "incomplete")

    def test_an_unavailable_target_is_reported_not_crashed(self) -> None:
        outcome = run_migration(FIXTURE, self.out, MigrationOptions(target="rust"))
        self.assertEqual(outcome.report.status.value, "failed")
        self.assertTrue(any(item.code == "CW0802" for item in outcome.report.diagnostics))

    def test_report_renders_in_every_format(self) -> None:
        from codeweaver.reporting import render_json, render_markdown, render_text

        report = self.migrate().report
        for rendered in (render_text(report), render_markdown(report), render_json(report)):
            self.assertIn("python", rendered)

    def test_a_pass_applied_to_any_module_is_not_also_listed_as_skipped(self) -> None:
        report = self.migrate().report
        self.assertEqual(set(report.applied_passes) & set(report.skipped_passes), set())


if __name__ == "__main__":
    unittest.main()


class ClassConstructionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_constructing_a_project_class_uses_new(self) -> None:
        source = Path(self._tmp.name) / "src"
        (source / "pkg").mkdir(parents=True)
        (source / "pkg" / "models.py").write_text(
            "class Account:\n"
            "    def __init__(self, owner: str) -> None:\n"
            "        self.owner = owner\n",
            encoding="utf-8",
        )
        (source / "pkg" / "factory.py").write_text(
            "from pkg.models import Account\n"
            "\n"
            "\n"
            "def make(owner: str) -> Account:\n"
            "    return Account(owner)\n",
            encoding="utf-8",
        )
        run_migration(source, self.out, NO_VALIDATION)
        contents = (self.out / "pkg" / "factory.ts").read_text(encoding="utf-8")
        self.assertIn("return new Account(owner);", contents)
