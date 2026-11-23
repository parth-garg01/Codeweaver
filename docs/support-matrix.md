# Support matrix

CodeWeaver only advertises language pairs that are implemented end to end:
parsing, IR mapping, code generation and target-toolchain validation.

| Source | Target | Status | Notes |
| ------ | ------ | ------ | ----- |
| Python 3.11+ | TypeScript 5 | partial | Documented construct subset. Unsupported constructs are reported, never approximated. |
| Python 3.11+ | C++20 | partial | CMake project; header-only runtime; validated with a C++20 compiler. |
| Python 3.11+ | Java 17 | partial | Maven project; single-file runtime; validated with `javac`. |
| Python 3.11+ | C11 | partial | CMake project; header-only runtime; validated with a C11 compiler. Narrowest subset. |

Every target reports a construct it cannot express the same way: a
diagnostic, a `CODEWEAVER-UNSUPPORTED` marker in the generated source, and an
entry in the report, which makes the migration **incomplete** (exit code 3).
That holds whether the frontend or the backend found the gap, so a `dict` that
C cannot represent counts just like a `match` statement no target supports.

The TypeScript project is checked with `tsc --noEmit` under `strict`. The one
strictness option left off is `noUncheckedIndexedAccess`: it models a language
where `xs[i]` may be `undefined`, whereas Python raises `IndexError`, so
enabling it would flag every faithful index expression. Turning it on is a
reasonable step for a human hardening the output.

`partial` is not a softer word for `supported`: it means a deliberately scoped
set of constructs is translated and everything else produces an explicit
diagnostic plus a marker in the generated source.

Run `codeweaver languages` for the live matrix, which is generated from the
adapters themselves rather than from this document.

## Python → TypeScript

### Translated

- Modules, module docstrings, absolute and relative imports
- Module-level constants (`UPPER_CASE` becomes `const`) and variables
- Functions and `async` functions, with parameter types, defaults, `*args`
- Classes with single inheritance, class and instance fields, methods,
  constructors (including `super().__init__`), `@staticmethod`, `@property`,
  `@abstractmethod`
- Arithmetic, bitwise, shift, logical and comparison operators; `//` is
  lowered to `Math.floor`; `is` maps to `===`
- Chained comparisons, conditional expressions, lambdas
- Calls, attribute access, indexing, slicing without a step
- `list`, `tuple`, `set` and `dict` literals; f-strings without format specs
- Single-clause list, set and dict comprehensions, generated as
  `.filter(...).map(...)` chains
- Destructuring assignment of names (`a, b = b, a`), lowered by the frontend
  to element-wise assignments with temporaries where the order matters
- Builtins with a faithful counterpart: `len`, `print`, `str`, `int`, `float`,
  `bool`, `abs`, `round`, `list`, `tuple`, `set`, `sum`, `sorted`,
  `isinstance`, `min`, `max`
- Builtin exceptions mapped onto JavaScript error constructors, with `new` at
  every `raise` site
- Instance fields that Python only ever assigns, declared on the class
- `if`/`elif`/`else`, `while`, `for ... in ...`, `break`, `continue`
- `try`/`except`/`else`/`finally`, `raise`, `raise ... from ...`, `assert`

### Reported, not translated

Each of these produces a diagnostic and a `CODEWEAVER-UNSUPPORTED` marker in
the generated file:

| Construct | Reason |
| --------- | ------ |
| Multi-clause comprehensions, generator expressions | Not lowered by this build |
| `with` / `async with` | No direct equivalent; needs explicit try/finally |
| `match` | No structural pattern matching in the target |
| Nested `def` / `class` | Not lifted to module scope |
| Starred or nested destructuring, tuple `for` targets | Needs explicit element access |
| `global` / `nonlocal` | No equivalent scoping |
| `yield` / `yield from` | Generators are not modelled |
| Walrus `:=` | No equivalent in an expression position |
| `async for` | No asynchronous iteration mapping |
| Imports inside a function body | Not hoisted |
| `a @ b` | No matrix-multiply operator |
| Stepped slices `xs[::2]` | No equivalent slice form |
| `del x` on a binding | No equivalent |
| `enumerate`, `zip`, `map`, `filter`, `reversed`, `iter`, `next` | Depend on Python's iterator protocol |
| `getattr` / `setattr` / `hasattr` | Dynamic attribute access is not modelled |
| `eval` / `exec` | Dynamic evaluation is deliberately never generated |

### Behaviour differences the report always flags

