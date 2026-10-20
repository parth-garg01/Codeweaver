"""Structural validation of IR trees.

Serialisation guarantees that a document can be read back; it says nothing
about whether the tree makes sense.  This module answers the second question:
are required slots filled, are names unique, are expressions where expressions
belong, and does the tree contain constructs nobody mapped?

Validation returns diagnostics instead of raising, because an invalid tree is
normally the *frontend's* report about difficult source rather than a crash:
the planner needs to see every problem at once.  :func:`assert_valid` is
available for the places that genuinely cannot continue.
"""

from __future__ import annotations

from collections import Counter

from codeweaver.diagnostics import DiagnosticBag, SourceSpan
from codeweaver.errors import IRSchemaError
from codeweaver.ir.base import IRNode, IRUnsupported, registered_kinds, walk
from codeweaver.ir.declarations import IRClass, IRFunction, IRImport, IRModule
from codeweaver.ir.expressions import IRExpression, IRParam
from codeweaver.ir.statements import IRBlock, IRStatement, IRTry

__all__ = [
    "VAR_KEYWORD",
    "VAR_POSITIONAL",
    "assert_valid",
    "validate_module",
    "validate_node",
]

VAR_POSITIONAL = "var_positional"
VAR_KEYWORD = "var_keyword"

_PARAM_STYLES = frozenset({"positional", "keyword", VAR_POSITIONAL, VAR_KEYWORD})


def validate_module(module: IRModule, bag: DiagnosticBag | None = None) -> DiagnosticBag:
    """Validate *module* and everything inside it."""
    diagnostics = bag if bag is not None else DiagnosticBag()
    if not isinstance(module, IRModule):
        diagnostics.error(
            "CW0301",
            f"expected an IR module, got {type(module).__name__}",
            stage="ir-validate",
        )
        return diagnostics

    if not module.name:
        diagnostics.error("CW0302", "module has no name", stage="ir-validate")
    if not module.path:
        diagnostics.warning(
            "CW0303",
            f"module {module.name!r} has no source path",
            stage="ir-validate",
            hint="diagnostics for this module cannot link back to a file",
        )

    _check_duplicates(
        module.declared_names,
        bag=diagnostics,
        code="CW0304",
        what=f"top-level name in module {module.name!r}",
        span=SourceSpan(module.path),
    )

    for node in walk(module):
        validate_node(node, diagnostics, module_path=module.path)
    return diagnostics


def validate_node(
    node: IRNode, bag: DiagnosticBag | None = None, *, module_path: str = ""
) -> DiagnosticBag:
    """Validate a single node's own invariants (not its descendants')."""
    diagnostics = bag if bag is not None else DiagnosticBag()
    span = node.span or (SourceSpan(module_path) if module_path else None)

    if node.kind not in registered_kinds():
        diagnostics.error(
            "CW0305",
            f"node kind {node.kind!r} is not registered in the IR schema",
            stage="ir-validate",
            span=span,
        )

    if isinstance(node, IRUnsupported):
        # Not an error: the tree is valid and the gap is reported.  The
        # migration report decides how loudly to present it.
        diagnostics.warning(
            "CW0300",
            f"unsupported construct {node.source_kind!r}: {node.reason}",
            stage="ir-validate",
            span=span,
            hint=node.hint,
        )
    elif isinstance(node, IRFunction):
        _validate_function(node, diagnostics, span)
    elif isinstance(node, IRClass):
        _validate_class(node, diagnostics, span)
    elif isinstance(node, IRImport):
        _validate_import(node, diagnostics, span)
    elif isinstance(node, IRBlock):
        _validate_block(node, diagnostics, span)
    elif isinstance(node, IRTry):
        _validate_try(node, diagnostics, span)

    _validate_expression_slots(node, diagnostics, span)
    return diagnostics


def assert_valid(module: IRModule) -> None:
    """Raise :class:`IRSchemaError` when *module* has validation errors."""
    diagnostics = validate_module(module)
    if diagnostics.has_errors:
        joined = "; ".join(item.format() for item in diagnostics.errors)
        raise IRSchemaError(f"invalid IR for module {module.name!r}: {joined}")


def _validate_function(node: IRFunction, bag: DiagnosticBag, span: SourceSpan | None) -> None:
    if not node.name:
        bag.error("CW0306", "function has no name", stage="ir-validate", span=span)
    _check_duplicates(
        [param.name for param in node.params],
        bag=bag,
        code="CW0307",
        what=f"parameter of {node.name!r}",
        span=span,
    )
    seen_var_positional = False
    seen_var_keyword = False
    for param in node.params:
        if param.style not in _PARAM_STYLES:
            bag.error(
                "CW0308",
                f"parameter {param.name!r} of {node.name!r} has unknown style {param.style!r}",
                stage="ir-validate",
                span=param.span or span,
            )
        if seen_var_keyword:
            bag.error(
                "CW0309",
                f"parameter {param.name!r} of {node.name!r} follows a **kwargs parameter",
                stage="ir-validate",
                span=param.span or span,
            )
        if param.style == VAR_POSITIONAL:
            if seen_var_positional:
                bag.error(
                    "CW0310",
                    f"{node.name!r} declares more than one *args parameter",
                    stage="ir-validate",
                    span=param.span or span,
                )
            seen_var_positional = True
        elif param.style == VAR_KEYWORD:
            seen_var_keyword = True
    if node.is_constructor and not node.is_method:
        bag.error(
            "CW0311",
            f"{node.name!r} is marked as a constructor but not as a method",
            stage="ir-validate",
            span=span,
        )
    if node.is_abstract and not node.body.is_empty:
        bag.warning(
            "CW0312",
            f"abstract function {node.name!r} has a body",
            stage="ir-validate",
            span=span,
            hint="the body will not be emitted by backends that elide abstract members",
        )


