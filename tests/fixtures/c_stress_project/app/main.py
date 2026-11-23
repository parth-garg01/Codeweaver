"""Regression programs in the C subset, compared with CPython on every native target."""

from app import algorithms
from app import objects
from app import workload
from app import formatting
from app import strings

if __name__ == "__main__":
    print("--", "algorithms")
    algorithms.run()
    print("--", "objects")
    objects.run()
    print("--", "workload")
    workload.run()
    print("--", "formatting")
    formatting.run()
    print("--", "strings")
    strings.run()
