# Architecture

CodeWeaver is a pipeline with one hinge: the intermediate representation.
Language-specific code lives on either side of it and nowhere else.

```text
 project on disk
       |
       v
 project_intake ....... scan, ignore rules, path containment, manifests
       |
       v
 frontends/<lang> ..... source text -> IR            (language-specific)
       |
       v
 ir ................... versioned schema, validation, serialisation
       |
       +--> graph ...... symbol index, dependency graph, SCCs, impact
       |
       +--> planner .... ordered groups, risk labels, required toolchains
       |
       v
 transforms ........... deterministic IR -> IR passes
       |
       v
 backends/<lang> ...... IR -> target source + project manifests  (language-specific)
       |
       v
 validation ........... local toolchains, execution policy, tsc runner
       |
       v
 reporting ............ one report, rendered as text, Markdown or JSON
```

`pipeline.py` is the only module that knows this order. The CLI
(`cli/`) and the UI (`ui/`) both call it, which is why they cannot disagree
about what a run produced.

## Why an IR at all

A direct translator needs one implementation per source/target pair: *n×m*.
Routing through an IR makes it *n+m*, and more importantly it puts the semantic
decisions in one place where they can be tested without a target language in
the picture. Every IR node has unit tests that never mention TypeScript.

The IR is a tree of dataclasses, not rendered text. Nothing in the pipeline may
pass target source where IR is expected; the schema validator would reject it,
and `tests/security/test_no_network.py` would not be the test that caught it.

## The rules that shape the code

**Nothing is silently dropped.** A construct a frontend cannot map becomes an
`IRUnsupported` node — a real part of the tree, with the original source text,
a reason and a hint. Backends emit a `CODEWEAVER-UNSUPPORTED` marker for it and
the report counts it. This is why a migration can be `incomplete`: the output
exists and is honestly labelled as partial.

**Skipping is not passing.** `CheckResult` has four outcomes and no way to
collapse `skipped` into `passed`. A report where no check ran is `incomplete`,
never `passed` — see `MigrationReport.status`.

**Determinism.** Same source, same configuration, same tool versions, same
bytes out. Modules are generated in name order, JSON is written with sorted
keys, and the code writer is the only thing that makes layout decisions.

**Transforms return new trees.** A pass never mutates its input, so running the
pipeline twice over the same IR produces the same IR and a resumed migration
cannot drift from a fresh one.

**Paths are checked once, centrally.** Everything the tool reads or writes goes
through `project_intake/paths.py`. A symlink out of the project is skipped and
reported, not followed.

**Execution is opt-in by class.** `ExecutionPolicy` separates type checking
(reads files) from running tests (executes generated code) from installing
packages (needs the network). The first is on by default; the others are not.

## Where the language-specific knowledge lives

| Decision | Lives in |
| -------- | -------- |
| What Python syntax means | `frontends/python/` |
| What a Python annotation maps to | `frontends/python/annotations.py` |
| How TypeScript spells a type | `backends/typescript/types.py` |
| Operator precedence in the output | `backends/typescript/expressions.py` |
| Python builtins → JavaScript | `backends/typescript/builtins.py` |
| Python exceptions → JS errors | `backends/typescript/exceptions.py` |
| Project-wide facts a backend needs | `transforms/` |

The last row is the interesting one. Some facts cannot be derived from a single
module or from the expression alone:

- `Account("a")` is a *call* in Python and a *construction* in TypeScript, so
  `transforms/construction.py` marks the calls it can prove are constructors
  using the symbol index, and the backend reads that mark.
- A subclass must not re-declare a field its base class declares, so
  `transforms/inheritance.py` resolves the hierarchy across modules.
- `len(x)` generates three different things depending on what `x` is, so
  `transforms/types.py` copies declared types onto the references that use
  them.

Each of these was found by running `tsc` over generated output, not by
reasoning about it.

## Adding a language

A new **frontend** implements the `Frontend` protocol in
`language_registry/registry.py`: a `parse_module(source, path)` that returns a
module plus diagnostics, and honest `supported_constructs` and
`known_limitations` tuples. It must produce `IRUnsupported` for anything it
cannot map.

A new **backend** implements `Backend`: `generate_module(module)`, a
`file_extension`, `required_toolchains`, and the same two honesty tuples.

Then declare the pair in `adapters.py`. A pair stays `PARTIAL` until its
coverage justifies otherwise, and it needs a fixture project under
`tests/fixtures/` whose generated output passes the target toolchain.

## Package boundaries

`diagnostics`, `errors` and `ir` sit at the bottom and import nothing from
above them. `backends` do not import `frontends`. `graph`, `planner` and
`reporting` read IR but never produce it. The one deliberate exception is
documented where it occurs: `ir/base.py` owns the `CONSTRUCTS_CLASS` metadata
key, because it is an IR contract that both a transform and a backend need.
