#!/usr/bin/env python3
"""Scan tracked files for anything that looks like a committed secret.

This is a cheap, deliberately noisy-on-purpose check, not a replacement for a
real secret scanner.  It exists because the sanity suite is the last gate
before a commit, and a committed credential is the one mistake that cannot be
undone by a later fix.

Exit codes: 0 clean, 1 findings, 2 could not run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: (name, pattern) pairs.  Each pattern targets a shape, not a vendor list, so
#: a new provider's key is still caught if it looks like a key.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    (
        "assigned credential",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|private[_-]?key)\b"
            r"\s*[:=]\s*[\"'][^\"'\n]{12,}[\"']"
        ),
    ),
]

#: Files whose whole purpose is to talk about credentials.
ALLOWED_PATHS = frozenset({"scripts/check_secrets.py"})

#: Lines marked as deliberate examples.
ALLOW_MARKER = "secret-scan: allow"

TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".json",
        ".toml",
        ".cfg",
        ".ini",
        ".md",
        ".txt",
        ".yml",
        ".yaml",
        ".html",
        ".css",
        ".sh",
        ".env",
        "",
    }
)


def tracked_files(root: Path) -> list[Path]:
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "ls-files", "-z"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check_secrets: could not list tracked files: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return [root / name for name in completed.stdout.split("\0") if name]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings: list[str] = []
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in ALLOWED_PATHS or path.suffix not in TEXT_SUFFIXES:
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            for name, pattern in PATTERNS:
                if pattern.search(line):
                    findings.append(f"{relative}:{number}: possible {name}")
    if findings:
        print("check_secrets: possible secrets in tracked files:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            f"  mark a deliberate example with a '{ALLOW_MARKER}' comment",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
