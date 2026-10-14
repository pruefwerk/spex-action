# Spex Action

Spex Action submits acceptance-test definitions to a receiver workflow. The
receiver runs Spex, parses the definitions, resolves dependencies and creates the
canonical scenario. It controls the Spex version, supported runtimes, test
environment and credentials. The submission runner does not install or run Spex.

Use `execution-mode: prepare` to upload a request for a later reusable-workflow
job. This mode needs no remote destination or transport credentials. See
[prepared workflow execution](docs/prepared-workflow.md) for the job setup and
artifact-ID output. The default `dispatch` mode submits the request and waits
for the result. Failed acceptance tests fail the Action.

Hosted caller-to-receiver qualification is still
pending. Receivers must implement the [receiver contract](docs/remote-protocol.md);
earlier Spex submission receipts use a different protocol.

## Workflow integration

Add a submission job after publishing the candidate, and require it to pass before
promotion. The job needs Bash, Python 3.11+, HTTPS access to GitHub and a supported
Node environment for `actions/upload-artifact@v4`. You do not need Go, Spex, Kind,
Helm or Kubernetes access on this runner. The job occupies the runner while it waits.

Required inputs for dispatch mode:

| Input | Purpose |
| --- | --- |
| `runtime-workflow` | `owner/repository/.github/workflows/file.yaml@ref` |
| `app-id` + `app-private-key`, or `remote-token` | Renewable GitHub App authentication, or an existing token valid for the full wait |

The Action resolves the workflow reference to a commit and checks that the
dispatched run uses it. Dispatch mode uses workflow dispatch; prepare mode leaves
the reusable workflow call to your next job. Use a protected immutable workflow
tag for reproducible runs.
The receiver needs its own credentials to fetch the source and request artifact.

For long runs, provide `app-id` and `app-private-key`. Install the App on both
repositories and grant Actions write and Contents read on the receiver, plus
Actions read on the source. The Action discovers the installations, requests a
token for each repository and renews tokens five minutes before expiry. The
repositories can use separate installations.

App authentication requires OpenSSL for RSA signing but no additional Python
packages. The Action masks issued tokens and keeps credentials out of the request
artifact. Temporary signing-key files have restricted permissions, and the Action
removes them immediately after signing. It does not persist issued tokens; they
expire naturally. Caller-provided tokens are not revoked.

```yaml
- uses: pruefwerk/spex-action@<release>
  with:
    runtime-workflow: owner/runtime/.github/workflows/acceptance.yaml@<release>
    app-id: ${{ vars.ACCEPTANCE_APP_ID }}
    app-private-key: ${{ secrets.ACCEPTANCE_APP_PRIVATE_KEY }}
```

You can use `remote-token` if it remains valid for the entire wait. Token renewal
requires App credentials. The Action rejects inputs that combine both
authentication modes. The source repository's normal `GITHUB_TOKEN` generally
cannot dispatch into another private repository. AWS credential renewal on the
receiver does not renew the caller's GitHub credentials.

## Definitions and source files

Use `definition` for inline text or `definition-files` for committed paths or glob
patterns, one per line. The Action forwards the inputs unchanged. The receiver
detects their format and validates them as TOML, Spex YAML or Gherkin:

- Provide one complete TOML definition or one or more YAML/Gherkin test definitions.
- Do not mix TOML with another definition. The receiver does not implicitly append
  or replace tests.
- Separate inline YAML/Gherkin documents with standalone column-zero `---` lines.
- Declare fixture paths in TOML with `dependencies = [...]`.

Choose one input. The Action rejects requests that supply both, as well as paths
that escape the source workspace. The receiver expands patterns, sorts the
matches, removes duplicates and rejects patterns that match nothing. Omit both inputs
to use the receiver's conventional test discovery.

The Action forwards metadata overrides (`name`, `description`, `scenario-timeout`)
as data for Spex to apply on the receiver. Put runtime configuration in the
definition's `[runtime_config]` section. The Action has no separate
`runtime-config` input or transport override.

The Action records the preparation step's current directory relative to
`GITHUB_WORKSPACE` (normally `.`). It rejects directories outside that workspace
and has no directory input. The receiver fetches the source at `github.sha`,
resolves paths from the recorded source base and chooses its own storage locations.

You do not need a checkout on the submission runner. If you have one, its HEAD
must match `github.sha`, and the selected workspace must have no uncommitted
changes. Ignored or generated files do not enter the submitted source snapshot.
Commit file-based definitions and dependencies; send generated definitions inline.
Submodules and Git LFS materialization require an explicit receiver policy.

Never put literal credentials in definitions or overrides. The Action uploads a
size-limited JSON request with the source identity, definition text or paths, and
raw authoring inputs. The upload excludes the repository archive, environment
dump and local fixtures. The request artifact expires after three days.

## Examples

These examples use the v1 Action. Replace the receiver references with
your runtime's published workflow and release.

### 1. Inline tests

```yaml
- uses: pruefwerk/spex-action@v1
  with:
    runtime-workflow: your-org/testbench/.github/workflows/acceptance.yaml@v1
    remote-token: ${{ secrets.ACCEPTANCE_TOKEN }}
    definition: |
      Feature: Gateway ownership
        Scenario: An empty gateway becomes ready
          Given an empty gateway exists
          When the gateway migrates to AWS
          Then the gateway routes to AWS
```

