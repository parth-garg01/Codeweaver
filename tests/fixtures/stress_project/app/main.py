"""Regression programs for the C++ and Java backends, compared with CPython."""

from app import algorithms
from app import objects
from app import collections_demo
from app import errors
from app import workload
from app import formatting
from app import strings

if __name__ == "__main__":
    print("--", "algorithms")
    algorithms.run()
    print("--", "objects")
    objects.run()
    print("--", "collections_demo")
    collections_demo.run()
    print("--", "errors")
    errors.run()
    print("--", "workload")
    workload.run()
    print("--", "formatting")
    formatting.run()
    print("--", "strings")
    strings.run()
