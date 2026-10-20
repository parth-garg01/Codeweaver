"""Constructs CodeWeaver reports rather than translating."""

import os


def read_config(path: str) -> str:
    with open(path) as handle:
        return handle.read()


def lazy_values(limit: int):
    return (index for index in range(limit))


def environment() -> str:
    return os.environ.get("HOME", "")


def swap(pairs: list[list[int]]) -> None:
    for left, right in pairs:
        print(left, right)


def pick(values: dict[str, int]) -> int:
    match values:
        case {"a": found}:
            return found
        case _:
            return 0


def counter():
    total = 0

    def bump() -> int:
        return total + 1

    return bump
