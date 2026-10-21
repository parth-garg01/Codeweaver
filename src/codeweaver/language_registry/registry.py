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

from codeweaver.diagnostics import DiagnosticBag
from codeweaver.errors import UnsupportedPairError
from codeweaver.ir.declarations import IRModule

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
class ParseResult(Protocol):
    """What a frontend returns for one source file.

    Declared structurally so the registry does not have to import any
    frontend, which would make the dependency run the wrong way.
    """

    @property
    def module(self) -> IRModule | None:
        """The parsed module, or ``None`` when parsing failed outright."""
        ...

    @property
    def diagnostics(self) -> DiagnosticBag:
        """Everything the frontend wants reported, including unsupported nodes."""
        ...


@runtime_checkable
class GeneratedFile(Protocol):
    """What a backend returns for one IR module."""

    @property
    def path(self) -> str:
        """Output path, relative to the output directory."""
        ...

    @property
    def contents(self) -> str: ...

    @property
    def mappings(self) -> list[dict[str, Any]]:
        """Output-line to source-line records, for mapping errors back."""
        ...

    @property
    def diagnostics(self) -> DiagnosticBag: ...

    @property
    def unsupported_count(self) -> int: ...


@runtime_checkable
class Frontend(Protocol):
    """A source-language frontend: source text in, IR modules out.

    The descriptive members are read-only properties rather than mutable
    attributes.  A mutable protocol attribute is invariant, so an
    implementation declaring ``file_extensions: tuple[str, str]`` — which is
    what a concrete adapter naturally writes — would not satisfy
    ``tuple[str, ...]``.  Read-only properties are covariant, and an ordinary
    attribute satisfies one, so a normal dataclass adapter type-checks.
    """

    @property
    def language(self) -> str:
        """Registry key, for example ``"python"``."""
        ...

    @property
    def display_name(self) -> str: ...

    @property
    def file_extensions(self) -> tuple[str, ...]:
        """Extensions this frontend parses, including the leading dot."""
        ...

    @property
    def supported_constructs(self) -> tuple[str, ...]: ...

    @property
    def known_limitations(self) -> tuple[str, ...]:
        """What this frontend reports rather than translating.  Not optional."""
        ...

    def parse_module(self, source: str, path: str) -> ParseResult:
        """Parse *source*, which is already decoded text, into IR.

        *path* is project-relative and is used for source spans; the frontend
        must not read it from disk.
        """
        ...


@runtime_checkable
class Backend(Protocol):
    """A target-language backend: IR in, target files out."""

    @property
    def language(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def file_extension(self) -> str: ...

    @property
    def supported_constructs(self) -> tuple[str, ...]: ...

    @property
    def known_limitations(self) -> tuple[str, ...]: ...

    @property
    def required_toolchains(self) -> tuple[str, ...]:
        """Executables needed to validate this backend's output."""
        ...

    def generate_module(
        self,
        module: IRModule,
        layout: Any = None,
        *,
        diagnostics: DiagnosticBag | None = None,
    ) -> GeneratedFile:
        """Generate the target file for *module*.

        *layout* carries where every module in the project will be written, so
        that a cross-module import can be spelled relatively.  It is the
        backend's own layout type, which is why it is untyped here: the
        registry has no reason to know it.  Passing ``None`` means "this module
        alone", which is only correct when it has no project imports.

        *diagnostics* is the run's shared bag.  A backend appends to it rather
        than returning a second one, so the migration report sees every
        diagnostic in the order it was produced.
        """
        ...

    def generate_project(
        self,
        modules: list[IRModule],
        *,
        project_name: str = "codeweaver-output",
        diagnostics: DiagnosticBag | None = None,
    ) -> dict[str, str]:
        """Generate every file of the target project, keyed by output path.

        Includes whatever manifests the target ecosystem needs, not just the
        translated sources.
        """
        ...

    def output_path(self, source_path: str) -> str:
        """The output path for a source path, without generating anything."""
        ...

    def layout_for(self, modules: list[IRModule]) -> Any:
        """Where every module will be written, for resolving cross-module imports.

        Built once for the whole project and passed back to
        :meth:`generate_module`.  The pipeline needs the per-module call rather
        than :meth:`generate_project` so that it can report progress, honour
        cancellation and collect source-map metadata — so it needs a way to
        build the layout the modules share.

        The return type is the backend's own layout type; the registry has no
        reason to know it.
        """
        ...

    def project_files(self, project_name: str) -> dict[str, str]:
        """Manifests and other non-source files the target ecosystem needs."""
        ...


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
