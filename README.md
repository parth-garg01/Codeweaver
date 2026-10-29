# CodeWeaver

Local-first, LLM-free code migration through a shared intermediate
representation.

CodeWeaver parses a source project into a typed IR, records module and symbol
relationships in a dependency graph, generates a target-language project from
that IR, and then runs the target's own toolchain over the result. There is no
LLM, no remote inference, no account and no API key. The runtime has no
third-party Python dependencies at all.

The point of the tool is not that it translates everything. It is that it tells
you exactly what it did not.

## Status

| Source | Target | Status | Validated with |
| ------ | ------ | ------ | -------------- |
| Python 3.11+ | TypeScript 5 | partial | `tsc --noEmit`, strict |
| Python 3.11+ | JavaScript (ES2022) | partial | `node --check` only |
| Python 3.11+ | Go 1.22+ | partial | `go build` + `go vet` |

Different targets earn different confidence, and the report always names the
check that actually ran: TypeScript output is type-checked, Go output is
compiled and vetted, JavaScript output is only parsed. Nothing executes your
translated program.

Go is the furthest from Python and the differences are correspondingly larger —
`try`/`except` is reported rather than translated, inheritance becomes struct
embedding without virtual dispatch, and exported names are capitalised. All of
it is listed in the support matrix.

`partial` is not a softer word for `supported`. A deliberately scoped set of
constructs is translated; everything else produces a diagnostic, a
`CODEWEAVER-UNSUPPORTED` marker in the generated file, and a line in the report.
See [`docs/support-matrix.md`](docs/support-matrix.md) for the exact lists,
including the behaviour differences that translate but are not identical.

A migration never reports `passed` unless a validation check actually ran, and
even then the report states plainly that compiling is not equivalence.

## Install

```bash
git clone https://github.com/parth-garg01/Codeweaver.git
cd Codeweaver
python3 -m pip install -e .          # optional; installs the `codeweaver` command
```

Running from a checkout works without installing:

```bash
PYTHONPATH=src python3 -m codeweaver.cli --help
```

To validate generated TypeScript you also need `tsc` on your `PATH`
(`npm install -g typescript`). Without it, validation is reported as **skipped**
and the migration as **incomplete** — never as a pass.

## Use it

```bash
codeweaver languages                 # what this build actually implements
codeweaver scan   ./my-project       # which files would be used, and what was skipped
codeweaver analyze ./my-project      # parse, index symbols, build the graph
codeweaver plan   ./my-project --to typescript
codeweaver migrate ./my-project --to typescript --out ../my-project-ts
codeweaver migrate ./my-project --to javascript --out ../my-project-js
codeweaver migrate ./my-project --to go         --out ../my-project-go
codeweaver validate ../my-project-ts
codeweaver report ./my-project --format markdown
```

Or open the local UI:

```bash
codeweaver ui                        # http://127.0.0.1:8731
```

The UI binds to the loopback interface only, refuses requests addressed
anywhere else, and sends no CORS headers. It shows the same records the CLI
prints.

### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0` | passed, or passed with warnings |
| `1` | failed, or a validation check failed |
| `2` | used incorrectly, or the environment prevents it |
| `3` | **incomplete**: unsupported constructs were left in the output, or no check ran |

`3` is the one worth wiring into CI. It means files were produced and nothing
verified them.

## What it will not do

- Guarantee behavioural equivalence. It does not try, and it says so in every
  report.
- Execute your project, your tests or any package script unless you pass
  `--allow-tests` or `--allow-install`. Type checking reads files; it does not
  run them.
- Write into your source directory, or replace files in a non-empty output
  directory without `--overwrite`.
- Reach the network during analysis, translation or validation.
  [`tests/security/test_no_network.py`](tests/security/test_no_network.py) runs
  the whole pipeline with sockets cut off and asserts it.
- Translate a construct it has no mapping for. It reports it instead.

## Develop

```bash
python3 -m pip install -e '.[dev]'
./scripts/sanity-check               # the full gate: format, lint, types, tests, privacy audit
./scripts/sanity-check --fast        # while iterating
```

[`docs/architecture.md`](docs/architecture.md) explains the pipeline and where
language-specific knowledge is allowed to live.
[`CONTRIBUTING.md`](CONTRIBUTING.md) has the workflow for a change.

## License

MIT. See [LICENSE](LICENSE).
