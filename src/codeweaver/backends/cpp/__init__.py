"""C++20 target backend."""

from __future__ import annotations

from codeweaver.backends.cpp.backend import CppBackend, render_cmakelists, runtime_header
from codeweaver.backends.cpp.context import CppLayout

__all__ = ["CppBackend", "CppLayout", "render_cmakelists", "runtime_header"]
