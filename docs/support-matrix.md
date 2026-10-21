# Support matrix

CodeWeaver only advertises language pairs that are implemented end to end:
parsing, IR mapping, code generation and target-toolchain validation.

| Source | Target | Status | Validated with | Notes |
| ------ | ------ | ------ | -------------- | ----- |
| Python 3.11+ | TypeScript 5 | partial | `tsc --noEmit` (strict) | Documented construct subset. Unsupported constructs are reported, never approximated. |
| Python 3.11+ | JavaScript (ES2022) | partial | `node --check` | Same construct coverage with annotations erased. **The check is weaker:** a syntax check cannot see a type error. |
| Python 3.11+ | Go 1.22+ | partial | `go build ./...` then `go vet ./...` | Classes become structs with pointer receivers. `try`/`except` is **not** translated. |

### A word on what "validated" means per target

The two targets share a code generator, so they translate the same constructs.
They do **not** give you the same confidence:

- TypeScript output is type-checked under `strict`, so a passing run means the
  translation is internally consistent — the wrong type reaching the wrong
  parameter is caught.
- JavaScript output is only *parsed*. `node --check` proves the file is
  syntactically valid and nothing more. Every type error the TypeScript target
  would have caught passes silently here.

- Go output is compiled by the Go compiler, which is stricter than most: an
  unused import, an unused variable, a missing return or a type mismatch all
  fail the build. A passing `go build` is strong evidence of internal
  consistency. `go vet` then adds format-string checking, which matters because
  f-strings become `fmt.Sprintf`.

Nothing executes the translated program. `go build` compiles, `node --check`
parses and `tsc --noEmit` type-checks; none of them run your code.

If you have a choice between the two ECMAScript targets, migrate to TypeScript
and strip the types afterwards. The JavaScript target exists for projects that
cannot take the dependency.

## Python → Go

Go is the furthest of the targets from Python, so the differences are larger
and worth reading before using the output.

### Translated

- Packages, one per source directory, with project imports resolved. Modules in
  the same directory share a package, so a Python import cycle between them
  disappears rather than becoming an illegal Go import cycle.
- Package-level constants and variables; functions with typed parameters and
  results.
- Classes as structs. Methods take a **pointer receiver**, because Python
  objects have reference semantics and a value receiver would mutate a copy.
- `__init__` as a `NewT` constructor returning a pointer; a class with no
  `__init__` still gets one, because a class call generates `NewT(...)`.
- Class attribute defaults, assigned in the constructor — a Go struct field
  cannot have a default, so the value would otherwise be silently lost.
- Property accessors as `GetX` / `SetX` methods.
- `f`-strings as `fmt.Sprintf` with `%v` holes.
- Membership tests as `slices.Contains`, `strings.Contains` or a comma-ok map
  index, chosen from the container's type.
- Mixed `int`/`float` arithmetic, with the `float64` conversion Python
  performed implicitly inserted explicitly.
- `assert` as a guarded `panic`.

### Reported, not translated

| Construct | Reason |
| --------- | ------ |
| `try` / `except` / `finally` | Go returns errors as values. `defer`/`recover` would turn ordinary control flow into panic handling and resume somewhere else. |
| `a @ b` | No matrix-multiply operator |
| Stepped slices | No equivalent slice form |
| `del x` on a binding | No equivalent |
| Keyword and spread arguments | Go has neither |

### Behaviour differences the report always flags

- **`raise` becomes `panic`**, which is not how Go reports an expected failure.
- **A builtin exception loses its type**: `ValueError("x")` becomes
  `errors.New("x")`, so a translated program can no longer tell `ValueError`
  from `KeyError`. A project's own exception class keeps its identity.
- **Inheritance becomes struct embedding.** Fields and methods are promoted,
  but there is no virtual dispatch: an overridden method is *not* called
  through a base-typed value.
- **`int` becomes a 64-bit `int`**, losing Python's arbitrary precision.
- **Integer division truncates toward zero** rather than flooring, so it
  disagrees with Python whenever an operand is negative.
- **Parameter defaults have no equivalent**; callers must pass the value.
- **A conditional expression** becomes an immediately called function.
- **Tuples become `[]any`**, losing the element types.
- **`async` functions become ordinary functions**; Go uses goroutines, so
  callers will not await them.
- **Exported names are capitalised.** This is the only renaming CodeWeaver
  does anywhere, and it is forced: Go decides visibility by the first letter.
  Every rename is listed in the report.

The generated project is checked with `tsc --noEmit` under `strict`. The one
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
| Comprehensions, generator expressions | Not lowered by this build |
| `with` / `async with` | No direct equivalent; needs explicit try/finally |
| `match` | No structural pattern matching in the target |
| Nested `def` / `class` | Not lifted to module scope |
| Destructuring assignment, tuple `for` targets | Needs explicit element access |
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
