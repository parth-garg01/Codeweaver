"""Identifier spelling for Java output.

Python names are kept where Java allows them.  Three kinds change:

* Java keywords and literals (``new``, ``class``, ``null``) gain a trailing
  underscore;
* a project class whose name would shadow a type the generated code relies on
  (``List``, ``Map``, ``String``, ``Py``) gains a trailing underscore, so the
  wildcard ``java.util`` import keeps meaning what the generator assumes;
* dunder methods with a Java meaning get the Java name (``__str__`` becomes
  ``toString``); the others get a readable ``name_`` form.
"""

from __future__ import annotations

__all__ = [
    "DUNDER_METHODS",
    "RESERVED_WORDS",
    "class_identifier",
    "method_name",
    "module_class_name",
    "safe_identifier",
]

RESERVED_WORDS: frozenset[str] = frozenset(
    [
        "abstract",
        "assert",
        "boolean",
        "break",
        "byte",
        "case",
        "catch",
        "char",
        "class",
        "const",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extends",
        "final",
        "finally",
        "float",
        "for",
        "goto",
        "if",
        "implements",
        "import",
        "instanceof",
        "int",
        "interface",
        "long",
        "native",
        "new",
        "package",
        "private",
        "protected",
        "public",
        "return",
        "short",
        "static",
        "strictfp",
        "super",
        "switch",
        "synchronized",
        "this",
        "throw",
        "throws",
        "transient",
        "try",
        "void",
        "volatile",
        "while",
        "true",
        "false",
        "null",
        "var",
        "yield",
        "record",
        "sealed",
        "permits",
        "non_sealed",
        "_",
        "args",
    ]
)

#: Type names the generated code refers to without qualification.
RESERVED_TYPES: frozenset[str] = frozenset(
    [
        "Object",
        "String",
        "Long",
        "Double",
        "Boolean",
        "Integer",
        "Character",
        "Math",
        "System",
        "Objects",
        "List",
        "Map",
        "Set",
        "ArrayList",
        "LinkedHashMap",
        "LinkedHashSet",
        "Arrays",
        "Collections",
        "Iterator",
        "Iterable",
        "Comparator",
        "Py",
        "Function",
        "Supplier",
        "Consumer",
        "Runnable",
        "BiFunction",
        "BiConsumer",
        "Predicate",
        "Exception",
        "RuntimeException",
        "Error",
        "Throwable",
        "Override",
        "Record",
        "Thread",
        "Number",
        "Comparable",
        "Collection",
        "Optional",
        "Entry",
    ]
)

DUNDER_METHODS: dict[str, str] = {
    "__str__": "toString",
    "__repr__": "repr_",
    "__len__": "len_",
    "__eq__": "eq_",
    "__ne__": "ne_",
    "__lt__": "lt_",
    "__le__": "le_",
    "__gt__": "gt_",
    "__ge__": "ge_",
    "__hash__": "hash_",
    "__contains__": "contains_",
    "__getitem__": "getItem_",
    "__setitem__": "setItem_",
    "__iter__": "iter_",
    "__bool__": "bool_",
    "__call__": "call_",
}


def safe_identifier(name: str) -> str:
    if name in RESERVED_WORDS:
        return f"{name}_"
    if name.startswith("__") and name.endswith("__") and len(name) > 4:
        return DUNDER_METHODS.get(name, f"{name.strip('_')}_")
    if name.startswith("__"):
        # Python's name-mangled private: Java has real access control.
        return f"{name.lstrip('_')}_"
    return name


def method_name(name: str) -> str:
    return safe_identifier(name)


def class_identifier(name: str) -> str:
    if name in RESERVED_TYPES or name in RESERVED_WORDS:
        return f"{name}_"
    return name


def module_class_name(module: str, taken: set[str]) -> str:
    """The class holding a module's functions and variables: ``pkg.text_utils`` -> ``TextUtils``."""
    leaf = module.rsplit(".", 1)[-1] or "module"
    if leaf == "__init__":
        leaf = module.rsplit(".", 2)[-2] if module.count(".") else "package"
    pascal = "".join(part[:1].upper() + part[1:] for part in leaf.split("_") if part) or "Module"
    if pascal[0].isdigit():
        pascal = f"M{pascal}"
    pascal = class_identifier(pascal)
    while pascal in taken:
        pascal = f"{pascal}Module"
    return pascal
