"""IR statement nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from codeweaver.ir.base import IRNode, IRUnsupported, ir_node
from codeweaver.ir.expressions import ExpressionSlot
from codeweaver.ir.operators import BinaryOp
from codeweaver.ir.types import IRType, t_unknown

__all__ = [
    "IRAssert",
    "IRAssign",
    "IRAugAssign",
    "IRBlock",
    "IRBreak",
    "IRCatch",
    "IRComment",
    "IRContinue",
    "IRDelete",
    "IRExpressionStatement",
    "IRForEach",
    "IRForRange",
    "IRIf",
    "IRNoOp",
    "IRReturn",
    "IRStatement",
    "IRThrow",
    "IRTry",
    "IRVarDecl",
    "IRWhile",
    "StatementSlot",
]


@dataclass
class IRStatement(IRNode):
    """Base class for nodes executed for effect."""

    kind: ClassVar[str] = "statement"


#: What may stand where a statement is expected.  See :data:`ExpressionSlot`.
StatementSlot = IRStatement | IRUnsupported


@ir_node
@dataclass
class IRBlock(IRStatement):
    """An ordered sequence of statements forming one scope-less body."""

    kind: ClassVar[str] = "block"

    statements: list[StatementSlot] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.statements)

    @property
    def is_empty(self) -> bool:
        return not self.statements


@ir_node
@dataclass
class IRExpressionStatement(IRStatement):
    kind: ClassVar[str] = "expression_statement"

    expression: ExpressionSlot


@ir_node
@dataclass
class IRVarDecl(IRStatement):
    """Introduction of a new binding.

    Frontends distinguish a declaration from a re-assignment because targets
    that need ``let``/``const`` cannot recover the difference from an
    assignment alone.
    """

    kind: ClassVar[str] = "var_decl"

    name: str
    var_type: IRType = field(default_factory=t_unknown)
    value: ExpressionSlot | None = None
    is_const: bool = False


@ir_node
@dataclass
class IRAssign(IRStatement):
    """Assignment to an existing target, which may be a name, attribute or index."""

    kind: ClassVar[str] = "assign"

    target: ExpressionSlot
    value: ExpressionSlot


@ir_node
@dataclass
class IRAugAssign(IRStatement):
    kind: ClassVar[str] = "aug_assign"

    op: BinaryOp
    target: ExpressionSlot
    value: ExpressionSlot


@ir_node
@dataclass
class IRReturn(IRStatement):
    kind: ClassVar[str] = "return"

    value: ExpressionSlot | None = None


@ir_node
@dataclass
class IRIf(IRStatement):
    kind: ClassVar[str] = "if"

    test: ExpressionSlot
    then_body: IRBlock = field(default_factory=IRBlock)
    else_body: IRBlock | None = None


@ir_node
@dataclass
class IRWhile(IRStatement):
    kind: ClassVar[str] = "while"

    test: ExpressionSlot
    body: IRBlock = field(default_factory=IRBlock)
    #: Python's ``while ... else``.  Kept so a backend can lower or refuse it
    #: instead of dropping a branch that can legitimately run.
    else_body: IRBlock | None = None


@ir_node
@dataclass
class IRForEach(IRStatement):
    """Iteration over a sequence, equivalent to ``for x of xs``."""

    kind: ClassVar[str] = "for_each"

    target: ExpressionSlot
    iterable: ExpressionSlot
    body: IRBlock = field(default_factory=IRBlock)
    else_body: IRBlock | None = None


@ir_node
@dataclass
class IRForRange(IRStatement):
    """A counted loop.

    This is not produced directly by a frontend; a transform recognises
    ``for i in range(...)`` and lowers it here so that numeric targets can emit
    an index loop instead of materialising a range.
    """

    kind: ClassVar[str] = "for_range"

    variable: str
    start: ExpressionSlot
    stop: ExpressionSlot
    step: ExpressionSlot | None = None
    body: IRBlock = field(default_factory=IRBlock)


@ir_node
@dataclass
class IRBreak(IRStatement):
    kind: ClassVar[str] = "break"


@ir_node
@dataclass
class IRContinue(IRStatement):
    kind: ClassVar[str] = "continue"


@ir_node
@dataclass
class IRNoOp(IRStatement):
    """An explicit no-op, such as Python's ``pass``."""

    kind: ClassVar[str] = "noop"


@ir_node
@dataclass
class IRThrow(IRStatement):
    kind: ClassVar[str] = "throw"

    value: ExpressionSlot | None = None
    cause: ExpressionSlot | None = None


@ir_node
@dataclass
class IRCatch(IRNode):
    """One handler of a :class:`IRTry`.

    ``exception_types`` is a list because ``except (A, B)`` must not be
    flattened into a single unnamed catch.
    """

    kind: ClassVar[str] = "catch"

    exception_types: list[ExpressionSlot] = field(default_factory=list)
    name: str = ""
    body: IRBlock = field(default_factory=IRBlock)


@ir_node
@dataclass
class IRTry(IRStatement):
    kind: ClassVar[str] = "try"

    body: IRBlock = field(default_factory=IRBlock)
    handlers: list[IRCatch] = field(default_factory=list)
    else_body: IRBlock | None = None
    finally_body: IRBlock | None = None


@ir_node
@dataclass
class IRDelete(IRStatement):
    kind: ClassVar[str] = "delete"

    targets: list[ExpressionSlot] = field(default_factory=list)


@ir_node
@dataclass
class IRAssert(IRStatement):
    kind: ClassVar[str] = "assert"

    test: ExpressionSlot
    message: ExpressionSlot | None = None


@ir_node
@dataclass
class IRComment(IRStatement):
    """A comment preserved from the source, or emitted by a transform.

    ``generated`` marks comments CodeWeaver itself produced (for example an
    unsupported-construct marker) so that they can be told apart from the
    author's own words in a diff.
    """

    kind: ClassVar[str] = "comment"

    text: str
    generated: bool = False
