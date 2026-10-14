# Reproduce a scenario locally

Use the Spex CLI to run the receiver's canonical scenario on your own machine:

```sh
spex scenario run --workspace /path/to/runtime-workspace /path/to/scenario.toml
```

Restore the packaged test sources and support files at their workspace-relative
paths. You also need the receiver's runtime configuration, tools and credentials.
The canonical scenario contains no expanded credentials and does not provision
these dependencies.

Spex parses and validates the scenario, selects the runtime, resolves configuration
precedence and executes the tests. The command runs locally without dispatching a
GitHub workflow. It may create resources in the configured environment, so check
the target before running it.

The Action no longer accepts the unreleased `execution-mode: local` input.
Existing Spex CLI and migration-testbench entrypoints remain unchanged.
Local-adapter tests still provide regression coverage, but the Action does not
support a local execution interface.

See the [receiver contract](remote-protocol.md) and
[qualification status](qualification.md) for the current remote-execution limits.
