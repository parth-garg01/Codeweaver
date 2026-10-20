# CodeWeaver

## Product Requirements Document (PRD)

**Document status:** Initial implementation specification\
**Product type:** Local-first, LLM-free,
intermediate-representation-based code migration tool\
**Primary interface:** Desktop-friendly web UI plus CLI\
**Repository status:** GitHub repository URL to be supplied by the
project owner\
**Version:** 1.0

------------------------------------------------------------------------

## 1. Product overview

CodeWeaver is a local-first tool that converts supported software
codebases from one programming language to another by using a shared
Intermediate Representation (IR).

The product must not use an LLM, generative AI API, cloud AI service, or
remote inference service for any part of its runtime operation. All
parsing, analysis, transformation, code generation, validation, and UI
operation must run locally on the user's machine. Users must be able to
use the application without an account or API key.

The first version is not expected to translate every language or every
framework. It must clearly define the supported language pair and
supported constructs, produce deterministic results for supported cases,
report unsupported constructs explicitly, and never claim that an
unverified migration is fully equivalent.

### 1.1 Product vision

Enable a developer to select a source repository, choose a supported
target language, inspect the analysis and migration plan, generate a
target project, run checks, and review a clear report of what passed,
failed, or needs manual attention.

### 1.2 Core principles

1.  **No LLM at runtime:** no LLM calls, model downloads, embeddings,
    remote AI APIs, or hidden AI fallback.
2.  **Local-first:** source code and project analysis remain on the
    user's machine.
3.  **IR-centered architecture:** language-specific frontends produce
    IR; target-specific backends generate output from IR.
4.  **Deterministic transformations:** the same source, configuration,
    and tool versions should produce the same output, apart from
    explicitly documented nondeterministic metadata.
5.  **Honest reporting:** unsupported features and unverified behavior
    must be visible.
6.  **Safe execution:** do not run arbitrary source or generated code
    without explicit user consent and an isolated execution strategy.
7.  **Incremental, reviewable engineering:** every feature change is
    tested and committed in a small, meaningful Git commit.
8.  **Professional UI:** use a coherent, human-designed visual system
    rather than generic AI-generated-looking layouts.

------------------------------------------------------------------------

## 2. Goals and non-goals

### 2.1 Goals

-   Analyze a repository containing multiple source files.
-   Parse supported languages into syntax trees using suitable language
    parsers.
-   Build a shared, typed IR that preserves the information needed for
    supported translations.
-   Record module and symbol relationships in a dependency graph.
-   Translate through the IR rather than creating a separate translator
    for every source-target pair.
-   Generate a target-language project with consistent imports, files,
    and configuration.
-   Detect unsupported constructs and semantic mismatches.
-   Validate generated code with locally installed or locally
    provisioned target toolchains.
-   Provide a polished UI and CLI for project selection, configuration,
    progress, diffs, diagnostics, and reports.
-   Support cancellation, resumable work, and safe re-runs where
    practical.
-   Provide an architecture that allows future language frontends and
    backends to be added without rewriting the core.

### 2.2 Non-goals for the initial release

-   Supporting every programming language or framework.
-   Guaranteeing semantic equivalence for arbitrary programs.
-   Automatically replacing every third-party library with an equivalent
    library.
-   Translating arbitrary runtime reflection, monkey-patching, dynamic
    code generation, or undocumented behavior without explicit support.
-   Cloud processing, account systems, telemetry by default, or hosted
    AI services.
-   Automatically pushing changes to a remote repository without user
    review.
-   Silently overwriting source files.
-   Claiming that compilation alone proves behavior equivalence.

------------------------------------------------------------------------

## 3. Target users and primary workflow

### 3.1 Target users

-   Developers migrating a project between supported languages.
-   Students and researchers exploring compiler and language translation
    techniques.
-   Engineering teams that need repeatable, inspectable migration
    workflows.
-   Developers who require local-only processing for privacy, cost, or
    offline use.

### 3.2 Primary workflow

1.  User opens CodeWeaver or runs its CLI.
2.  User selects a local source repository.
3.  CodeWeaver identifies project files and available language support.
4.  User selects a supported target language and output directory.
5.  CodeWeaver scans the repository, excluding configured ignored
    directories.
6.  The analyzer parses source files, builds symbol indexes, and creates
    dependency relationships.