These translate, but not identically. The migration report lists every
occurrence, because a passing `tsc` run does not make them safe:

- **Integer arithmetic** becomes IEEE-754 double arithmetic. Large integers
  lose precision and `/` is no longer exact.
- **Keyword arguments** become a trailing options object; the callee must
  accept one.
- **`try`/`else`** bodies move inside the `try` block, so exceptions they raise
  become catchable.
- **`while`/`else`** and **`for`/`else`** bodies are not emitted at all.
- **Chained comparisons** evaluate the middle operand twice.
- **Membership tests** (`x in xs`) assume an array or string unless the
  container's type is known.
- **Multiple inheritance** keeps only the first base class.
- **`ValueError`, `KeyError`, `RuntimeError` and friends** all become `Error`,
  so a `catch` can no longer tell them apart.
- **A class extending a builtin exception** extends `Error` instead, so the
  hierarchy above it differs.
- **`sorted()`** becomes `[...xs].sort()`, which compares elements as strings
  unless a comparator is supplied.
- **`int()`** truncates toward zero and accepts inputs Python's `int()` rejects.
- **`round()`** rounds halves up; Python rounds halves to even.
- **`isinstance(x, int)`** becomes a `typeof number` test and cannot tell
  integers from floats.

## Python → native targets (C++, Java, C)

The three native backends share the frontend, the type inference pass and the
analyses in `backends/common/`, so they agree on what a construct means. Every
statement that is translated keeps Python's semantics exactly unless it is
listed under *behaviour differences*; `tests/integration/test_native_targets.py`
builds the fixtures with warnings as errors and requires byte-identical output
against CPython.

### Translated by all three

- Modules with imports, module-level variables and statements (run once, in
  import order, at start-up in Java and C)
- Functions with typed parameters, defaults and keyword arguments matched to
  the callee's signature
- Classes with single inheritance, fields, methods and `super().__init__`;
  dynamic dispatch only for methods that are overridden somewhere
- `/`, `//`, `%`, `**` with Python's semantics (floor division and modulo round
  toward negative infinity; `/` is always true division)
- Negative and bounds-checked indexing; slices with steps on lists
- `for`/`else` and `while`/`else` with exact semantics; a loop variable that is
  read after its loop keeps its last value
- f-strings, `str()`, `repr()` and `print()` with Python's formatting,
  including the shortest round-tripping float repr (`0.1 + 0.2` prints
  `0.30000000000000004`, `1e20` prints `1e+20`)
- List comprehensions; `len`, `print`, `str`, `int`, `float`, `bool`, `abs`,
  `round` (halves to even), `min`, `max`, `sum`, `sorted`, `list`, `range`
- Common `str` and `list` methods; `math` functions and constants
- `raise` of builtin and project exception classes; an uncaught exception
  prints its type and message and exits with status 1

### Additionally translated by C++ and Java

- `dict` (insertion ordered), `set` and their methods and comprehensions
- `try`/`except`/`else`/`finally` with exact `else` and `finally` ordering;
  exception classes and hierarchies (Java maps builtins onto its own, e.g.
  `ValueError` → `IllegalArgumentException`)
- `isinstance`, `any`, `all`; lambdas (Java captures copies of reassigned
  locals explicitly)
- C++: unannotated parameters become template parameters

### Reported, not translated (native)

| Construct | C++ | Java | C |
| --------- | --- | ---- | - |
| `dict`, `set`, `tuple` values | translated | translated | reported |
| `try`/`except` | translated | translated | reported |
| `try`/`finally` without handlers | translated | translated | translated (finally is skipped if the body raises, which ends the program) |
| Lambdas, nested functions, closures | lambdas only | lambdas only | reported |
| Generators, `async`, `with`, `match` | reported | reported | reported |
| Values whose type inference cannot determine | `auto`/templates | `Object` | reported |
| Multiple inheritance | first base only | first base only | first base only |

In C, a function that contains a gap is replaced by a stub that stops with
`NotImplementedError`, and the partial translation is kept beneath it in an
`#if 0` block for review, so the project still compiles and the gap is visible.

### Behaviour differences the report flags

- **int** is a 64-bit signed integer in all three; Python's arbitrary
  precision is not modelled, so overflow wraps instead of growing.
- **C++ containers have value semantics**: assigning a list to a second name
  copies it. Java and C share references, as Python does.
- **C never frees memory**; define `CW_ALLOC` (for example as Boehm GC's
  `GC_MALLOC`) for long-running programs.
- **`/=` on an int variable** keeps the integer type in the target.
