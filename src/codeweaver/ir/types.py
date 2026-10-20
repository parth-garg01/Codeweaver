"""The IR type lattice.

Types are structural descriptions, not language-specific spellings.  A frontend
records what it could observe (``list[int]``, ``str``, ``unknown``) and a
backend decides how to spell it.  ``UNKNOWN`` is a first-class answer: it means
"the frontend could not determine this", which is different from ``ANY``, which
means "the source explicitly allowed anything".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PRIMITIVE_NAMES",
    "TypeKind",
    "IRType",
    "t_any",
    "t_bool",
    "t_callable",
    "t_dict",
    "t_float",
    "t_int",
    "t_list",
    "t_none",
    "t_optional",
    "t_set",
    "t_str",
    "t_tuple",
    "t_union",
    "t_unknown",
]


class TypeKind(enum.StrEnum):
    PRIMITIVE = "primitive"
    COLLECTION = "collection"
    CALLABLE = "callable"
    OBJECT = "object"
    UNION = "union"
    OPTIONAL = "optional"
    ANY = "any"
    UNKNOWN = "unknown"
    NONE = "none"


PRIMITIVE_NAMES: frozenset[str] = frozenset({"int", "float", "str", "bool", "bytes"})


@dataclass(frozen=True, slots=True)
class IRType:
    """A type expression: a kind, a name, and zero or more arguments."""

    kind: TypeKind
    name: str = ""
    args: tuple[IRType, ...] = ()
    #: Original spelling in the source language, kept for diagnostics.
    source_text: str = ""
    nullable: bool = False
    meta: dict[str, Any] = field(default_factory=dict, compare=False)

    def __str__(self) -> str:
        if self.kind is TypeKind.OPTIONAL and self.args:
            return f"{self.args[0]} | None"
        if self.kind is TypeKind.UNION:
            return " | ".join(str(arg) for arg in self.args)
        if self.kind is TypeKind.CALLABLE:
            *params, returns = self.args or (t_unknown(),)
            joined = ", ".join(str(param) for param in params)
            return f"({joined}) -> {returns}"
        if self.args:
            joined = ", ".join(str(arg) for arg in self.args)
            return f"{self.name}[{joined}]"
        return self.name or self.kind.value

    @property
    def is_known(self) -> bool:
        return self.kind is not TypeKind.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": "type", "typeKind": self.kind.value}
        if self.name:
            payload["name"] = self.name
        if self.args:
            payload["args"] = [arg.to_dict() for arg in self.args]
        if self.source_text:
            payload["sourceText"] = self.source_text
        if self.nullable:
            payload["nullable"] = True
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IRType:
        return cls(
            kind=TypeKind(data["typeKind"]),
            name=data.get("name", ""),
            args=tuple(cls.from_dict(arg) for arg in data.get("args", ())),
            source_text=data.get("sourceText", ""),
            nullable=bool(data.get("nullable", False)),
        )


def _primitive(name: str) -> IRType:
    return IRType(TypeKind.PRIMITIVE, name, source_text=name)


def t_int() -> IRType:
    return _primitive("int")


def t_float() -> IRType:
    return _primitive("float")


def t_str() -> IRType:
    return _primitive("str")


def t_bool() -> IRType:
    return _primitive("bool")


def t_none() -> IRType:
    return IRType(TypeKind.NONE, "None", source_text="None")


def t_any() -> IRType:
    return IRType(TypeKind.ANY, "any", source_text="Any")


def t_unknown(source_text: str = "") -> IRType:
    return IRType(TypeKind.UNKNOWN, "unknown", source_text=source_text)


def t_list(item: IRType | None = None) -> IRType:
    return IRType(TypeKind.COLLECTION, "list", (item or t_unknown(),))


def t_set(item: IRType | None = None) -> IRType:
    return IRType(TypeKind.COLLECTION, "set", (item or t_unknown(),))


def t_tuple(*items: IRType) -> IRType:
    return IRType(TypeKind.COLLECTION, "tuple", items)


def t_dict(key: IRType | None = None, value: IRType | None = None) -> IRType:
    return IRType(TypeKind.COLLECTION, "dict", (key or t_unknown(), value or t_unknown()))


def t_callable(params: tuple[IRType, ...] = (), returns: IRType | None = None) -> IRType:
    return IRType(TypeKind.CALLABLE, "callable", (*params, returns or t_unknown()))


def t_optional(inner: IRType) -> IRType:
    if inner.kind is TypeKind.OPTIONAL:
        return inner
    return IRType(TypeKind.OPTIONAL, "optional", (inner,), nullable=True)


def t_union(*members: IRType) -> IRType:
    unique: list[IRType] = []
    for member in members:
        if member not in unique:
            unique.append(member)
    if len(unique) == 1:
        return unique[0]
    return IRType(TypeKind.UNION, "union", tuple(unique))