7.  The IR pipeline converts supported source constructs into the shared
    representation.
8.  The planner identifies dependency groups, cycles, unsupported
    constructs, and migration risks.
9.  User reviews the plan and warnings.
10. CodeWeaver generates target files in a separate output directory.
11. The validator runs applicable formatters, compilers, type checks,
    and tests.
12. CodeWeaver displays a report with generated files, checks, errors,
    warnings, unsupported constructs, and confidence limitations.
13. User reviews the diff and chooses what to keep or export.

At no point may the application silently replace the original
repository.

------------------------------------------------------------------------

## 4. Functional requirements

### FR-01: Local project intake

-   Accept a local directory selected by the user.
-   Support an optional Git repository URL only as a user-initiated
    local clone operation; do not require a hosted service after
    cloning.
-   Detect project manifests and supported source files.
-   Respect `.gitignore` and a CodeWeaver ignore file, with safe
    defaults for build outputs, virtual environments, dependencies, and
    generated files.
-   Let the user review the file list before analysis.
-   Reject or safely handle symlinks that escape the selected project
    root.
-   Never upload source files, symbols, logs, or telemetry.

### FR-02: Language support registry

-   Maintain a registry of source frontends, target backends, versions,
    and supported constructs.
-   Show supported source-target pairs before a migration starts.
-   Each language adapter must declare its coverage and known
    limitations.
-   Unsupported pairs must be disabled or clearly marked as unavailable.
-   Do not pretend that a generic fallback can translate constructs that
    have no implemented mapping.

### FR-03: Parsing and semantic analysis

-   Use parsers and native compiler/analyzer APIs where practical.
-   Produce syntax diagnostics with file and source-span locations.
-   Resolve symbols and imports when the relevant language tooling
    permits.
-   Collect function, class, type, module, call, inheritance, and
    external dependency relationships where supported.
-   Distinguish facts directly observed from source syntax from inferred
    or unresolved relationships.
-   Preserve comments and source locations when feasible for diagnostics
    and source mapping.

### FR-04: Shared Intermediate Representation

The IR is the core product contract.

It must represent, where supported:

-   Modules and namespaces.
-   Functions, methods, parameters, default values, and return values.
-   Variables, scopes, and assignments.
-   Primitive, collection, function, object, and user-defined types.
-   Expressions, operators, calls, and member access.
-   Conditional branches, loops, exceptions, and return flow.
-   Classes, interfaces, inheritance, and composition.
-   Imports, exports, and visibility.
-   Async operations and concurrency constructs where supported.
-   Source locations and original-language metadata needed for
    diagnostics.
-   Unsupported or source-specific constructs without silently dropping
    them.
-   Explicit semantic annotations for cases where the target language
    differs from the source.

IR requirements:

-   Version the IR schema.
-   Validate IR instances against the schema.
-   Keep parsing, IR transformation, and code generation as separate
    stages.
-   Do not use rendered source text as the primary IR.
-   Avoid designing a falsely universal IR that discards source
    semantics. Preserve source-specific metadata and allow explicit
    extension nodes.
-   Include unit tests for every IR node and transformation pass.

### FR-05: Dependency graph and planning

-   Build a graph from resolved modules and symbols.
-   Support import, call, type, inheritance, and other implemented edge
    types.
-   Clearly label unresolved or dynamic relationships.
-   Detect strongly connected components and dependency cycles.
-   Plan migrations in dependency-aware groups; do not assume every
    graph is acyclic.
-   Provide impact analysis when an interface or IR node changes.
-   Show graph details in the UI without making graph visualization a
    prerequisite for running migrations.
-   Keep graph facts linked to source locations and IR nodes.

### FR-06: IR transformations

-   Implement deterministic, testable transformation passes.
-   Each pass must declare inputs, outputs, preconditions, and failure
    conditions.
-   Keep language-specific transformations in language adapters or rule
    packages rather than scattering them through core logic.
-   Make transformations idempotent where possible.
-   Detect unsupported semantics and emit actionable diagnostics.
-   Avoid silently substituting behavior that is merely similar.
-   Include focused tests and regression fixtures for each rule.

### FR-07: Target code generation

-   Generate code from validated IR using target-specific backends.
-   Generate imports, module boundaries, declarations, and project
    manifests consistently.
