# Contributing to CodeWeaver

## Ground rules

1. **No LLM, no remote inference, no network at runtime.** Runtime code paths
   must work with the machine offline. Optional network actions (a Git clone, a
   package install) must be user-initiated and documented.
2. **No third-party runtime dependencies.** The standard library only. Dev
   tooling (`ruff`, `mypy`) lives in the `dev` extra.
3. **The IR is the contract.** Frontends produce IR, backends consume IR.
   Nothing may read rendered target source as if it were IR.
4. **Be honest in reports.** A skipped check is reported as skipped. Compilation
   passing is never reported as behavioural equivalence.

## Development setup

```bash
python3 -m pip install -e '.[dev]'
./scripts/sanity-check
```

Without an install, prefix commands with `PYTHONPATH=src`.

## Workflow for a change

1. State the change and its acceptance criteria.
2. Make the smallest coherent implementation.
3. Add or update tests in the same change.
4. Run `./scripts/sanity-check --fast` while iterating and the full suite before
   declaring a feature complete.
5. Review the full diff, including untracked files.
6. Commit with a conventional message, for example `feat(ir): add class nodes`.

Do not combine unrelated changes in one commit, and never commit a knowingly
broken feature without saying so in the commit message.

## Layout

| Path | Purpose |
| ---- | ------- |
| `src/codeweaver/ir/` | Versioned intermediate representation and its schema |
| `src/codeweaver/frontends/` | Source language → IR |
| `src/codeweaver/backends/` | IR → target language |
| `src/codeweaver/graph/` | Symbol index and dependency graph |
| `src/codeweaver/validation/` | Target toolchain runners |
| `src/codeweaver/cli/` | Command line interface |
| `src/codeweaver/ui/` | Local web UI and its JSON API |
| `tests/` | `unit`, `integration`, `regression`, `security`, `fixtures` |