The receiver must provide catalog bindings for these example steps.

### 2. Committed test files

```yaml
- uses: pruefwerk/spex-action@v1
  with:
    runtime-workflow: your-org/testbench/.github/workflows/acceptance.yaml@v1
    remote-token: ${{ secrets.ACCEPTANCE_TOKEN }}
    definition-files: |
      acceptance/gateway-migration/**/*.feature
      acceptance/shared/*.yaml
```

### 3. Complete definition with fixture dependencies

```yaml
- uses: pruefwerk/spex-action@v1
  with:
    runtime-workflow: your-org/testbench/.github/workflows/acceptance.yaml@v1
    remote-token: ${{ secrets.ACCEPTANCE_TOKEN }}
    definition: |
      schema = "spex.scenario-request/v1"
      dependencies = ["acceptance/fixtures/reading.json"]

      [[tests]]
      file = "acceptance/ownership.feature"

      [runtime_config.execution]
      fail_fast = true
```

You can commit the same TOML and pass its path through `definition-files`.
Definition and fixture dependency paths resolve from the recorded source base,
not from the TOML file's directory.

Globs support `*`, `?`, character classes such as `[ab]`, and `**` as a complete
path component for recursive matching, including zero directories. Use `/`
separators. Regex, brace expansion and exclusion patterns are unsupported.

Matches include hidden files except `.git`. Spex skips directory symlinks and
requires file symlinks to stay inside the source base. Scans stop at 100,000 entries
and results at 1,000 files. The one-TOML-or-tests rule also applies to expanded
matches; arbitrary data files are not test definitions.

## Runtime and execution versions

Omit `runtime` to use the receiver's default. A selector such as
`migration-testbench/v1@v0.4.0` requests a supported implementation release. A bare
contract requests that contract's configured default. The receiver rejects
unsupported selections without falling back to another release. `/v1` identifies
the contract version, not a Spex release.

The receiver records the exact runtime release and Spex version, keeps the
canonical scenario and returns its identity. The caller cannot configure the
receiver's Spex installation. `spex-version` reports the version as an output;
`spex-executable` is not a public input.

## Results and cancellation

The outputs are `request-id`, `request-path`, `request-sha256`, `request-artifact-id`, `run-url`, `result-path`,
`artifact-directory`, `scenario-id`, `runtime`, `runtime-release`, `spex-version`
and `cancellation`. Early failures can leave outputs absent. `scenario-id`
identifies the receiver's final scenario. An unresolved request has no scenario
identity. Retrieve canonical TOML from the receiver's evidence; the Action has
no caller-side `scenario-path`.

Preparation provides the request outputs and `artifact-directory` even if upload
later fails. The Action publishes receiver identities and versions only after it
validates the receipt. Unresolved values stay empty; they do not contain
placeholders such as `None`.

The Action sets `result-path` after it writes a sanitized local JSON summary.
Detailed test evidence stays on the receiver. If the summary write fails,
verified identities remain available. Writing the transport summary and publishing
GitHub outputs are independent operations, so either can succeed when the other
fails. Hard runner termination or a failure to write GitHub's output file can
still prevent output publication.

`cancellation` records the Action's cancellation request status:
`not_requested`, `requested`, or `unconfirmed`. It does not report whether the
receiver has stopped or completed cleanup. It is absent if submission never starts.

The Action checks the receipt against the request bytes, source commit, receiver
workflow commit, run ID and attempt. It requires complete test evidence for the
submitted request before reporting success, even if the workflow passed. The
local summary contains structured outcomes and version data and excludes
arbitrary receiver diagnostics.

`wait-timeout` covers API calls, queueing and execution. It defaults to `6h`, allows
up to `24h`, and accepts integer `h`, `m`, `s` components. Set it below the caller's
job timeout.

When cancelled, the Action spends up to ten seconds attempting to cancel the
remote run. An in-flight API call can take up to thirty seconds to return first.
`requested` means GitHub accepted the request; it does not confirm cleanup.
Inspect the remote run if the status is `unconfirmed`. Hard runner termination or
expired credentials can leave remote work running, so the receiver still needs
deadlines and cleanup.

The Action never retries dispatch automatically. An exclusive local marker blocks
a second dispatch of the same request if the first acknowledgement is uncertain.
Inspect the remote run before creating a new request. The Action requires a
receiver result to report success.

## Development and qualification

Install `requirements-dev.txt`, then run:

```sh
python3 -m unittest discover -s tests -p 'test_transport.py'
python3 -m unittest discover -s tests -p 'test_composite.py'
ruff check scripts tests
ruff format --check scripts tests
pyright
```

Transport tests use an in-memory GitHub service. Composite tests run the
checked-in preparation shell step with substitutes for submission HTTP calls and
artifact storage. Neither test suite needs a Spex binary. CI also tests real artifact
upload and download without dispatching a receiver.

The repository keeps `scripts/run.py`, `scripts/remote.py` and their tests as
transition regression fixtures. `action.yml` invokes neither, and they do not
define the public interface. Their optional integration checks need a candidate
Spex binary and skip when none is available. See [qualification status](docs/qualification.md)
for the outstanding hosted and live receiver checks.

## License

Spex Action uses the same [Internal Business Source Available License 1.0](LICENSE)
as Spex. It is source-available, not open source. See the license for permitted
internal use and restrictions on redistribution and external offerings.
