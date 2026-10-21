"""Turning source names into safe target identifiers.

Every target has reserved words and a notion of which characters may appear in
an identifier, and every backend needs the same three decisions:

* leave a name alone when the target allows it — renaming ``total_count`` to
  ``totalCount`` makes the generated project harder to review against the
  original and silently breaks string-keyed access;
* rename deterministically when the target forbids the name, so two runs agree;
* decide separately what is legal as a *member* name, which is usually a larger
  set than what is legal as a binding.

:class:`IdentifierPolicy` holds the per-language data; the logic is shared.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

__all__ = ["IdentifierPolicy", "escape_double_quoted"]

_DEFAULT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def escape_double_quoted(value: str) -> str:
    """Render *value* as a double-quoted literal.

    ``json.dumps`` is used because its escaping rules are a subset of what C,
    C++, Java, Go, JavaScript and Rust accept for double-quoted strings, and it
    handles control characters and surrogate pairs correctly — which
    hand-rolled escaping routinely gets wrong.
    """
    return json.dumps(value, ensure_ascii=False)


@dataclass(frozen=True)
class IdentifierPolicy:
    """How one target language spells identifiers."""

    #: Words that cannot be used as a binding name.
    reserved: frozenset[str] = field(default_factory=frozenset)
    #: What a legal identifier looks like.
    pattern: re.Pattern[str] = _DEFAULT_PATTERN
    #: Appended to a reserved word to make it usable.
    suffix: str = "_"
    #: Prepended when a name would otherwise start with a digit.
    digit_prefix: str = "_"
    #: Characters allowed in addition to letters, digits and ``_``.
    extra_chars: str = ""

    def is_valid(self, name: str) -> bool:
        return bool(self.pattern.match(name)) and name not in self.reserved

    def identifier(self, name: str) -> str:
        """Return *name* usable as a binding name in this language."""
        if not name:
            return self.digit_prefix
        allowed = f"A-Za-z0-9_{re.escape(self.extra_chars)}"
        cleaned = re.sub(f"[^{allowed}]", "_", name)
        if cleaned[0].isdigit():
            cleaned = f"{self.digit_prefix}{cleaned}"
        if cleaned in self.reserved:
            cleaned = f"{cleaned}{self.suffix}"
        return cleaned

    def member(self, name: str) -> str:
        """Return *name* usable as a member name.

        Reserved words are usually legal as members, so only genuinely invalid
        spellings are changed.  Callers that need quoting (JavaScript object
        keys) check :meth:`is_plain_member` and quote themselves, because
        quoting changes the shape of the value rather than just its name.
        """
        return name if self.is_plain_member(name) else self.identifier(name)

    def is_plain_member(self, name: str) -> bool:
        return bool(self.pattern.match(name))

    def exported(self, name: str) -> str:
        """Return *name* as this language spells an exported member.

        The default is "unchanged"; Go overrides it, because export there is
        decided by the first letter being upper case rather than by a keyword.
        """
        return name
