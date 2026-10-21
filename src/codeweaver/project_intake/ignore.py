"""Ignore rules for project scanning.

CodeWeaver honours three layers, applied in order of increasing precedence:

1. built-in defaults for build output, caches, virtual environments and
   vendored dependencies;
2. ``.gitignore`` files found in the project;
3. ``.codeweaverignore``, which is the user's final say.

The matcher implements the subset of the gitignore format that matters for
source discovery: comments, blank lines, negation with ``!``, directory-only
patterns ending in ``/``, anchored patterns containing ``/``, and the ``*``,
``?``, ``[...]`` and ``**`` wildcards.  Unsupported exotica (such as
``\\``-escaped leading ``#``) is treated as a literal pattern rather than being
silently dropped.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from codeweaver.project_intake.textio import read_project_text

__all__ = [
    "DEFAULT_IGNORE_PATTERNS",
    "IgnoreRule",
    "IgnoreRules",
    "load_ignore_rules",
]

#: Directories and files that are never source input.  Keeping this list
#: explicit means a scan of a working checkout does not pick up a virtual
#: environment or a previous migration's output.
DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    ".git/",
    ".hg/",
    ".svn/",
    "__pycache__/",
    "*.py[cod]",
    ".venv/",
    "venv/",
    "env/",
    "node_modules/",
    "dist/",
    "build/",
    "target/",
    "out/",
    ".tox/",
    ".nox/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pytest_cache/",
    ".idea/",
    ".vscode/",
    ".DS_Store",
    "*.egg-info/",
    ".codeweaver/",
    "_generated/",
)


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    """A single compiled ignore pattern."""

    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool
    source: str

    @classmethod
    def parse(cls, line: str, source: str) -> IgnoreRule | None:
        raw = line.rstrip("\n")
        if not raw.strip() or raw.lstrip().startswith("#"):
            return None
        negated = raw.startswith("!")
        if negated:
            raw = raw[1:]
        raw = raw.strip()
        if not raw:
            return None
        directory_only = raw.endswith("/")
        if directory_only:
            raw = raw[:-1]
        anchored = "/" in raw
        return cls(
            pattern=raw.lstrip("/"),
            negated=negated,
            directory_only=directory_only,
            anchored=anchored,
            source=source,
        )

    def matches(self, relative: str, *, is_dir: bool) -> bool:
        if self.directory_only and not is_dir:
            return False
        if self.anchored:
            return _match_path(relative, self.pattern)
        parts = PurePosixPath(relative).parts
        if any(fnmatch.fnmatch(part, self.pattern) for part in parts):
            return True
        return _match_path(relative, self.pattern)


def _match_path(relative: str, pattern: str) -> bool:
    """Match *relative* against *pattern*, treating ``**`` as "any depth"."""
    if fnmatch.fnmatch(relative, pattern):
        return True
    if "**" not in pattern:
        return False
    head, _, tail = pattern.partition("**")
    head = head.rstrip("/")
    tail = tail.lstrip("/")
    if head and not (relative == head or relative.startswith(f"{head}/")):
        return False
    remainder = relative[len(head) :].lstrip("/") if head else relative
    if not tail:
        return True
    candidate = remainder
    while True:
        if fnmatch.fnmatch(candidate, tail):
            return True
        _, sep, candidate = candidate.partition("/")
        if not sep:
            return False


@dataclass
class IgnoreRules:
    """An ordered rule set; the last matching rule wins, as in git."""

    rules: list[IgnoreRule] = field(default_factory=list)

    def add_patterns(self, patterns: list[str] | tuple[str, ...], source: str) -> None:
        for line in patterns:
            rule = IgnoreRule.parse(line, source)
            if rule is not None:
                self.rules.append(rule)

    def add_file(self, path: Path, *, relative_to: Path) -> bool:
        """Load patterns from *path*.  Returns ``True`` when the file existed."""
        if not path.is_file():
            return False
        try:
            text = read_project_text(path, errors="replace")
        except OSError:
            return False
        try:
            source = str(path.relative_to(relative_to))
        except ValueError:
            source = path.name
        self.add_patterns(text.splitlines(), source)
        return True

    def decide(self, relative: str, *, is_dir: bool) -> IgnoreRule | None:
        """Return the rule that decided *relative*, or ``None`` if unmatched."""
        decision: IgnoreRule | None = None
        for rule in self.rules:
            if rule.matches(relative, is_dir=is_dir):
                decision = rule
        return decision

    def is_ignored(self, relative: str, *, is_dir: bool = False) -> bool:
        """Return ``True`` when *relative* is excluded from the scan.

        A path under an excluded directory is excluded even if a later rule
        appears to re-include it, matching git's behaviour: once a directory is
        pruned its contents are never visited.
        """
        parts = PurePosixPath(relative).parts
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            decision = self.decide(ancestor, is_dir=True)
            if decision is not None and not decision.negated:
                return True
        decision = self.decide(relative, is_dir=is_dir)
        return decision is not None and not decision.negated

    def __len__(self) -> int:
        return len(self.rules)


def load_ignore_rules(root: Path, *, use_defaults: bool = True) -> IgnoreRules:
    """Build the rule set for *root* from defaults, ``.gitignore`` and ours."""
    rules = IgnoreRules()
    if use_defaults:
        rules.add_patterns(DEFAULT_IGNORE_PATTERNS, "built-in defaults")
    rules.add_file(root / ".gitignore", relative_to=root)
    rules.add_file(root / ".codeweaverignore", relative_to=root)
    return rules
