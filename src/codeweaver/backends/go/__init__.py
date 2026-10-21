"""Go target backend."""

from __future__ import annotations

from codeweaver.backends.go.backend import GO_VERSION, GoBackend, render_go_mod
from codeweaver.backends.go.module import (
    GeneratedGoFile,
    GoLayout,
    generate_module,
    output_path_for,
    package_name_for,
)
from codeweaver.backends.go.naming import exported_name, unexported_name
from codeweaver.backends.go.types import render_type

__all__ = [
    "GO_VERSION",
    "GeneratedGoFile",
    "GoBackend",
    "GoLayout",
    "exported_name",
    "generate_module",
    "output_path_for",
    "package_name_for",
    "render_go_mod",
    "render_type",
    "unexported_name",
]
