"""IR transformation passes and the pipelines that run them.

:func:`default_pipeline` is the order the migration pipeline uses.  The order
matters and is not arbitrary:

1. **infer-instance-fields** first, so the fields it adds carry declared types
   into step 3 rather than being added after propagation has run.
2. **lower-range-loops** next, because it replaces a ``for-each`` with a
   counted loop and the replacement should be in place before anything
   annotates the nodes inside it.
3. **propagate-declared-types** last, so it sees the final tree shape and every
   declaration the earlier passes introduced.
"""

from __future__ import annotations

from codeweaver.transforms.base import (
    PassOutcome,
    PassPipeline,
    PipelineResult,
    TransformPass,
)
from codeweaver.transforms.fields import InferFieldsPass, infer_instance_fields
from codeweaver.transforms.range_loops import RangeLoopPass, lower_range_loops
from codeweaver.transforms.rewrite import count_nodes, rewrite, rewrite_all
from codeweaver.transforms.types import TypePropagationPass, propagate_types

__all__ = [
    "InferFieldsPass",
    "PassOutcome",
    "PassPipeline",
    "PipelineResult",
    "RangeLoopPass",
    "TransformPass",
    "TypePropagationPass",
    "count_nodes",
    "default_pipeline",
    "default_passes",
    "infer_instance_fields",
    "lower_range_loops",
    "propagate_types",
    "rewrite",
    "rewrite_all",
]


def default_passes() -> list[TransformPass]:
    """The bundled passes, in the order they must run."""
    return [InferFieldsPass(), RangeLoopPass(), TypePropagationPass()]


def default_pipeline(target: str = "") -> PassPipeline:
    """A pipeline of the bundled passes for *target*."""
    return PassPipeline(default_passes(), target=target)
