"""The migration plan.

The plan is what the user reviews *before* anything is generated.  It answers
four questions:

* in what order will modules be migrated, and which ones must be done together
  because they form a cycle;
* what will not translate, and where;
* what the target toolchain needs in order to validate the result;
* how risky each group is, so attention goes to the right place first.

Risk is assigned from facts already in the IR and the graph — unsupported
constructs, unresolved imports, cycle membership — never from a guess about
code the tool did not understand.
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from codeweaver.diagnostics import SourceSpan
from codeweaver.graph.analysis import topological_groups
from codeweaver.graph.dependency import DependencyGraph, EdgeKind
from codeweaver.ir.base import IRUnsupported, walk
from codeweaver.ir.declarations import IRModule
from codeweaver.reporting.report import UnsupportedEntry

__all__ = ["MigrationPlan", "PlannedGroup", "Risk", "build_plan"]


class Risk(enum.StrEnum):
    """How much manual review a group is likely to need."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return {"low": 1, "medium": 2, "high": 3}[self.value]


@dataclass
class PlannedGroup:
    """One migration step: a set of modules generated together."""

    index: int
    modules: tuple[str, ...]
    depends_on: tuple[int, ...] = ()
    is_cycle: bool = False
    unsupported_count: int = 0
    unresolved_import_count: int = 0
    risk: Risk = Risk.LOW
    risk_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "modules": list(self.modules),
            "dependsOn": list(self.depends_on),
            "isCycle": self.is_cycle,
            "unsupportedCount": self.unsupported_count,
            "unresolvedImportCount": self.unresolved_import_count,
            "risk": self.risk.value,
            "riskReasons": list(self.risk_reasons),
        }


@dataclass
class MigrationPlan:
    """The ordered plan for one migration."""

    source_language: str = ""
    target_language: str = ""
    support_level: str = ""
    groups: list[PlannedGroup] = field(default_factory=list)
    unsupported: list[UnsupportedEntry] = field(default_factory=list)
    unresolved_imports: list[str] = field(default_factory=list)
    required_toolchains: tuple[str, ...] = ()
    missing_toolchains: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()

    @property
    def module_order(self) -> list[str]:
        """Every module in migration order."""
        return [module for group in self.groups for module in group.modules]

    @property
    def cycles(self) -> list[list[str]]:
        return [list(group.modules) for group in self.groups if group.is_cycle]

    @property
    def unsupported_total(self) -> int:
        return sum(entry.count for entry in self.unsupported)

    @property
    def highest_risk(self) -> Risk:
        return max(
            (group.risk for group in self.groups), key=lambda risk: risk.rank, default=Risk.LOW
        )

    @property
    def cycle_handling(self) -> str:
        if not self.cycles:
            return "no dependency cycles were found"
        return (
            f"{len(self.cycles)} cycle(s) are migrated as single groups; modules "
            "inside a cycle are generated together because no ordering of them "
            "alone is correct"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sourceLanguage": self.source_language,
            "targetLanguage": self.target_language,
            "supportLevel": self.support_level,
            "groups": [group.to_dict() for group in self.groups],
            "moduleOrder": self.module_order,
            "cycles": self.cycles,
            "cycleHandling": self.cycle_handling,
            "unsupported": [entry.to_dict() for entry in self.unsupported],
            "unsupportedTotal": self.unsupported_total,
            "unresolvedImports": list(self.unresolved_imports),
            "requiredToolchains": list(self.required_toolchains),
            "missingToolchains": list(self.missing_toolchains),
            "knownLimitations": list(self.known_limitations),
            "highestRisk": self.highest_risk.value,
        }


def collect_unsupported(modules: list[IRModule]) -> list[UnsupportedEntry]:
    """Group every unsupported node across *modules* by construct."""
    by_kind: dict[str, list[IRUnsupported]] = {}
    for module in modules:
        for node in walk(module):
            if isinstance(node, IRUnsupported):
                by_kind.setdefault(node.source_kind, []).append(node)
    entries: list[UnsupportedEntry] = []
    for kind in sorted(by_kind):
        nodes = by_kind[kind]
        reasons = Counter(node.reason for node in nodes)
        hints = [node.hint for node in nodes if node.hint]
        locations = tuple(
            str(node.span) for node in nodes if isinstance(node.span, SourceSpan) and node.span
        )
        entries.append(
            UnsupportedEntry(
                source_kind=kind,
                reason=reasons.most_common(1)[0][0],
                count=len(nodes),
                locations=locations,
                hint=hints[0] if hints else "",
            )
        )
    return entries


