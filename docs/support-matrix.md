# Support matrix

CodeWeaver only advertises language pairs that are implemented end to end:
parsing, IR mapping, code generation and target-toolchain validation.

| Source | Target | Status | Notes |
| ------ | ------ | ------ | ----- |
| Python 3.11+ | TypeScript 5 | partial | Documented construct subset. Unsupported constructs are reported, never approximated. |

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
