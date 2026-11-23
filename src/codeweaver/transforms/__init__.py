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

:func:`project_pipeline` runs afterwards, once every module has been through
the default passes, because its passes read the whole project:

4. **infer-expression-types** (statically typed targets only) resolves local,
   attribute, call and element types through the project index.
5. **hoist-block-declarations** lifts declarations that escape their block, and
   runs after inference so a hoisted declaration carries its inferred type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from codeweaver.ir.types import IRType
from codeweaver.transforms.base import (
    PassOutcome,
    PassPipeline,
    PipelineResult,
    TransformPass,
)
from codeweaver.transforms.construction import (
    ClassConstructionPass,
    mark_class_construction,
)
from codeweaver.transforms.fields import InferFieldsPass, infer_instance_fields
from codeweaver.transforms.inference import TypeInferencePass, infer_module_types
from codeweaver.transforms.inheritance import collect_inherited_fields
from codeweaver.transforms.range_loops import RangeLoopPass, lower_range_loops
from codeweaver.transforms.rewrite import count_nodes, rewrite, rewrite_all
from codeweaver.transforms.scoping import HoistDeclarationsPass, hoist_declarations
from codeweaver.transforms.types import TypePropagationPass, propagate_types

if TYPE_CHECKING:
    from codeweaver.graph.project_index import ProjectIndex

__all__ = [
    "ClassConstructionPass",
    "HoistDeclarationsPass",
    "InferFieldsPass",
    "PassOutcome",
    "PassPipeline",
    "PipelineResult",
    "RangeLoopPass",
    "TransformPass",
    "TypeInferencePass",
    "TypePropagationPass",
    "collect_inherited_fields",
    "count_nodes",
    "default_pipeline",
    "default_passes",
    "hoist_declarations",
    "infer_instance_fields",
    "infer_module_types",
    "lower_range_loops",
    "mark_class_construction",
    "project_pipeline",
    "propagate_types",
    "rewrite",
    "rewrite_all",
]


def default_passes(
    inherited_fields: dict[str, dict[str, IRType]] | None = None,
) -> list[TransformPass]:
    """The bundled passes, in the order they must run.

    *inherited_fields* comes from project-wide analysis (see
    :func:`codeweaver.transforms.inheritance.collect_inherited_fields`).  The
    passes work without it; a subclass may then re-declare a base class's field
    and see its inherited field types as unknown.
    """
    fields = inherited_fields or {}
    return [
        InferFieldsPass(inherited_fields=fields),
        RangeLoopPass(),
        TypePropagationPass(inherited_fields=fields),
    ]


def default_pipeline(
    target: str = "",
    *,
    inherited_fields: dict[str, dict[str, IRType]] | None = None,
) -> PassPipeline:
    """A pipeline of the bundled passes for *target*."""
    return PassPipeline(default_passes(inherited_fields), target=target)


def project_pipeline(target: str = "", index: ProjectIndex | None = None) -> PassPipeline:
    """The passes that need every module transformed first.

    *index* is given for targets that need inferred types; without it only the
    scoping pass runs.
    """
    passes: list[TransformPass] = []
    if index is not None:
        passes.append(TypeInferencePass(index))
    passes.append(HoistDeclarationsPass())
    return PassPipeline(passes, target=target)
