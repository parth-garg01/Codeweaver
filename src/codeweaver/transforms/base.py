"""The transform pass framework.

A pass declares what it needs and what it produces, and returns a *new* module
rather than mutating the one it was given.  That is what makes a pipeline
re-runnable: running it twice over the same IR produces the same IR, so a
resumed migration does not drift from a fresh one.

Passes report through diagnostics rather than raising.  A pass that cannot run
(its preconditions are not met) is recorded as skipped with the reason, which
then appears in the migration report instead of silently doing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.ir.declarations import IRModule

__all__ = ["PassOutcome", "PassPipeline", "PipelineResult", "TransformPass"]


@runtime_checkable
class TransformPass(Protocol):
    """One deterministic IR-to-IR rewrite."""

    name: str
    description: str
    #: Target languages this pass is for; empty means "any target".
    targets: tuple[str, ...]

    def applies_to(self, module: IRModule) -> bool:
        """Cheap check for whether this pass has anything to do."""
        ...

    def run(self, module: IRModule, bag: DiagnosticBag) -> IRModule:
        """Return a rewritten module.  Must not mutate *module*."""
        ...


@dataclass
class PassOutcome:
    """What one pass did to one module."""

    name: str
    applied: bool
    skipped_reason: str = ""
    changed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "applied": self.applied,
            "skippedReason": self.skipped_reason,
            "changed": self.changed,
        }


@dataclass
class PipelineResult:
    """The rewritten module plus a record of every pass that ran."""

    module: IRModule
    outcomes: list[PassOutcome] = field(default_factory=list)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)

    @property
    def applied_passes(self) -> list[str]:
        return [outcome.name for outcome in self.outcomes if outcome.applied]

    @property
    def skipped_passes(self) -> list[str]:
        return [outcome.name for outcome in self.outcomes if not outcome.applied]

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module.name,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "diagnostics": self.diagnostics.to_list(),
        }


@dataclass
class PassPipeline:
    """An ordered list of passes applied to a module."""

    passes: list[TransformPass] = field(default_factory=list)
    target: str = ""

    def add(self, transform: TransformPass) -> PassPipeline:
        self.passes.append(transform)
        return self

    def names(self) -> list[str]:
        return [transform.name for transform in self.passes]

    def run(self, module: IRModule, bag: DiagnosticBag | None = None) -> PipelineResult:
        """Apply every applicable pass in order."""
        diagnostics = bag if bag is not None else DiagnosticBag()
        result = PipelineResult(module=module, diagnostics=diagnostics)
        current = module
        for transform in self.passes:
            targets = getattr(transform, "targets", ())
            if targets and self.target and self.target not in targets:
                result.outcomes.append(
                    PassOutcome(
                        transform.name,
                        applied=False,
                        skipped_reason=f"pass targets {', '.join(targets)}",
                    )
                )
                continue
            if not transform.applies_to(current):
                result.outcomes.append(
                    PassOutcome(transform.name, applied=False, skipped_reason="nothing to rewrite")
                )
                continue
            rewritten = transform.run(current, diagnostics)
            result.outcomes.append(
                PassOutcome(transform.name, applied=True, changed=rewritten is not current)
            )
            current = rewritten
        result.module = current
        return result
