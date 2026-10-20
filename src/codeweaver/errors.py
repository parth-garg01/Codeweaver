"""Exception hierarchy.

CodeWeaver prefers diagnostics over exceptions for anything the user can act
on.  Exceptions are reserved for conditions that make continuing meaningless,
such as an unreadable project root or an IR tree that violates its own schema.
"""

from __future__ import annotations

__all__ = [
    "BackendError",
    "CodeWeaverError",
    "FrontendError",
    "IRSchemaError",
    "ProjectIntakeError",
    "UnsupportedPairError",
]


class CodeWeaverError(Exception):
    """Base class for every error raised by CodeWeaver."""


class ProjectIntakeError(CodeWeaverError):
    """The selected project root cannot be used."""


class IRSchemaError(CodeWeaverError):
    """An IR instance does not satisfy the IR schema."""


class FrontendError(CodeWeaverError):
    """A source-language frontend could not run at all."""


class BackendError(CodeWeaverError):
    """A target-language backend could not run at all."""


class UnsupportedPairError(CodeWeaverError):
    """The requested source/target language pair is not implemented."""
