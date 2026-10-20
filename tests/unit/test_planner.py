from __future__ import annotations

import unittest

from codeweaver.frontends.python import parse_source
from codeweaver.graph import build_dependency_graph
from codeweaver.planner import Risk, build_plan, collect_unsupported


def modules_for(sources: dict[str, str]):
    modules = []
    for path, text in sources.items():
        result = parse_source(text, path)
        assert result.module is not None, path
        modules.append(result.module)
    return modules


def plan_for(sources: dict[str, str], **kwargs):
    modules = modules_for(sources)
    graph = build_dependency_graph(modules)
    return build_plan(modules, graph, **kwargs)


LINEAR = {
    "pkg/util.py": "def helper():\n    pass\n",
    "pkg/service.py": "from pkg.util import helper\n\ndef run():\n    helper()\n",
    "pkg/app.py": "from pkg.service import run\n\ndef main():\n    run()\n",
}

CYCLE = {
    "a.py": "from b import g\n\ndef f():\n    g()\n",
    "b.py": "from a import f\n\ndef g():\n    f()\n",
}


class OrderingTests(unittest.TestCase):
    def test_dependencies_come_before_their_users(self) -> None:
        plan = plan_for(LINEAR)
        order = plan.module_order
        self.assertLess(order.index("pkg.util"), order.index("pkg.service"))
        self.assertLess(order.index("pkg.service"), order.index("pkg.app"))

    def test_each_group_depends_only_on_earlier_groups(self) -> None:
        plan = plan_for(LINEAR)
        for group in plan.groups:
            for dependency in group.depends_on:
                self.assertLess(dependency, group.index)

    def test_group_indices_are_contiguous(self) -> None:
        plan = plan_for(LINEAR)
        self.assertEqual([group.index for group in plan.groups], list(range(len(plan.groups))))

    def test_every_module_appears_exactly_once(self) -> None:
        plan = plan_for(LINEAR)
        self.assertEqual(sorted(plan.module_order), ["pkg.app", "pkg.service", "pkg.util"])

    def test_an_empty_module_still_gets_a_group(self) -> None:
        plan = plan_for({"empty.py": ""})
        self.assertEqual(plan.module_order, ["empty"])

    def test_an_empty_project_produces_an_empty_plan(self) -> None:
        plan = build_plan([], build_dependency_graph([]))
        self.assertEqual(plan.groups, [])
        self.assertEqual(plan.highest_risk, Risk.LOW)


class CycleTests(unittest.TestCase):
    def test_a_cycle_becomes_one_group(self) -> None:
        plan = plan_for(CYCLE)
        cycles = plan.cycles
        self.assertEqual(len(cycles), 1)
        self.assertEqual(sorted(cycles[0]), ["a", "b"])

    def test_cycle_group_is_marked_and_at_least_medium_risk(self) -> None:
        group = next(group for group in plan_for(CYCLE).groups if group.is_cycle)
        self.assertTrue(group.is_cycle)
        self.assertGreaterEqual(group.risk.rank, Risk.MEDIUM.rank)
        self.assertTrue(any("cycle" in reason for reason in group.risk_reasons))

    def test_cycle_handling_is_explained(self) -> None:
        self.assertIn("generated together", plan_for(CYCLE).cycle_handling)

    def test_no_cycles_is_stated_explicitly(self) -> None:
        self.assertEqual(plan_for(LINEAR).cycle_handling, "no dependency cycles were found")


