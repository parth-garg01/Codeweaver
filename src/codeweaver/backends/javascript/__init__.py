"""JavaScript target backend."""

from __future__ import annotations

from codeweaver.backends.javascript.backend import (
    JavaScriptBackend,
    project_files,
    render_package_json,
)

__all__ = ["JavaScriptBackend", "project_files", "render_package_json"]
