"""Identifier and literal spelling for TypeScript output.

Identifiers are preserved verbatim wherever TypeScript allows it.  Renaming
``total_count`` to ``totalCount`` would make the generated project harder to
review against the original and would silently break any string-keyed access,
so the backend only changes a name when it has to: a reserved word, or a
character TypeScript does not accept in an identifier.
"""

from __future__ import annotations

import json
import re

__all__ = [
    "RESERVED_WORDS",
    "escape_string",
    "is_valid_identifier",
    "safe_identifier",
    "safe_property",
]

#: TypeScript reserved words plus the strict-mode and contextual words that
#: cannot be used as binding names.
RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "any",
        "as",
        "asserts",
        "await",
        "boolean",
        "break",
        "case",
        "catch",
        "class",
        "const",
        "constructor",
        "continue",
        "debugger",
        "declare",
        "default",
        "delete",
        "do",
        "else",
        "enum",
        "export",
        "extends",
        "false",
        "finally",
        "for",
        "function",
        "get",
        "if",
        "implements",
        "import",
        "in",
        "infer",
        "instanceof",
        "interface",
        "is",
        "keyof",
        "let",
        "module",
        "namespace",
        "never",
        "new",
        "null",
        "number",
        "object",
        "package",
        "private",
        "protected",
        "public",
        "readonly",
        "require",
        "return",
        "set",
        "static",
        "string",
        "super",
        "switch",
        "symbol",
        "this",
        "throw",
        "true",
        "try",
        "type",
        "typeof",
        "unique",
        "unknown",
        "var",
        "void",
        "while",
        "with",
        "yield",
    }
)

_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def is_valid_identifier(name: str) -> bool:
    return bool(_IDENTIFIER.match(name)) and name not in RESERVED_WORDS


def safe_identifier(name: str) -> str:
    """Return *name* usable as a TypeScript binding name.

    A reserved word gains a trailing underscore; invalid characters are
    replaced; an empty or digit-leading name is prefixed.  The transformation
    is deterministic so two runs agree.
    """
    if not name:
        return "_"
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", name)
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    if cleaned in RESERVED_WORDS:
        cleaned = f"{cleaned}_"
    return cleaned


def safe_property(name: str) -> str:
    """Return *name* usable as an object or class member name.

    Reserved words are legal as property names, so only genuinely invalid
    spellings are quoted — and quoting is preferred over renaming because a
    renamed property changes the shape of the value.
    """
    if _IDENTIFIER.match(name):
        return name
    return escape_string(name)


def escape_string(value: str) -> str:
    """Render *value* as a double-quoted TypeScript string literal.

    ``json.dumps`` is used because its escaping rules are a subset of
    JavaScript's and it handles control characters and surrogate pairs
    correctly, which hand-rolled escaping routinely gets wrong.
    """
    return json.dumps(value, ensure_ascii=False)