-   Preserve project structure when practical, while allowing documented
    target conventions.
-   Produce deterministic output.
-   Generate source maps or mapping metadata connecting output locations
    to source locations where feasible.
-   Never write output into the source directory by default.
-   Make overwrite behavior explicit and require confirmation.

### FR-08: Validation and behavioral checks

-   Run available target parsers, formatters, compilers, and type
    checkers.
-   Run migrated tests only when the user has enabled execution and the
    environment is suitable.
-   Support differential testing by feeding equivalent inputs to source
    and target implementations when a harness can be generated safely.
-   Compare return values, errors, state changes, and relevant side
    effects when observable.
-   Report checks that were skipped and why.
-   Do not report full behavioral equivalence merely because compilation
    or a subset of tests passed.
-   Bound retries and repair attempts. Since there is no LLM, repairs
    must come from deterministic rules or explicit user action.
-   Provide an actionable diagnostic for every failed check where
    possible.

### FR-09: Migration reports

Each migration report must include:

-   Source and target languages and tool versions.
-   Number of files discovered, parsed, translated, skipped, and failed.
-   Supported and unsupported constructs.
-   Unresolved imports and symbols.
-   Dependency cycles and how they were handled.
-   Validation commands and their exit statuses.
-   Test results and skipped tests.
-   Warnings about behavior that could not be verified.
-   Output directory and a list of generated files.
-   A final status such as `Passed`, `Passed with warnings`,
    `Incomplete`, or `Failed`.

### FR-10: CLI

Provide commands with clear help and exit codes. Exact command syntax
may be refined during implementation, but should cover:

-   `scan`: inspect a repository.
-   `analyze`: parse source and build the project model.
-   `plan`: produce a migration plan.
-   `migrate`: generate the target project.
-   `validate`: run checks on an existing generated project.
-   `report`: show or export the latest report.
-   `languages`: list supported languages and pairs.

The CLI must work without launching the UI.

### FR-11: Local persistence and resumability

-   Persist migration metadata and checkpoints locally.
-   Do not persist source code copies unnecessarily.
-   Make the storage location configurable.
-   Detect when source files have changed since a checkpoint and
    invalidate affected analysis.
-   Make reruns safe and avoid duplicating generated files or records.
-   Provide a way to clear local project metadata.

### FR-12: Privacy and offline operation

-   No LLM or generative AI dependency in production code paths.
-   No account or API key requirement.
-   No mandatory internet connection for analysis, translation, or
    validation, except when a user explicitly requests a package
    installation or Git clone.
-   No telemetry by default.
-   Network access should not occur silently. Document any optional
    network-dependent action.
-   Include a test or audit that detects unexpected outbound network
    calls in core workflows.

------------------------------------------------------------------------

## 5. User interface requirements

The UI should feel like a polished developer tool designed by a product
team, not a generic AI-generated dashboard.

### 5.1 Design direction

-   Clear information hierarchy, restrained colors, consistent spacing,
    and readable typography.
-   Use a small, deliberate design system for colors, typography,
    spacing, borders, radii, and interaction states.
-   Prefer neutral backgrounds with one restrained accent color.
-   Use real product information and useful empty states rather than
    decorative filler.
-   Avoid excessive gradients, glassmorphism, oversized hero sections,
    gratuitous animations, and unnecessary cards.
-   Use tables, code panes, split views, status indicators, and compact
    panels where they improve workflow.
-   Make accessibility, keyboard navigation, visible focus, and contrast
    part of implementation.
-   Ensure responsive behavior for desktop and tablet widths.
-   Keep errors, warnings, progress, and success states visually
    distinct.
-   Do not use AI-generated images as filler. Prefer icons, code
    visualizations, graph views, and typography.

### 5.2 Main screens

1.  **Welcome / project selection**
    -   Open local folder.
    -   Recent projects, if enabled locally.
    -   Supported language pairs and limitations.
2.  **Project overview**
    -   Repository file count, detected languages, manifests, and
        analysis status.
    -   Warnings and unresolved dependencies.
3.  **Dependency explorer**
    -   Searchable graph and file/symbol list.
    -   Filters for imports, calls, types, and unresolved edges.
    -   Selecting a node opens source location and dependency details.
