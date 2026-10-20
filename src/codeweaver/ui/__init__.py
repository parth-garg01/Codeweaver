"""The local web UI: a JSON API plus packaged static assets."""

from __future__ import annotations

from codeweaver.ui.api import ApiError, ApiRouter
from codeweaver.ui.server import STATIC_DIR, create_server, serve

__all__ = ["STATIC_DIR", "ApiError", "ApiRouter", "create_server", "serve"]
