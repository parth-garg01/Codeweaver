"""TypeScript target backend."""

from __future__ import annotations

from codeweaver.backends.common.writer import CodeWriter, MappingEntry
from codeweaver.backends.typescript.backend import (
    TypeScriptBackend,
    project_files,
    render_package_json,
    render_tsconfig,
)
from codeweaver.backends.typescript.module import (
    GeneratedFile,
    ModuleLayout,
    generate_module,
    output_path_for,
)

__all__ = [
    "CodeWriter",
    "GeneratedFile",
    "MappingEntry",
    "ModuleLayout",
    "TypeScriptBackend",
    "generate_module",
    "output_path_for",
    "project_files",
    "render_package_json",
    "render_tsconfig",
]
