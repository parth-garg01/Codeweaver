"""Constructs CodeWeaver reports rather than translating."""

import os


def read_config(path: str) -> str:
    with open(path) as handle:
        return handle.read()


def lazy_values(limit: int):
    return (index for index in range(limit))


def environment() -> str:
    return os.environ.get("HOME", "")
