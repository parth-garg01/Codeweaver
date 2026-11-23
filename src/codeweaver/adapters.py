"""Adapter registration.

Importing this module populates the process-wide language registry.  It is the
only place that knows which concrete frontends and backends exist, which keeps
the registry itself free of imports from the adapters it holds.

A pair is declared ``PARTIAL`` rather than ``SUPPORTED`` while any construct in
the frontend's own limitation list is still unmapped.  The distinction is
user-visible, so it should not be upgraded without the coverage to justify it.
"""

from __future__ import annotations

from codeweaver.backends.c import CBackend
from codeweaver.backends.cpp import CppBackend
from codeweaver.backends.java import JavaBackend
from codeweaver.backends.typescript import TypeScriptBackend
from codeweaver.frontends.python import PythonFrontend
from codeweaver.language_registry import LanguagePair, LanguageRegistry, SupportLevel, registry

__all__ = ["register_all"]


def register_all(target: LanguageRegistry | None = None) -> LanguageRegistry:
    """Register every bundled adapter into *target* (the global registry by default)."""
    into = target if target is not None else registry
    into.register_frontend(PythonFrontend())
    into.register_backend(TypeScriptBackend())
    into.declare_pair(
        LanguagePair(
            source="python",
            target="typescript",
            level=SupportLevel.PARTIAL,
            notes=(
                "Implemented end to end for a documented construct subset. "
                "Comprehensions, context managers, pattern matching and nested "
                "definitions are reported rather than translated."
            ),
        )
    )
    into.register_backend(CppBackend())
    into.declare_pair(
        LanguagePair(
            source="python",
            target="cpp",
            level=SupportLevel.PARTIAL,
            notes=(
                "Implemented end to end for a documented construct subset, validated "
                "with a C++20 compiler. Unannotated parameters become templates; "
                "generators, context managers and async code are reported."
            ),
        )
    )
    into.register_backend(JavaBackend())
    into.declare_pair(
        LanguagePair(
            source="python",
            target="java",
            level=SupportLevel.PARTIAL,
            notes=(
                "Implemented end to end for a documented construct subset, validated "
                "with javac. Unannotated parameters are declared as Object; "
                "generators, context managers and async code are reported."
            ),
        )
    )
    into.register_backend(CBackend())
    into.declare_pair(
        LanguagePair(
            source="python",
            target="c",
            level=SupportLevel.PARTIAL,
            notes=(
                "Implemented end to end for a narrower subset, validated with a C11 "
                "compiler: ints, floats, bools, strings, lists and classes. dict, set "
                "and tuple values and try/except are reported."
            ),
        )
    )
    return into


register_all()