class RiskTests(unittest.TestCase):
    def test_clean_module_is_low_risk_with_a_stated_reason(self) -> None:
        plan = plan_for({"a.py": "def f(a: int) -> int:\n    return a\n"})
        group = plan.groups[0]
        self.assertEqual(group.risk, Risk.LOW)
        self.assertIn("no unsupported constructs", group.risk_reasons[0])

    def test_unsupported_constructs_make_a_group_high_risk(self) -> None:
        plan = plan_for({"a.py": "def f():\n    with open('x') as h:\n        pass\n"})
        group = plan.groups[0]
        self.assertEqual(group.risk, Risk.HIGH)
        self.assertEqual(group.unsupported_count, 1)

    def test_external_imports_make_a_group_medium_risk(self) -> None:
        plan = plan_for({"a.py": "import requests\n\ndef f():\n    pass\n"})
        group = plan.groups[0]
        self.assertEqual(group.risk, Risk.MEDIUM)
        self.assertEqual(group.unresolved_import_count, 1)

    def test_highest_risk_is_reported_for_the_plan(self) -> None:
        plan = plan_for(
            {
                "a.py": "def f(a: int) -> int:\n    return a\n",
                "b.py": "def g():\n    with open('x') as h:\n        pass\n",
            }
        )
        self.assertEqual(plan.highest_risk, Risk.HIGH)


class UnsupportedCollectionTests(unittest.TestCase):
    def test_occurrences_are_grouped_by_construct(self) -> None:
        modules = modules_for(
            {
                "a.py": "def f():\n    with open('x') as h:\n        pass\n",
                "b.py": "def g():\n    with open('y') as h:\n        pass\n    global z\n",
            }
        )
        entries = {entry.source_kind: entry for entry in collect_unsupported(modules)}
        self.assertEqual(entries["With"].count, 2)
        self.assertEqual(entries["Global"].count, 1)

    def test_locations_are_recorded(self) -> None:
        modules = modules_for({"a.py": "def f():\n    with open('x') as h:\n        pass\n"})
        entry = collect_unsupported(modules)[0]
        self.assertEqual(entry.locations, ("a.py:2:5",))

    def test_hint_is_carried_through(self) -> None:
        modules = modules_for({"a.py": "def f():\n    with open('x') as h:\n        pass\n"})
        self.assertIn("try/finally", collect_unsupported(modules)[0].hint)

    def test_entries_are_sorted_by_construct(self) -> None:
        modules = modules_for({"a.py": "global z\nwith open('x') as h:\n    pass\n"})
        kinds = [entry.source_kind for entry in collect_unsupported(modules)]
        self.assertEqual(kinds, sorted(kinds))

    def test_clean_project_has_no_entries(self) -> None:
        self.assertEqual(collect_unsupported(modules_for({"a.py": "x = 1\n"})), [])


class PlanMetadataTests(unittest.TestCase):
    def test_languages_and_toolchains_are_carried(self) -> None:
        plan = plan_for(
            LINEAR,
            source_language="python",
            target_language="typescript",
            support_level="partial",
            required_toolchains=("tsc",),
            missing_toolchains=("tsc",),
            known_limitations=("decorators are not applied",),
        )
        self.assertEqual(plan.source_language, "python")
        self.assertEqual(plan.required_toolchains, ("tsc",))
        self.assertEqual(plan.missing_toolchains, ("tsc",))
        self.assertEqual(plan.known_limitations, ("decorators are not applied",))

    def test_unresolved_imports_are_listed_once(self) -> None:
        plan = plan_for(
            {
                "a.py": "import requests\n",
                "b.py": "import requests\nimport os\n",
            }
        )
        self.assertEqual(plan.unresolved_imports, ["os", "requests"])

    def test_to_dict_is_json_friendly_and_complete(self) -> None:
        import json

        payload = plan_for(CYCLE).to_dict()
        for key in (
            "groups",
            "moduleOrder",
            "cycles",
            "cycleHandling",
            "unsupported",
            "unresolvedImports",
            "requiredToolchains",
            "highestRisk",
        ):
            self.assertIn(key, payload, key)
        json.dumps(payload)

    def test_plan_is_deterministic(self) -> None:
        self.assertEqual(plan_for(LINEAR).to_dict(), plan_for(LINEAR).to_dict())


if __name__ == "__main__":
    unittest.main()
