"""Pieces every target backend needs, with no target-specific knowledge.

A backend is mostly language-specific by nature, so this package stays small on
purpose.  Something belongs here only if it would be character-for-character
identical in a second backend: indentation and brace bookkeeping, source-map
accumulation, and the rules for turning a source name into a safe identifier in
a language with reserved words.

Anything that encodes how a *particular* language spells something stays in
that language's package, where a reader looking for "how does CodeWeaver emit a
Go map" can find it.
"""

from __future__ import annotations

from codeweaver.backends.common.identifiers import (
    IdentifierPolicy,
    escape_double_quoted,
)
from codeweaver.backends.common.writer import CodeWriter, MappingEntry

__all__ = [
    "CodeWriter",
    "IdentifierPolicy",
    "MappingEntry",
    "escape_double_quoted",
]
