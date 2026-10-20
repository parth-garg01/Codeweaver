# Test fixtures

| Fixture | Purpose |
| ------- | ------- |
| `sample_project/` | A project that translates cleanly and whose generated TypeScript passes `tsc --noEmit`. Covers classes, inheritance, constructors, counted loops, comprehensions, exceptions and a deliberate import cycle. |
| `unsupported_project/` | A project built entirely from constructs CodeWeaver reports rather than translates. Its generated output is *expected* not to type-check; the tests assert that each gap is reported with a location and a hint. |

Fixtures are inputs to the tool, not project source. `scratch/notes.py` inside
`sample_project` is deliberately invalid Python so that ignore rules can be
tested against a file that would otherwise fail to parse — which is why
`tests/fixtures` is excluded from linting and formatting.
