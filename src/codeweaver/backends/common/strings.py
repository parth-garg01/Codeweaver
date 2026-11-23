"""String literal spelling that is valid in C, C++ and Java alike.

JSON escaping is not safe here.  Java translates ``\\uXXXX`` escapes before it
tokenises, so a ``\\u000a`` inside a string literal ends the line and breaks the
file, and C rejects universal character names for control characters.  Octal
escapes mean the same thing in all three languages, so every control character
is written as a three-digit octal escape and everything else is left as UTF-8.
"""

from __future__ import annotations

__all__ = ["c_string_literal", "escape_c_text"]

_SIMPLE = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
}


def escape_c_text(value: str, *, trigraphs: bool = False) -> str:
    """Escape *value* for the inside of a double-quoted C-family literal.

    *trigraphs* escapes ``?`` as ``\\?`` so ``??=`` cannot form a trigraph in a
    strict ISO C dialect.  Java has no ``\\?`` escape, so it is opt-in.
    """
    pieces: list[str] = []
    for char in value:
        simple = _SIMPLE.get(char)
        if simple is not None:
            pieces.append(simple)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            pieces.append(f"\\{ord(char):03o}")
        elif char == "?" and trigraphs:
            # `??=` and friends are trigraphs in older C dialects.
            pieces.append("\\?")
        else:
            pieces.append(char)
    return "".join(pieces)


def c_string_literal(value: str, *, trigraphs: bool = False) -> str:
    return f'"{escape_c_text(value, trigraphs=trigraphs)}"'
