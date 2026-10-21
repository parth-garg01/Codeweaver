"""IR expression nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from codeweaver.ir.base import IRNode, IRUnsupported, ir_node
from codeweaver.ir.operators import BinaryOp, CompareOp, LogicalOp, UnaryOp
from codeweaver.ir.types import IRType, t_unknown

__all__ = [
    "ExpressionSlot",
    "IRArrayLiteral",
    "IRComprehension",
    "IRAttribute",
    "IRAwait",
    "IRBinary",
    "IRCall",
    "IRCompare",
    "IRConditional",
    "IRExpression",
    "IRIndex",
    "IRKeyword",
    "IRLambda",
    "IRLiteral",
    "IRLogical",
    "IRMapEntry",
    "IRMapLiteral",
    "IRName",
    "IRParam",
    "IRSetLiteral",
    "IRSlice",
    "IRTemplateString",
    "IRTupleLiteral",
    "IRUnary",
]


@dataclass
class IRExpression(IRNode):
    """Base class for nodes that produce a value.

    ``value_type`` is the frontend's best observation.  It defaults to
    ``unknown`` rather than ``any``: a backend must be able to tell "no
    information" apart from "deliberately untyped".
    """

    kind: ClassVar[str] = "expression"

    value_type: IRType = field(default_factory=t_unknown, kw_only=True)


#: What may stand where an expression is expected.
#:
#: :class:`~codeweaver.ir.base.IRUnsupported` is position-agnostic by design —
#: it stands wherever the construct it replaced stood — and
#: :mod:`codeweaver.ir.schema` validates exactly that.  Spelling it out in the
#: annotations keeps the type checker in agreement with the validator, instead
#: of rejecting every frontend converter that can report a gap.
#:
#: Written as a plain alias rather than PEP 695 `type` syntax because the
#: project supports Python 3.11.
ExpressionSlot = IRExpression | IRUnsupported


@ir_node
@dataclass
class IRLiteral(IRExpression):
    """A constant: number, string, boolean, ``None`` or bytes."""

    kind: ClassVar[str] = "literal"

    value: Any
    #: Original spelling, kept so a backend can preserve ``0x1f`` or ``1_000``.
    source_text: str = ""


@ir_node
@dataclass
class IRName(IRExpression):
    """A reference to a binding by name."""

    kind: ClassVar[str] = "name"

    name: str
    #: Set by the symbol resolver when the binding could be located.
    resolved_symbol: str = ""


@ir_node
@dataclass
class IRBinary(IRExpression):
    kind: ClassVar[str] = "binary"

    op: BinaryOp
    left: ExpressionSlot
    right: ExpressionSlot


@ir_node
@dataclass
class IRUnary(IRExpression):
    kind: ClassVar[str] = "unary"

    op: UnaryOp
    operand: ExpressionSlot


@ir_node
@dataclass
class IRCompare(IRExpression):
    """A single comparison.

    Python's chained comparisons (``a < b < c``) are expanded by the frontend
    into a logical conjunction of these, because most targets have no chaining.
    """

    kind: ClassVar[str] = "compare"

    op: CompareOp
    left: ExpressionSlot
    right: ExpressionSlot


@ir_node
@dataclass
class IRLogical(IRExpression):
    kind: ClassVar[str] = "logical"

    op: LogicalOp
    operands: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRKeyword(IRNode):
    """A named argument at a call site."""

    kind: ClassVar[str] = "keyword"

    name: str
    value: ExpressionSlot


@ir_node
@dataclass
class IRCall(IRExpression):
    kind: ClassVar[str] = "call"

    callee: ExpressionSlot
    args: list[ExpressionSlot] = field(default_factory=list)
    keywords: list[IRKeyword] = field(default_factory=list)
    #: ``f(*xs)`` / ``f(**kw)`` positions, recorded so a backend can refuse or
    #: lower them rather than quietly dropping the spread.
    star_args: list[ExpressionSlot] = field(default_factory=list)
    star_keywords: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRAttribute(IRExpression):
    kind: ClassVar[str] = "attribute"

    target: ExpressionSlot
    name: str


@ir_node
@dataclass
class IRIndex(IRExpression):
    kind: ClassVar[str] = "index"

    target: ExpressionSlot
    index: ExpressionSlot


@ir_node
@dataclass
class IRSlice(IRExpression):
    kind: ClassVar[str] = "slice"

    target: ExpressionSlot
    lower: ExpressionSlot | None = None
    upper: ExpressionSlot | None = None
    step: ExpressionSlot | None = None


@ir_node
@dataclass
class IRArrayLiteral(IRExpression):
    kind: ClassVar[str] = "array"

    elements: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRTupleLiteral(IRExpression):
    kind: ClassVar[str] = "tuple"

    elements: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRSetLiteral(IRExpression):
    kind: ClassVar[str] = "set"

    elements: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRMapEntry(IRNode):
    kind: ClassVar[str] = "map_entry"

    key: ExpressionSlot
    value: ExpressionSlot


@ir_node
@dataclass
class IRMapLiteral(IRExpression):
    kind: ClassVar[str] = "map"

    entries: list[IRMapEntry] = field(default_factory=list)


@ir_node
@dataclass
class IRParam(IRNode):
    """A parameter of a function, method or lambda."""

    kind: ClassVar[str] = "param"

    name: str
    param_type: IRType = field(default_factory=t_unknown)
    default: ExpressionSlot | None = None
    #: ``positional`` | ``keyword`` | ``var_positional`` | ``var_keyword``
    style: str = "positional"


@ir_node
@dataclass
class IRLambda(IRExpression):
    kind: ClassVar[str] = "lambda"

    params: list[IRParam] = field(default_factory=list)
    body: ExpressionSlot | None = None


@ir_node
@dataclass
class IRConditional(IRExpression):
    kind: ClassVar[str] = "conditional"

    test: ExpressionSlot
    then_value: ExpressionSlot
    else_value: ExpressionSlot


@ir_node
@dataclass
class IRTemplateString(IRExpression):
    """An interpolated string.

    ``parts`` alternates freely between :class:`IRLiteral` text chunks and
    arbitrary expressions, so a backend can emit a template literal without
    re-parsing a format string.
    """

    kind: ClassVar[str] = "template_string"

    parts: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRAwait(IRExpression):
    kind: ClassVar[str] = "await"

    operand: ExpressionSlot


@ir_node
@dataclass
class IRComprehension(IRExpression):
    """A single-clause comprehension.

    Only the shape every target can express is modelled: one iteration clause
    over a named binding with an optional filter.  Nested clauses, multiple
    ``if`` guards and ``async for`` are reported as unsupported by the frontend
    rather than squeezed into this node, because flattening them would change
    evaluation order.

    ``form`` is ``"list"``, ``"set"`` or ``"dict"``.  For a dict comprehension
    ``key`` holds the key expression and ``element`` holds the value.
    """

    kind: ClassVar[str] = "comprehension"

    form: str
    target: str
    iterable: ExpressionSlot
    element: ExpressionSlot
    key: ExpressionSlot | None = None
    condition: ExpressionSlot | None = None