4.  **Migration plan**
    -   Ordered dependency groups.
    -   Cycles, unsupported constructs, risk labels, and required
        toolchains.
    -   Clear review-before-generation action.
5.  **Migration progress**
    -   Current stage and module.
    -   Completed, failed, skipped, and queued work.
    -   Cancellation and safe resume.
6.  **Diff and generated output**
    -   Source/target comparison where meaningful.
    -   File tree and syntax-highlighted code.
    -   Explicit warnings for generated or incomplete files.
7.  **Validation report**
    -   Compilation, type checking, tests, differential checks, and
        skipped checks.
    -   Actionable diagnostics with file and line links.
    -   Exportable report.
8.  **Settings**
    -   Local toolchain paths, output defaults, ignored paths, resource
        limits, and privacy information.

### 5.3 UI quality gate

Before marking a UI feature complete:

-   Review the screen at supported viewport sizes.
-   Test loading, empty, success, warning, and error states.
-   Check keyboard and focus behavior.
-   Check contrast and text truncation.
-   Verify that every visible control performs a real action.
-   Remove placeholder controls and fabricated metrics.
-   Run automated UI tests and a manual visual review.

------------------------------------------------------------------------

## 6. Technical architecture

Use a modular architecture with explicit interfaces between packages.

Suggested logical structure:

``` text
codeweaver/
├── apps/
│   ├── cli/
│   └── desktop-ui/
├── packages/
│   ├── project_intake/
│   ├── language_registry/
│   ├── frontends/
│   ├── semantic_analysis/
│   ├── symbol_index/
│   ├── dependency_graph/
│   ├── ir_schema/
│   ├── ir_transforms/
│   ├── backends/
│   ├── validation/
│   ├── differential_testing/
│   ├── sandbox/
│   ├── persistence/
│   └── reporting/
├── adapters/
│   ├── source_languages/
│   ├── target_languages/
│   └── framework_rules/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   ├── differential/
│   ├── security/
│   └── fixtures/
├── docs/
├── scripts/
├── PRD.md
└── README.md
```

This is a logical starting point, not a requirement to create empty
folders for every component. Add modules when there is real
implementation work for them.

### 6.1 Architecture rules

-   Keep core interfaces stable and documented.
-   Keep language-specific code out of generic orchestration.
-   Use dependency injection or explicit adapters for toolchains and
    storage.
-   Avoid circular package dependencies.
-   Keep long-running tasks cancellable and observable.
-   Use structured logs with secret and source-code redaction.
-   Use schema versions for persisted records and IR.
-   Define resource limits for parsing and validation.
-   Pin or constrain dependency versions and document reproducible
    setup.
-   Prefer mature parsers and compilers over writing a parser from
    scratch.

### 6.2 Language-pair strategy

Do not claim universal translation in the first release. Choose one
source-target pair for the MVP after inspecting the repository and
available toolchains. The first pair must be implemented end-to-end,
including parsing, IR mapping, code generation, and validation.

The architecture should permit additional frontends and backends later.
Every added language pair must have a documented support matrix and a
benchmark fixture suite.

------------------------------------------------------------------------

## 7. Security and reliability

-   Treat source repositories and build scripts as untrusted.
-   Never automatically execute source code, generated code, package
    lifecycle scripts, or tests without an explicit user action or
    clearly configured execution policy.
-   If sandboxing is available, use restricted filesystem access,
    timeouts, memory limits, and network isolation.
-   Never overwrite the source project by default.
-   Validate all paths to prevent path traversal and symlink escapes.
-   Do not expose secrets in logs, diffs, issue reports, or generated
    reports.
-   Do not automatically include proprietary source code in GitHub
    issues.
-   GitHub issue creation must be opt-in and require a preview. Default
    issue bodies must contain only sanitized diagnostics, reproduction
    steps, versions, and relevant logs.
-   Do not create a public issue or PR automatically without user
    confirmation.
-   Handle interrupted migrations without corrupting prior output.
-   Keep backups or use a separate output directory for destructive
    operations.

------------------------------------------------------------------------

## 8. Engineering workflow for the coding agent

These requirements are mandatory for every implementation session.

### 8.1 Before coding

1.  Inspect the existing repository and read its `README`,
    `CONTRIBUTING`, build scripts, test instructions, and current Git
    status.
