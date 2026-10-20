# CodeWeaver

Local-first, LLM-free code migration through a shared Intermediate Representation.

CodeWeaver parses a source project into a typed IR, records module and symbol
relationships in a dependency graph, and generates a target-language project
from that IR. There is no LLM, no remote inference, no account, and no API key.

> **Status:** early implementation. The supported language pair and supported
> constructs are documented in [`docs/support-matrix.md`](docs/support-matrix.md).
> CodeWeaver never claims an unverified migration is behaviourally equivalent.

## Install

CodeWeaver's runtime has no third-party Python dependencies.

```bash
git clone https://github.com/parth-garg01/Codeweaver.git
cd Codeweaver
python3 -m pip install -e .      # optional, installs the `codeweaver` entry point
```

Running straight from a checkout works too:

```bash
PYTHONPATH=src python3 -m codeweaver.cli --help
```

## Quick start

```bash
codeweaver languages                       # supported source -> target pairs
codeweaver scan ./my-project               # inspect files, respect ignore rules
codeweaver analyze ./my-project            # parse + build the project model
codeweaver plan ./my-project --to typescript
codeweaver migrate ./my-project --to typescript --out ./out
codeweaver validate ./out
codeweaver report --format markdown
```

## Checks

```bash
./scripts/sanity-check
```

## License

MIT. See [LICENSE](LICENSE).