def _validate_class(node: IRClass, bag: DiagnosticBag, span: SourceSpan | None) -> None:
    if not node.name:
        bag.error("CW0313", "class has no name", stage="ir-validate", span=span)
    _check_duplicates(
        [item.name for item in node.fields],
        bag=bag,
        code="CW0314",
        what=f"field of {node.name!r}",
        span=span,
    )
    _check_duplicates(
        [method.name for method in node.methods],
        bag=bag,
        code="CW0315",
        what=f"method of {node.name!r}",
        span=span,
    )
    constructors = [method for method in node.methods if method.is_constructor]
    if len(constructors) > 1:
        bag.error(
            "CW0316",
            f"class {node.name!r} declares {len(constructors)} constructors",
            stage="ir-validate",
            span=span,
        )
    if node.is_interface and node.fields:
        bag.warning(
            "CW0317",
            f"interface {node.name!r} declares {len(node.fields)} field(s)",
            stage="ir-validate",
            span=span,
        )


def _validate_import(node: IRImport, bag: DiagnosticBag, span: SourceSpan | None) -> None:
    if not node.module and not node.is_relative:
        bag.error("CW0318", "import has no module", stage="ir-validate", span=span)
    if node.level and not node.is_relative:
        bag.error(
            "CW0319",
            f"import of {node.module!r} has level {node.level} but is not marked relative",
            stage="ir-validate",
            span=span,
        )
    _check_duplicates(
        [name.local_name for name in node.names],
        bag=bag,
        code="CW0320",
        what=f"imported name from {node.module!r}",
        span=span,
    )


def _validate_block(node: IRBlock, bag: DiagnosticBag, span: SourceSpan | None) -> None:
    for index, statement in enumerate(node.statements):
        # IRUnsupported is position-agnostic by design: it stands in wherever
        # the construct it replaced stood, including a statement slot.
        if not isinstance(statement, IRStatement | IRUnsupported):
            bag.error(
                "CW0321",
                f"block entry {index} is a {statement.kind!r} node, not a statement",
                stage="ir-validate",
                span=getattr(statement, "span", None) or span,
                hint="wrap expressions in an expression statement",
            )


def _validate_try(node: IRTry, bag: DiagnosticBag, span: SourceSpan | None) -> None:
    if not node.handlers and node.finally_body is None:
        bag.error(
            "CW0322",
            "try statement has neither a handler nor a finally body",
            stage="ir-validate",
            span=span,
        )
    for index, handler in enumerate(node.handlers):
        if not handler.exception_types and index != len(node.handlers) - 1:
            bag.error(
                "CW0323",
                "a catch-all handler must be the last handler",
                stage="ir-validate",
                span=handler.span or span,
            )
    if node.else_body is not None and not node.handlers:
        bag.warning(
            "CW0324",
            "try statement has an else body but no handlers",
            stage="ir-validate",
            span=span,
        )


#: node kind -> field names that must hold an expression when present.
_EXPRESSION_SLOTS: dict[str, tuple[str, ...]] = {
    "binary": ("left", "right"),
    "unary": ("operand",),
    "compare": ("left", "right"),
    "call": ("callee",),
    "attribute": ("target",),
    "index": ("target", "index"),
    "slice": ("target",),
    "conditional": ("test", "then_value", "else_value"),
    "await": ("operand",),
    "keyword": ("value",),
    "map_entry": ("key", "value"),
    "expression_statement": ("expression",),
    "assign": ("target", "value"),
    "aug_assign": ("target", "value"),
    "if": ("test",),
    "while": ("test",),
    "for_each": ("target", "iterable"),
    "for_range": ("start", "stop"),
    "assert": ("test",),
}


def _validate_expression_slots(node: IRNode, bag: DiagnosticBag, span: SourceSpan | None) -> None:
    for name in _EXPRESSION_SLOTS.get(node.kind, ()):
        value = getattr(node, name, None)
        if value is None:
            bag.error(
                "CW0325",
                f"{node.kind!r} node is missing required expression {name!r}",
                stage="ir-validate",
                span=span,
            )
        elif not isinstance(value, IRExpression | IRUnsupported):
            bag.error(
                "CW0326",
                f"{node.kind!r} node slot {name!r} holds a {value.kind!r} node, "
                "which is not an expression",
                stage="ir-validate",
                span=span,
            )
    if (
        isinstance(node, IRParam)
        and node.default is not None
        and not isinstance(node.default, IRExpression)
    ):
        bag.error(
            "CW0327",
            f"default of parameter {node.name!r} is not an expression",
            stage="ir-validate",
            span=span,
        )


def _check_duplicates(
    names: list[str],
    *,
    bag: DiagnosticBag,
    code: str,
    what: str,
    span: SourceSpan | None,
) -> None:
    for name, count in sorted(Counter(filter(None, names)).items()):
        if count > 1:
            bag.error(
                code,
                f"duplicate {what}: {name!r} declared {count} times",
                stage="ir-validate",
                span=span,
            )