2.  If a GitHub repository URL has not been provided, work only in the
    local repository and ask for the URL before any remote GitHub
    action.
3.  Do not overwrite existing work or discard user changes.
4.  Create or update an implementation plan with small, independently
    verifiable tasks.
5.  Identify the acceptance criteria and tests for the next task.
6.  Check the current branch and remote configuration before changing
    Git state.
7.  Do not create a new framework or rewrite the application without
    first checking what already exists.

### 8.2 One feature change at a time

For every feature or bug fix:

1.  State the intended change and acceptance criteria.
2.  Make the smallest coherent implementation.
3.  Add or update tests in the same change.
4.  Run formatting, linting, type checks, relevant unit tests, and
    relevant integration tests.
5.  Run the project sanity-check suite defined in Section 9.
6.  Inspect the full diff, including untracked files, and ensure there
    are no secrets, unrelated edits, generated junk, or accidental
    source changes.
7.  Fix failures caused by the change and rerun the relevant checks.
8.  Commit the completed feature or fix with a concise, meaningful
    message.
9.  Verify the commit and working tree state.
10. Update the task checklist and report what was changed, tested, and
    left unresolved.

Do not combine unrelated features into one commit. Prefer small commits
that each leave the repository in a coherent state. A feature spanning
several layers may use multiple commits if each commit is meaningful and
the branch remains understandable.

### 8.3 Commit rules

-   Make a Git commit for every completed feature change and every
    independent bug fix.
-   Use conventional, descriptive messages, for example:
    -   `feat(ir): add function and parameter nodes`
    -   `test(graph): cover dependency cycles`
    -   `fix(codegen): preserve import aliases`
    -   `docs(cli): document validation commands`
-   Never use empty commits to simulate progress.
-   Never amend, squash, reset, or rewrite existing user commits unless
    explicitly authorized.
-   Never use `git push --force`.
-   Never commit secrets, credentials, local databases, caches, build
    artifacts, or generated test output.
-   Do not include any `git diff` log, diff dump, watermark, generated
    diff transcript, or other diff artifact in the commit message,
    commit body, trailers, or committed files. The Git commit must
    contain only the intended project changes. Normal Git metadata and
    ordinary commit history are expected.
-   Do not create a file or watermark that says the commit was generated
    by a diff tool or that embeds a `git diff` transcript.
-   Inspect the actual staged changes before committing. Stage explicit
    paths where practical instead of blindly staging everything.
-   If a sanity check fails, do not commit a knowingly broken feature
    unless the commit is an intentional, documented checkpoint
    explicitly approved by the project owner.
-   Do not claim a commit was created unless Git confirms it.

### 8.4 GitHub issue and pull request workflow

GitHub operations are conditional on the repository URL, remote,
credentials, and permissions being available.

When a sanity check fails:

1.  Capture the exact failing command, exit code, relevant sanitized
    output, environment/tool versions, and reproduction steps.
2.  Determine whether the failure is caused by the current change, an
    existing baseline issue, missing dependencies, unavailable services,
    or environment configuration.
3.  Attempt to fix failures introduced by the current change before
    escalating.
4.  If a real unresolved defect remains, create a focused GitHub issue
    with a clear title, expected behavior, actual behavior, reproduction
    steps, environment details, and acceptance criteria.
5.  Preview the issue and ask for user confirmation before creating it
    on GitHub.
6.  Create a dedicated branch for the fix, following the repository's
    naming conventions where available.
7.  Implement the fix and add a regression test that fails before the
    fix and passes after it.
8.  Run the relevant tests and the full sanity-check suite.
9.  Commit the fix in a separate, descriptive commit.
10. Push the branch only when remote access is configured and the user
    has authorized the action.
11. Open a pull request linked to the issue. Include the root cause,
    fix, tests run, known limitations, and relevant issue reference.
12. Preview and obtain user confirmation before opening the PR unless
    the user has explicitly authorized automatic PR creation for the
    current task.
13. Never merge a PR, bypass required checks, or close an issue
    automatically without authorization.
14. If remote access or permission is unavailable, prepare the local
    branch and provide the exact commands and issue/PR drafts instead of
    claiming the remote action succeeded.

For failures in an external dependency or environment, do not
automatically file a product bug unless evidence indicates a CodeWeaver
defect. Record the blocker and explain what is needed to proceed.

