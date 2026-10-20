"""Registry of source frontends, target backends and supported pairs.

The registry is the single place that answers "can CodeWeaver do this?".  It is
deliberately pessimistic: a pair is only offered when a frontend and a backend
are both registered *and* the pair has been declared supported.  There is no
generic fallback translator, because a fallback would have to invent mappings
that nobody implemented.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from codeweaver.errors import UnsupportedPairError

__all__ = [
    "Backend",
    "Frontend",
    "LanguagePair",
    "LanguageRegistry",
    "SupportLevel",
    "registry",
]


class SupportLevel(enum.StrEnum):
    """How much of a language pair is implemented."""

    #: Parsing, IR mapping, code generation and validation all exist.
    SUPPORTED = "supported"
    #: Implemented for a documented subset; other constructs are reported.
    PARTIAL = "partial"
    #: Declared but not usable yet.  Never offered to the user as runnable.
    PLANNED = "planned"

    @property
    def runnable(self) -> bool:
        return self in (SupportLevel.SUPPORTED, SupportLevel.PARTIAL)


@runtime_checkable
class Frontend(Protocol):
    """A source-language frontend: source text in, IR modules out."""

    language: str
    display_name: str
    file_extensions: tuple[str, ...]
    supported_constructs: tuple[str, ...]
    known_limitations: tuple[str, ...]

    def parse_module(self, source: str, path: str) -> Any: ...


@runtime_checkable
class Backend(Protocol):
    """A target-language backend: IR in, target files out."""

    language: str
    display_name: str
    file_extension: str
    supported_constructs: tuple[str, ...]
    known_limitations: tuple[str, ...]
    required_toolchains: tuple[str, ...]

    def generate_module(self, module: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class LanguagePair:
    """A declared source → target pair."""

    source: str
    target: str
    level: SupportLevel
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.source}->{self.target}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "level": self.level.value,
            "runnable": self.level.runnable,
            "notes": self.notes,
        }


@dataclass
class LanguageRegistry:
    """Holds the registered frontends, backends and pairs."""

    frontends: dict[str, Frontend] = field(default_factory=dict)
    backends: dict[str, Backend] = field(default_factory=dict)
    pairs: dict[str, LanguagePair] = field(default_factory=dict)

    def register_frontend(self, frontend: Frontend) -> Frontend:
        self.frontends[frontend.language] = frontend
        return frontend

    def register_backend(self, backend: Backend) -> Backend:
        self.backends[backend.language] = backend
        return backend

    def declare_pair(self, pair: LanguagePair) -> LanguagePair:
        self.pairs[pair.key] = pair
        return pair

    def source_languages(self) -> list[str]:
        return sorted(self.frontends)

    def target_languages(self) -> list[str]:
        return sorted(self.backends)

    def available_pairs(self) -> list[LanguagePair]:
        """Pairs whose frontend and backend are both registered and runnable."""
        return [
            pair
            for pair in self.all_pairs()
            if pair.level.runnable
            and pair.source in self.frontends
            and pair.target in self.backends
        ]

    def all_pairs(self) -> list[LanguagePair]:
        return sorted(self.pairs.values(), key=lambda pair: pair.key)

    def find_pair(self, source: str, target: str) -> LanguagePair | None:
        return self.pairs.get(f"{source}->{target}")

    def is_available(self, source: str, target: str) -> bool:
        pair = self.find_pair(source, target)
        return bool(
            pair and pair.level.runnable and source in self.frontends and target in self.backends
        )

    def resolve(self, source: str, target: str) -> tuple[Frontend, Backend, LanguagePair]:
        """Return the components for a pair, or explain precisely why not."""
        pair = self.find_pair(source, target)
        if pair is None:
            raise UnsupportedPairError(
                f"no declared migration path from {source} to {target}; "
                f"available: {', '.join(p.key for p in self.available_pairs()) or 'none'}"
            )
        if not pair.level.runnable:
            raise UnsupportedPairError(
                f"{pair.key} is declared but not usable yet (status: {pair.level.value})"
            )
        if source not in self.frontends:
            raise UnsupportedPairError(f"no registered frontend for source language {source!r}")
        if target not in self.backends:
            raise UnsupportedPairError(f"no registered backend for target language {target!r}")
        return self.frontends[source], self.backends[target], pair

    def language_for_extension(self, extension: str) -> str | None:
        suffix = extension if extension.startswith(".") else f".{extension}"
        for frontend in self.frontends.values():
            if suffix.lower() in frontend.file_extensions:
                return frontend.language
        return None

    def support_matrix(self) -> dict[str, Any]:
        """A JSON-serialisable description of everything on offer."""
        return {
            "sources": [
                {
                    "language": frontend.language,
                    "displayName": frontend.display_name,
                    "extensions": list(frontend.file_extensions),
                    "supportedConstructs": list(frontend.supported_constructs),
                    "knownLimitations": list(frontend.known_limitations),
                }
                for frontend in sorted(self.frontends.values(), key=lambda f: f.language)
            ],
            "targets": [
                {
                    "language": backend.language,
                    "displayName": backend.display_name,
                    "extension": backend.file_extension,
                    "supportedConstructs": list(backend.supported_constructs),
                    "knownLimitations": list(backend.known_limitations),
                    "requiredToolchains": list(backend.required_toolchains),
                }
                for backend in sorted(self.backends.values(), key=lambda b: b.language)
            ],
            "pairs": [pair.to_dict() for pair in self.all_pairs()],
        }


#: The process-wide registry.  Adapters register themselves on import.
registry = LanguageRegistry()
