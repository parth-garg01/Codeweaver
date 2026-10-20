"""Path containment checks.

A selected project root is untrusted input.  It may contain symlinks that point
outside the root, absolute symlinks, or ``..`` components introduced by a
manifest.  Every path that CodeWeaver reads or writes passes through this
module first, so that a single implementation decides what "inside the project"
means.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from codeweaver.errors import CodeWeaverError

__all__ = [
    "PathSafetyError",
    "contains_path",
    "normalise_root",
    "resolve_within",
    "safe_relative_path",
]


class PathSafetyError(CodeWeaverError):
    """A path escapes its permitted root, or is otherwise unsafe to use."""


def normalise_root(root: Path | str) -> Path:
    """Return *root* as an absolute, symlink-resolved directory path."""
    resolved = Path(root).expanduser().resolve()
    if not resolved.exists():
        raise PathSafetyError(f"project root does not exist: {resolved}")
    if not resolved.is_dir():
        raise PathSafetyError(f"project root is not a directory: {resolved}")
    return resolved


def contains_path(root: Path, candidate: Path) -> bool:
    """Return ``True`` when *candidate* resolves to a location inside *root*.

    Both arguments are fully resolved first, so a symlink that points outside
    the root is correctly reported as *not* contained even when its own path
    lives inside the root.
    """
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve()
    except OSError as exc:  # broken symlink, permission error, ELOOP
        raise PathSafetyError(f"cannot resolve path {candidate}: {exc}") from exc
    return root_resolved == candidate_resolved or root_resolved in candidate_resolved.parents


def resolve_within(root: Path, candidate: Path | str) -> Path:
    """Resolve *candidate* and assert that it stays inside *root*.

    ``candidate`` may be absolute or relative to *root*.  The returned path is
    always absolute and fully resolved.
    """
    root_resolved = root.resolve()
    raw = Path(candidate)
    joined = raw if raw.is_absolute() else root_resolved / raw
    if not contains_path(root_resolved, joined):
        raise PathSafetyError(f"path escapes project root: {candidate}")
    return joined.resolve()


def safe_relative_path(root: Path, candidate: Path) -> str:
    """Return *candidate* relative to *root* as a POSIX string.

    Relative POSIX strings are what the IR, the dependency graph and the
    migration report store, so that records stay comparable between machines
    and never leak absolute filesystem paths.
    """
    resolved = resolve_within(root, candidate)
    relative = resolved.relative_to(root.resolve())
    return str(PurePosixPath(relative))