### 8.5 Parallel coding agents

If the coding environment supports multiple agents:

-   Use no more than **three agents concurrently**, including the
    coordinator.
-   Give each agent a bounded task and clear file/module ownership.
-   Agree on IR schemas and public interfaces before parallel work
    begins.
-   Avoid assigning agents overlapping write access to the same files.
-   Require each agent to report files changed, tests run, results, and
    unresolved issues.
-   The coordinator must review every change, run integration tests, and
    handle commits unless a repository-specific policy explicitly
    assigns ownership differently.
-   Do not let agents independently push, open issues, or create PRs
    without the coordinator's approval.
-   If parallel work creates conflicts or incompatible interfaces, stop
    integration and resolve them before proceeding.

------------------------------------------------------------------------

## 9. Sanity-check requirements

The project must provide a repeatable sanity-check command, such as
`make sanity`, `./scripts/sanity-check`, or an equivalent documented
command supported by the selected stack.

The command must run the applicable checks:

1.  Dependency and environment checks.
2.  Formatting verification.
3.  Linting.
4.  Static type checks.
5.  Unit tests.
6.  Integration tests for the affected pipeline.
7.  IR schema validation and fixture tests.
8.  Target code-generation regression tests.
9.  UI build and automated UI tests when UI code changed.
10. Security checks for accidental secrets and unsafe path handling
    where tooling is available.

### 9.1 After every feature

-   Run the fastest relevant checks during development.
-   Run the full sanity suite before the feature commit when feasible.
-   If the suite is too expensive to run on every small commit, run
    affected tests for intermediate commits and the full suite before
    declaring the feature complete.
-   Record the exact commands and outcomes in the agent's final summary.
-   Never invent passing test results or suppress failures merely to
    obtain a green result.

### 9.2 Failure handling

If a check fails:

1.  Preserve the failure output.
2.  Classify the failure as introduced, pre-existing, environmental, or
    inconclusive.
3.  Fix introduced failures and rerun the relevant checks.
4.  Add regression coverage for reproducible product defects.
5.  For unresolved product defects, follow the GitHub issue and PR
    workflow in Section 8.4.
6.  Do not state that the feature is complete if required checks remain
    failing. Mark it blocked or incomplete and explain why.

### 9.3 Baseline management

Before making changes, run the available checks to establish a baseline.
If the baseline is already failing, document those failures separately
so that the agent does not incorrectly attribute them to a new feature.
Do not modify tests merely to hide genuine failures.

------------------------------------------------------------------------

## 10. Testing strategy

### Unit tests

Cover individual parsers, IR nodes, schema validation, graph algorithms,
rewrite rules, code generators, and diagnostic formatting.

### Integration tests

Exercise the complete flow from repository scan to target project
generation.

### Regression tests

Every fixed bug must have a regression test where practical. Keep
minimal source fixtures and expected output fixtures under version
control.

### Differential tests

For supported cases, run equivalent inputs through source and target
implementations and compare observable results. Explicitly define
normalization for values whose representation differs but whose
semantics are equivalent.

### End-to-end tests

Use small, self-contained sample projects with known expected behavior.
Include multi-file projects, shared types, import aliases, dependency
cycles, exceptions, asynchronous behavior where supported, and
unsupported constructs.

### Performance tests

Measure scan, parse, graph construction, IR transformation, generation,
and validation separately. Avoid arbitrary performance claims before
benchmarks exist.

### UI tests

Test primary workflows, errors, cancellation, accessibility basics, and
empty/loading states.

------------------------------------------------------------------------

## 11. Milestones and acceptance criteria

### Milestone 0: Repository and engineering foundation

-   Inspect and document the existing stack.
-   Establish setup instructions, linting, tests, and a sanity-check
    command.
-   Establish the UI design system and architecture boundaries.
-   Confirm that no LLM or AI API is included in runtime dependencies.

**Acceptance:** a clean local setup can run the documented checks; CI,
if configured, runs the same relevant checks.

### Milestone 1: Project intake and analysis

-   Select a local project.
-   Enumerate files safely.
-   Detect language and manifests.
-   Parse supported source files.
-   Show diagnostics.

**Acceptance:** fixtures with nested directories, ignored paths, invalid
files, and unsafe symlinks are handled predictably.

