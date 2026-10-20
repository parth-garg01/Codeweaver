"""Normalised operator names.

Frontends translate their own operator spellings into these names and backends
translate them back.  Keeping the set small and explicit means an operator that
has no counterpart in the target language is a visible gap rather than a silent
mistranslation — Python's ``//`` and ``**``, for example, have no direct
TypeScript operator and must be lowered by a transform.
"""

from __future__ import annotations

import enum

__all__ = ["BinaryOp", "CompareOp", "LogicalOp", "UnaryOp"]


class BinaryOp(enum.StrEnum):
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    FLOOR_DIV = "floor_div"
    MOD = "mod"
    POW = "pow"
    BIT_AND = "bit_and"
    BIT_OR = "bit_or"
    BIT_XOR = "bit_xor"
    SHIFT_LEFT = "shift_left"
    SHIFT_RIGHT = "shift_right"
    MAT_MUL = "mat_mul"


class UnaryOp(enum.StrEnum):
    NEG = "neg"
    POS = "pos"
    NOT = "not"
    BIT_NOT = "bit_not"


class CompareOp(enum.StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IS = "is"
    IS_NOT = "is_not"
    IN = "in"
    NOT_IN = "not_in"


class LogicalOp(enum.StrEnum):
    AND = "and"
    OR = "or"
