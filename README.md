# Spex Action — implementation preview

This composite Action sends authoring inputs to `spex scenario build`, then runs
the resulting canonical document with `spex scenario run`. Spex owns parsing,
validation, runtime selection, configuration precedence and execution semantics.

The Action is not released yet. The scenario CLI also needs a new Spex release;
older releases do not contain these commands. For now, use a freshly built binary
through `spex-executable`. Local adapter qualification passes; live migration
acceptance and GitHub-hosted qualification remain outstanding.

## Requirements

- Bash and Python 3.11 or newer on Linux or macOS.
- Exactly one of `spex-executable` or an explicit published `spex-version` tag.
- An existing runtime workspace, suite configuration, tools and credentials.
  Select the suite through `runtime-config.suite`, `SPEX_SUITE`, or `suite.yaml`.

The installer uses the existing `pruefwerk/spex` release archive and requires its
SHA-256 checksum. It fails if that platform asset or checksum is unavailable.
The checksum checks integrity; it is not a separate publisher signature.
The Action does not install Helm, Kind, kubectl, probe images or project-specific
dependencies. Keep the existing runtime preparation until portable testbench
lifecycle qualification finishes.

## Three authoring modes

These examples assume you checked out this Action at `.github/actions/spex`,
provided a candidate executable at `${{ runner.temp }}/spex-candidate`, and
configured the runtime workspace. They do not imply that an `@v1` tag exists.

### 1. Inline Spex

```yaml
- uses: ./.github/actions/spex
  with:
    spex-executable: ${{ runner.temp }}/spex-candidate
    runtime: migration-testbench/v1
    name: Telemetry smoke
    spex: |
      apiVersion: spex.scenario.v0.1
      kind: Scenario
      metadata:
        name: telemetry-smoke
      spec:
        defaults:
          timeout: 60s
          pollInterval: 1s
        correlation:
          scenarioRunId: auto
          strategy: payloadTemplate
        payloadTemplates:
          reading:
            contentType: application/json
            body: '{"scenarioRunId":"${scenarioRunId}","correlationId":"${correlationId}"}'
        operations:
          - id: publish
            type: mqtt.publish
            mqtt:
              topic: telemetry/test
              payloadTemplateRef: reading
              correlationId: one
```

The selected binding must provide MQTT connectivity. Inline YAML and Gherkin use
the existing Spex parsers; the Action does not introduce a test language.

### 2. External Spex files

```yaml
- uses: ./.github/actions/spex
  with:
    spex-executable: ${{ runner.temp }}/spex-candidate
    runtime: migration-testbench/v1
    spex-files: |
      acceptance/gateway.spex
      acceptance/cache.feature
```

List one workspace-relative path per line. If no explicit sources are supplied,
the runtime uses its existing suite discovery.

### 3. Committed scenario with a final override

```yaml
- uses: ./.github/actions/spex
  with:
    spex-executable: ${{ runner.temp }}/spex-candidate
    scenario: acceptance/full-platform.toml
    runtime-config: |
      [execution]
      fail_fast = false
```

Omit `runtime-config` to execute the committed document unchanged. Spex rejects
combining a committed scenario with new inline/file sources or a different runtime.

## Inputs and outputs

| Input | Purpose |
| --- | --- |
| `scenario` | Committed TOML path |
| `runtime` | Runtime identifier for dynamic construction |
| `name`, `description`, `timeout` | Optional metadata overrides |
| `spex` | Inline YAML or Gherkin |
| `spex-files` | Newline-separated external paths |
| `runtime-config` | Runtime-owned TOML fragment |
| `working-directory` | Workspace root; defaults to `.` |
| `artifact-directory` | Workspace-relative artifact base; defaults to `.spex/runs` |
| `spex-version` | Explicit published Spex version tag |
| `spex-executable` | Existing executable path, relative to the workspace or absolute |

Empty optional Action inputs mean “not provided.” Use committed TOML or the CLI
if you need to explicitly clear metadata. Typed runtime TOML retains explicit
`false`, zero and legal empty values. No environment-variable expansion occurs in
the wrapper; references belong to the runtime's own configuration mechanism.

Outputs are `scenario-id`, `scenario-path`, `result-path`, `artifact-directory`
and `runtime`. Large evidence stays in files. The Action does not upload it:
consumers choose their artifact-upload policy. Outputs remain available after an
execution failure when Spex supplies them; a setup/storage failure can leave some
outputs absent. The canonical authoring file remains available after a successful
build even if later execution cannot start.

Reproduce a run with `spex scenario run --workspace REPOSITORY SCENARIO_PATH`.
The same sources, runtime configuration, tools and credentials must be available.

## Safety and diagnostics

Inputs travel through environment variables, private temporary files and argv
arrays. The wrapper uses no shell interpolation or `eval`, does not echo raw
configuration, and never prints captured subprocess output. It prints stage and
exit status on failure; inspect the retained result and evidence for details.

Use secret references, not literal credentials in metadata or inline test text.
Temporary authoring files are removed after the run. Spex separately controls
execution-workspace cleanup and evidence export. Cancellation reaches the active
Spex process; the wrapper gives it up to 40 seconds before forced termination.
Forced termination or runner failure can prevent cleanup and result creation.

## Qualification

`python3 -m unittest discover -s tests -p test_action.py -v` runs adapter and
installer unit tests without dependencies or network access. Set
`SPEX_TEST_EXECUTABLE` to a just-built Spex binary and run all tests to exercise
all authoring modes, local reproduction, failure outputs and cancellation.

The integration fixture simulates KUTTL. It does not establish that Kubernetes
deployment or gateway migration passes. The manual GitHub qualification workflow
accepts an accessible Spex commit, builds it, runs these tests and exercises the
composite Action itself. Run live acceptance separately before release.

See [qualification results](docs/qualification.md) and
[machine-readable blockers](docs/blockers.json) for the current boundary.
