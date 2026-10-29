"""The language registry: what CodeWeaver can actually translate."""

from __future__ import annotations

__all__ = [
    "Backend",
    "Frontend",
    "GeneratedFile",
    "ParseResult",
    "LanguagePair",
    "LanguageRegistry",
    "SupportLevel",
    "registry",
]

from codeweaver.language_registry.registry import (
    Backend,
    Frontend,
    GeneratedFile,
    LanguagePair,
    LanguageRegistry,
    ParseResult,
    SupportLevel,
    registry,
)
