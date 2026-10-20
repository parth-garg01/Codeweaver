"""Python exception types to JavaScript error types.

JavaScript has five built-in error constructors and Python has dozens, so the
mapping is lossy in a specific way: several Python types collapse onto ``Error``
and therefore stop being distinguishable by a ``catch``.  Every collapsed
mapping is reported, because a ``try/except ValueError`` that silently starts
catching every ``Error`` is a behaviour change a type checker will not find.

Names that are not built-in Python exceptions are left alone: a project's own
exception class translates to its own generated class.
"""

from __future__ import annotations

__all__ = [
    "COLLAPSED_TO_ERROR",
    "EXCEPTION_MAP",
    "is_builtin_exception",
    "map_exception_name",
]

#: Python exception -> JavaScript error constructor.
EXCEPTION_MAP: dict[str, str] = {
    "BaseException": "Error",
    "Exception": "Error",
    "ValueError": "Error",
    "RuntimeError": "Error",
    "NotImplementedError": "Error",
    "AssertionError": "Error",
    "KeyError": "Error",
    "StopIteration": "Error",
    "OSError": "Error",
    "IOError": "Error",
    "FileNotFoundError": "Error",
    "ArithmeticError": "RangeError",
    "ZeroDivisionError": "RangeError",
    "OverflowError": "RangeError",
    "IndexError": "RangeError",
    "TypeError": "TypeError",
    "AttributeError": "TypeError",
    "NameError": "ReferenceError",
    "UnboundLocalError": "ReferenceError",
    "SyntaxError": "SyntaxError",
    "IndentationError": "SyntaxError",
    "UnicodeDecodeError": "URIError",
    "UnicodeEncodeError": "URIError",
}

#: Mappings that lose the ability to tell the exception apart in a catch.
COLLAPSED_TO_ERROR: frozenset[str] = frozenset(
    name for name, mapped in EXCEPTION_MAP.items() if mapped == "Error"
)


def is_builtin_exception(name: str) -> bool:
    return name in EXCEPTION_MAP


def map_exception_name(name: str) -> str:
    """The JavaScript constructor for *name*, or *name* itself."""
    return EXCEPTION_MAP.get(name, name)
