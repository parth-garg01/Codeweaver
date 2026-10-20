"""Allow ``python -m codeweaver.cli``."""

from __future__ import annotations

from codeweaver.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
