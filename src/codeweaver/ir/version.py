"""IR schema version.

The IR is a persisted contract: checkpoints on disk and reports reference the
schema they were produced with.  Bump ``IR_SCHEMA_VERSION`` whenever a node's
field set changes, and record the reason here so that old checkpoints can be
rejected with a useful message instead of being misread.
"""

from __future__ import annotations

__all__ = [
    "IR_SCHEMA_VERSION",
    "SCHEMA_HISTORY",
    "is_compatible",
]

#: Semantic version of the IR node schema.
IR_SCHEMA_VERSION = "1.2.0"

#: version -> what changed.
SCHEMA_HISTORY: dict[str, str] = {
    "1.0.0": "Initial schema: modules, declarations, statements, expressions, types.",
    "1.1.0": "IRField gains 'optional' for fields with no guaranteed initial value.",
    "1.2.0": "IRFunction gains 'accessor' so a property getter/setter pair is one property.",
}


def is_compatible(version: str) -> bool:
    """Return ``True`` when IR written as *version* can be read by this build.

    Compatibility is major-version based: a reader accepts any schema with the
    same major version and a minor/patch version no greater than its own.
    """
    try:
        found = tuple(int(part) for part in version.split("."))
        current = tuple(int(part) for part in IR_SCHEMA_VERSION.split("."))
    except ValueError:
        return False
    if len(found) != 3 or found[0] != current[0]:
        return False
    return found <= current
