"""Identifier spelling for C output.

C has one namespace for every function, global and type tag in a program, so
top-level names are made unique across the whole project: a name keeps its
Python spelling when it is unique and does not collide with C or its
standard library, and is prefixed with its module otherwise
(``algo.py: index`` -> ``algo_index``).
"""

from __future__ import annotations

__all__ = ["DUNDER_METHODS", "RESERVED_WORDS", "safe_identifier"]

RESERVED_WORDS: frozenset[str] = frozenset(
    """auto break case char const continue default do double else enum extern float for goto if
    inline int long register restrict return short signed sizeof static struct switch typedef
    union unsigned void volatile while _Bool _Complex _Imaginary _Alignas _Alignof _Atomic
    _Generic _Noreturn _Static_assert _Thread_local bool true false NULL main errno assert
    abs labs div exit free malloc calloc realloc abort atexit atoi atof atol strtol strtod
    rand srand qsort bsearch getenv system printf fprintf sprintf snprintf scanf puts putchar
    getchar fopen fclose fread fwrite fgets fputs remove rename tmpfile strlen strcpy strncpy
    strcat strncat strcmp strncmp strchr strrchr strstr strtok memcpy memmove memset memcmp
    index rindex time clock difftime mktime sqrt pow exp log log2 log10 sin cos tan asin acos
    atan atan2 floor ceil round trunc fabs fmod hypot isnan isinf signbit max min stdin stdout
    stderr signal raise select read write open close link unlink stat wait kill sleep
    self base""".split()  # noqa: SIM905 - a word list reads better than 150 quoted strings
)

DUNDER_METHODS: dict[str, str] = {
    "__init__": "init",
    "__str__": "str_",
    "__repr__": "repr_",
    "__len__": "len_",
    "__eq__": "eq_",
    "__lt__": "lt_",
    "__contains__": "contains_",
}


def safe_identifier(name: str) -> str:
    if name in DUNDER_METHODS:
        return DUNDER_METHODS[name]
    if name.startswith("__") and name.endswith("__") and len(name) > 4:
        return f"{name.strip('_')}_"
    if name.startswith("__"):
        name = f"{name.lstrip('_')}_"
    if name.startswith("_") and len(name) > 1 and name[1].isupper():
        name = f"u{name}"
    if name in RESERVED_WORDS or name.startswith("cw_"):
        return f"{name}_"
    return name
