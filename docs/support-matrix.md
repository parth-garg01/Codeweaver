# Support matrix

CodeWeaver only advertises language pairs that are implemented end to end:
parsing, IR mapping, code generation and target-toolchain validation.

| Source | Target | Status | Validated with | Notes |
| ------ | ------ | ------ | -------------- | ----- |
| Python 3.11+ | TypeScript 5 | partial | `tsc --noEmit` (strict) | Documented construct subset. Unsupported constructs are reported, never approximated. |
| Python 3.11+ | JavaScript (ES2022) | partial | `node --check` | Same construct coverage with annotations erased. **The check is weaker:** a syntax check cannot see a type error. |

### A word on what "validated" means per target

The two targets share a code generator, so they translate the same constructs.
They do **not** give you the same confidence:

- TypeScript output is type-checked under `strict`, so a passing run means the
  translation is internally consistent — the wrong type reaching the wrong
  parameter is caught.
- JavaScript output is only *parsed*. `node --check` proves the file is
  syntactically valid and nothing more. Every type error the TypeScript target
  would have caught passes silently here.

If you have a choice, migrate to TypeScript and strip the types afterwards. The
JavaScript target exists for projects that cannot take the dependency.

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
