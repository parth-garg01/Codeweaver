"""Adapter registration.

Importing this module populates the process-wide language registry.  It is the
only place that knows which concrete frontends and backends exist, which keeps
the registry itself free of imports from the adapters it holds.

A pair is declared ``PARTIAL`` rather than ``SUPPORTED`` while any construct in
the frontend's own limitation list is still unmapped.  The distinction is
user-visible, so it should not be upgraded without the coverage to justify it.
"""

from __future__ import annotations

from codeweaver.backends.go import GoBackend
from codeweaver.backends.javascript import JavaScriptBackend
from codeweaver.backends.typescript import TypeScriptBackend
from codeweaver.frontends.python import PythonFrontend
from codeweaver.language_registry import LanguagePair, LanguageRegistry, SupportLevel, registry

__all__ = ["register_all"]


def register_all(target: LanguageRegistry | None = None) -> LanguageRegistry:
    """Register every bundled adapter into *target* (the global registry by default)."""
    into = target if target is not None else registry
    into.register_frontend(PythonFrontend())
    into.register_backend(TypeScriptBackend())
    into.register_backend(JavaScriptBackend())
    into.register_backend(GoBackend())
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
    into.declare_pair(
        LanguagePair(
            source="python",
            target="javascript",
            level=SupportLevel.PARTIAL,
            notes=(
                "Same construct coverage as the TypeScript target, with type "
                "annotations erased. Validation is a syntax check only, so a "
                "passing check proves much less."
            ),
        )
    )
    into.declare_pair(
        LanguagePair(
            source="python",
            target="go",
            level=SupportLevel.PARTIAL,
            notes=(
                "Classes become structs with pointer-receiver methods and NewT "
                "constructors. try/except is not translated: Go returns errors "
                "as values. Exported names are capitalised, which is the only "
                "renaming CodeWeaver does."
            ),
        )
    )
    return into


register_all()
