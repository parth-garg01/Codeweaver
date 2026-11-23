"""Operator precedence shared by C, C++ and Java.

The three languages agree on the relative precedence of every operator the IR
can produce, which is the property that matters: an expression is
parenthesised only where the target would otherwise re-associate it, so the
output reads like the source rather than like a fully bracketed tree.
"""

from __future__ import annotations

from dataclasses import dataclass

from codeweaver.ir.operators import BinaryOp, CompareOp

__all__ = [
    "ADDITIVE",
    "ASSIGNMENT",
    "BIT_AND",
    "BIT_OR",
    "BIT_XOR",
    "BINARY_SPELLING",
    "COMPARE_SPELLING",
    "CONDITIONAL",
    "EQUALITY",
    "LAMBDA",
    "LOGICAL_AND",
    "LOGICAL_OR",
    "MULTIPLICATIVE",
    "POSTFIX",
    "PRIMARY",
    "RELATIONAL",
    "SHIFT",
    "UNARY",
    "Emitted",
]

PRIMARY = 17
POSTFIX = 16
UNARY = 15
MULTIPLICATIVE = 13
ADDITIVE = 12
SHIFT = 11
RELATIONAL = 10
EQUALITY = 9
BIT_AND = 8
BIT_XOR = 7
BIT_OR = 6
LOGICAL_AND = 5
LOGICAL_OR = 4
CONDITIONAL = 3
ASSIGNMENT = 2
LAMBDA = 1

BINARY_SPELLING: dict[BinaryOp, tuple[str, int]] = {
    BinaryOp.ADD: ("+", ADDITIVE),
    BinaryOp.SUB: ("-", ADDITIVE),
    BinaryOp.MUL: ("*", MULTIPLICATIVE),
    BinaryOp.DIV: ("/", MULTIPLICATIVE),
    BinaryOp.MOD: ("%", MULTIPLICATIVE),
    BinaryOp.BIT_AND: ("&", BIT_AND),
    BinaryOp.BIT_OR: ("|", BIT_OR),
    BinaryOp.BIT_XOR: ("^", BIT_XOR),
    BinaryOp.SHIFT_LEFT: ("<<", SHIFT),
    BinaryOp.SHIFT_RIGHT: (">>", SHIFT),
}

COMPARE_SPELLING: dict[CompareOp, tuple[str, int]] = {
    CompareOp.EQ: ("==", EQUALITY),
    CompareOp.NE: ("!=", EQUALITY),
    CompareOp.LT: ("<", RELATIONAL),
    CompareOp.LE: ("<=", RELATIONAL),
    CompareOp.GT: (">", RELATIONAL),
    CompareOp.GE: (">=", RELATIONAL),
    CompareOp.IS: ("==", EQUALITY),
    CompareOp.IS_NOT: ("!=", EQUALITY),
}


@dataclass(frozen=True, slots=True)
class Emitted:
    """A rendered expression and the precedence of its outermost operator."""

    text: str
    precedence: int = PRIMARY

    def at_least(self, needed: int) -> str:
        """Render, parenthesising only when the operator would re-associate."""
        return f"({self.text})" if self.precedence < needed else self.text

    def __str__(self) -> str:
        return self.text
