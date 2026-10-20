"""Local, content-free persistence of migration metadata."""

from __future__ import annotations

from codeweaver.persistence.store import (
    STORE_SCHEMA_VERSION,
    Checkpoint,
    CheckpointStore,
    StaleCheckpointError,
    default_store_dir,
)

__all__ = [
    "STORE_SCHEMA_VERSION",
    "Checkpoint",
    "CheckpointStore",
    "StaleCheckpointError",
    "default_store_dir",
]
