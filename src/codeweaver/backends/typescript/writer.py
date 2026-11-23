"""Re-export of the shared code writer; kept so existing imports keep working."""

from __future__ import annotations

from codeweaver.backends.common.writer import CodeWriter, MappingEntry

__all__ = ["CodeWriter", "MappingEntry"]
