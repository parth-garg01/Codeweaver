"""Reading project text files.

Every text file CodeWeaver reads from a project goes through here, for one
reason: a UTF-8 byte-order mark.  CPython strips a leading BOM when it compiles
a file, so a BOM-prefixed module imports fine — but decoding the same bytes as
plain ``utf-8`` keeps the mark as a ``U+FEFF`` character, and ``ast.parse`` on
that string raises ``invalid non-printable character U+FEFF``.

The same mark breaks quieter things: a BOM-prefixed ``.gitignore`` loses its
first pattern, and a BOM-prefixed ``pyproject.toml`` loses its first key.

``utf-8-sig`` decodes both BOM-prefixed and plain UTF-8 correctly, so it is the
only decoding this project does.  Keeping it in one function means a future
reader cannot reintroduce the inconsistency by reaching for ``read_text``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SOURCE_ENCODING", "decode_source", "read_project_text"]

#: The encoding used for every project text file.  ``utf-8-sig`` reads plain
#: UTF-8 unchanged and strips a byte-order mark when one is present.
SOURCE_ENCODING = "utf-8-sig"


def decode_source(raw: bytes) -> str:
    """Decode *raw* project bytes, stripping a byte-order mark if present."""
    return raw.decode(SOURCE_ENCODING)


def read_project_text(path: Path, *, errors: str = "strict") -> str:
    """Read *path* as project text.

    Raises the same exceptions as :meth:`Path.read_text` so callers keep their
    existing error handling; only the decoding differs.
    """
    return path.read_text(encoding=SOURCE_ENCODING, errors=errors)