def _assess(
    modules: tuple[str, ...],
    *,
    is_cycle: bool,
    unsupported: int,
    unresolved: int,
) -> tuple[Risk, tuple[str, ...]]:
    reasons: list[str] = []
    risk = Risk.LOW
    if unsupported:
        risk = Risk.HIGH
        reasons.append(f"{unsupported} unsupported construct(s) will be left as markers")
    if is_cycle:
        risk = Risk.HIGH if risk is Risk.HIGH else Risk.MEDIUM
        reasons.append(f"{len(modules)} modules form a dependency cycle")
    if unresolved:
        risk = Risk.HIGH if risk is Risk.HIGH else Risk.MEDIUM
        reasons.append(f"{unresolved} import(s) are not project modules")
    if not reasons:
        reasons.append("no unsupported constructs, cycles or unresolved imports")
    return risk, tuple(reasons)


def build_plan(
    modules: list[IRModule],
    graph: DependencyGraph,
    *,
    source_language: str = "",
    target_language: str = "",
    support_level: str = "",
    required_toolchains: tuple[str, ...] = (),
    missing_toolchains: tuple[str, ...] = (),
    known_limitations: tuple[str, ...] = (),
) -> MigrationPlan:
    """Build the plan for migrating *modules*, ordered by the dependency graph."""
    by_name = {module.name: module for module in modules}
    unsupported_per_module = {
        name: sum(1 for node in walk(module) if isinstance(node, IRUnsupported))
        for name, module in by_name.items()
    }
    unresolved_per_module: Counter[str] = Counter()
    unresolved_names: list[str] = []
    for edge in graph.edges:
        if edge.resolved or edge.kind is not EdgeKind.IMPORT:
            continue
        unresolved_per_module[graph.module_of(edge.source)] += 1
        label = graph.nodes[edge.target].label if edge.target in graph.nodes else edge.target
        if label not in unresolved_names:
            unresolved_names.append(label)

    groups: list[PlannedGroup] = []
    for group in topological_groups(graph.module_adjacency()):
        members = tuple(name for name in group.members if name in by_name)
        if not members:
            continue
        unsupported = sum(unsupported_per_module.get(name, 0) for name in members)
        unresolved = sum(unresolved_per_module.get(name, 0) for name in members)
        risk, reasons = _assess(
            members, is_cycle=group.is_cycle, unsupported=unsupported, unresolved=unresolved
        )
        groups.append(
            PlannedGroup(
                index=len(groups),
                modules=members,
                depends_on=group.depends_on,
                is_cycle=group.is_cycle,
                unsupported_count=unsupported,
                unresolved_import_count=unresolved,
                risk=risk,
                risk_reasons=reasons,
            )
        )

    planned = {name for group in groups for name in group.modules}
    for name in sorted(by_name):
        if name in planned:
            continue
        # A module with no graph node at all (for example an empty file) still
        # has to be generated, so it gets its own group rather than vanishing.
        unsupported = unsupported_per_module.get(name, 0)
        risk, reasons = _assess((name,), is_cycle=False, unsupported=unsupported, unresolved=0)
        groups.append(
            PlannedGroup(
                index=len(groups),
                modules=(name,),
                unsupported_count=unsupported,
                risk=risk,
                risk_reasons=reasons,
            )
        )

    return MigrationPlan(
        source_language=source_language,
        target_language=target_language,
        support_level=support_level,
        groups=groups,
        unsupported=collect_unsupported(modules),
        unresolved_imports=sorted(unresolved_names),
        required_toolchains=required_toolchains,
        missing_toolchains=missing_toolchains,
        known_limitations=known_limitations,
    )