### Milestone 2: IR foundation

-   Versioned IR schema.
-   Source frontend for the chosen first language.
-   IR validation and serialization.
-   Source locations and unsupported-node reporting.

**Acceptance:** representative source fixtures produce valid, stable IR
and preserve required source information.

### Milestone 3: Dependency graph

-   Symbol index.
-   Import and call relationships where supported.
-   Cycle detection and dependency groups.
-   Graph queries and impact analysis.

**Acceptance:** known fixture graphs match expected nodes and edges,
including cyclic dependencies.

### Milestone 4: First end-to-end language pair

-   Select and document the first source-target pair.
-   Implement mappings for a deliberately scoped set of constructs.
-   Generate a valid target project.
-   Run target compiler or type checker.

**Acceptance:** the supported fixture suite generates reproducible
output and passes the target toolchain checks. Unsupported constructs
produce explicit diagnostics.

### Milestone 5: Validation and reports

-   Test harness.
-   Differential checks for supported cases.
-   Actionable report and exit codes.
-   Safe output and rerun behavior.

**Acceptance:** deliberately introduced errors are detected and reported
accurately; reports never overstate equivalence.

### Milestone 6: Product-quality UI

-   Project overview, dependency explorer, migration plan, progress,
    diff, and validation report.
-   Responsive layout, keyboard access, loading and error states.
-   Visual review and automated tests.

**Acceptance:** primary workflows are usable without placeholder
controls, fabricated data, or unresolved critical UI defects.

### Milestone 7: Reliability and extension

-   Incremental analysis.
-   Checkpoint/resume.
-   Performance benchmarks.
-   Adapter documentation.
-   Additional language pair only after the first pair has stable
    coverage.

**Acceptance:** interrupted runs recover safely, changed files
invalidate relevant results, and benchmark results are documented.

------------------------------------------------------------------------

## 12. Definition of done for every feature

A feature is complete only when all applicable conditions are met:

-   Requirements and acceptance criteria are satisfied.
-   Implementation fits the existing architecture and style.
-   Tests have been added or updated.
-   Relevant sanity checks pass, or remaining failures are explicitly
    documented as blockers.
-   No unrelated changes, secrets, or accidental generated files are
    included.
-   The UI is visually and functionally reviewed if affected.
-   Documentation is updated when user-facing behavior changes.
-   A meaningful Git commit is created after reviewing the staged diff.
-   The working tree and commit status are verified.
-   GitHub issues and PRs are handled according to the authorization
    rules when unresolved product defects require them.
-   The agent reports changed files, commit hash, checks run, results,
    and limitations.

------------------------------------------------------------------------

## 13. Agent final response format

At the end of each work session, report:

1.  **Implemented:** concise list of completed work.
2.  **Files changed:** key paths and purpose.
3.  **Tests and sanity checks:** exact commands and pass/fail results.
4.  **Git commit:** commit hash and message, if created.
5.  **GitHub:** issue/PR URLs if actually created, or state that remote
    action was not performed and why.
6.  **Known issues:** unresolved defects, environment blockers, and
    unsupported behavior.
7.  **Next step:** one or more small, concrete tasks.

Never claim that a command, commit, issue, push, or PR succeeded without
confirming its result.

------------------------------------------------------------------------

## 14. Initial instructions to the implementation agent

Before implementing product features:

1.  Inspect the repository and current Git status.
2.  Identify the existing stack, tests, build tooling, and UI framework.
3.  Check whether the GitHub remote is configured. If the project owner
    has not supplied the repository URL, ask for it before remote
    operations.
4.  Propose a short, ordered implementation plan and select a first
    source-target pair based on available parsers and toolchains.
5.  Implement Milestone 0 first.
6.  Do not build a fake end-to-end demo with hardcoded translation
    results.
7.  Do not add an LLM dependency or remote AI service.
8.  Do not claim arbitrary language support before the adapters and
    tests exist.
9.  Keep changes small, run sanity checks, and create meaningful commits
    according to Section 8.
10. Use GitHub issues and PRs for unresolved product defects only with
    the confirmation workflow defined above.
11. Optimize for correctness, explainability, and a polished developer
    experience over the number of languages advertised.

The first deliverable is a working engineering foundation and an
implementation plan, not a pretend universal translator.
