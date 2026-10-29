"""Python exceptions in Go.

Go has no exception types.  The nearest thing an idiomatic program has is an
``error`` value, and the nearest thing to *raising* is ``panic`` — which is not
the same, and is reported wherever it is used.

A builtin Python exception therefore becomes ``errors.New(message)``: the type
is gone, so a translated program can no longer distinguish ``ValueError`` from
``KeyError``.  That loss is reported once per site.

A project's own exception class is a generated struct, so it keeps its identity
and becomes ``&MyError{}`` like any other constructed value.
"""

from __future__ import annotations

__all__ = ["BUILTIN_EXCEPTIONS", "is_builtin_exception"]

#: Builtin Python exceptions CodeWeaver recognises.  Membership is what decides
#: whether a raise site loses its type, so the list is explicit rather than a
#: pattern like "ends with Error".
BUILTIN_EXCEPTIONS: frozenset[str] = frozenset(
    {
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "BaseException",
        "Exception",
        "FileNotFoundError",
        "IOError",
        "IndentationError",
        "IndexError",
        "KeyError",
        "NameError",
        "NotImplementedError",
        "OSError",
        "OverflowError",
        "RuntimeError",
        "StopIteration",
        "SyntaxError",
        "TypeError",
        "UnboundLocalError",
        "UnicodeDecodeError",
        "UnicodeEncodeError",
        "ValueError",
        "ZeroDivisionError",
    }
)


def is_builtin_exception(name: str) -> bool:
    return name in BUILTIN_EXCEPTIONS
