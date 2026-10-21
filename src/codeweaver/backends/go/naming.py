"""Go identifiers, and the one place CodeWeaver has to rename.

Go decides visibility by the first letter: a name starting with an upper-case
letter is visible outside its package, and nothing else is.  There is no
keyword to override that.  So unlike every other target, translating to Go
*requires* renaming — ``def total()`` has to become ``Total`` or no other
package can call it.

Renaming is the thing most likely to break a translation, so it is confined
here and applied through one function.  Every declaration site and every
reference site asks the same question of the same code, and the migration
report lists what was renamed.
"""

from __future__ import annotations

import re

from codeweaver.backends.common import IdentifierPolicy

__all__ = ["GO_POLICY", "RESERVED", "exported_name", "unexported_name"]

#: Go's keywords, plus the predeclared identifiers that would shadow something
#: important if used as a binding name.
RESERVED: frozenset[str] = frozenset(
    {
        "break",
        "case",
        "chan",
        "const",
        "continue",
        "default",
        "defer",
        "else",
        "fallthrough",
        "for",
        "func",
        "go",
        "goto",
        "if",
        "import",
        "interface",
        "map",
        "package",
        "range",
        "return",
        "select",
        "struct",
        "switch",
        "type",
        "var",
        "any",
        "append",
        "bool",
        "byte",
        "cap",
        "clear",
        "close",
        "complex",
        "complex64",
        "complex128",
        "copy",
        "delete",
        "error",
        "false",
        "float32",
        "float64",
        "imag",
        "int",
        "int8",
        "int16",
        "int32",
        "int64",
        "iota",
        "len",
        "make",
        "max",
        "min",
        "new",
        "nil",
        "panic",
        "print",
        "println",
        "real",
        "recover",
        "rune",
        "string",
        "true",
        "uint",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "uintptr",
    }
)

GO_POLICY = IdentifierPolicy(reserved=RESERVED)

_LEADING_UNDERSCORES = re.compile(r"^_+")


def exported_name(name: str) -> str:
    """Return *name* as a Go identifier visible outside its package.

    Leading underscores are dropped before capitalising, because ``_Total`` is
    not exported in Go — the first *letter* is what counts, and an underscore
    is not a letter.
    """
    cleaned = GO_POLICY.identifier(name)
    stripped = _LEADING_UNDERSCORES.sub("", cleaned) or cleaned
    if not stripped:
        return "X"
    first = stripped[0]
    if not first.isalpha():
        # A name that cannot start with a letter cannot be exported at all; the
        # prefix makes it legal and visible, and the rename is reported.
        return f"X{stripped}"
    return first.upper() + stripped[1:]


def unexported_name(name: str) -> str:
    """Return *name* as a Go identifier that stays inside its package."""
    cleaned = GO_POLICY.identifier(name)
    stripped = cleaned.lstrip("_") or cleaned
    if not stripped:
        return "x"
    first = stripped[0]
    if not first.isalpha():
        return f"x{stripped}"
    return first.lower() + stripped[1:]
