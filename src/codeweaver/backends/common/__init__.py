"""Building blocks shared by every target backend.

Nothing here knows a target language.  The writer owns layout, the generated
file record carries mappings back to the source, and :mod:`clike` holds the
operator-precedence model that C, C++ and Java share.
"""

from __future__ import annotations

from codeweaver.backends.common.context import UNSUPPORTED_MARKER, EmitContext
from codeweaver.backends.common.files import GeneratedFile
from codeweaver.backends.common.strings import c_string_literal
from codeweaver.backends.common.writer import CodeWriter, MappingEntry

__all__ = [
    "UNSUPPORTED_MARKER",
    "CodeWriter",
    "EmitContext",
    "GeneratedFile",
    "MappingEntry",
    "c_string_literal",
]
