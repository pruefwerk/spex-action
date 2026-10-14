# Action qualification checkpoint

## Prepared reusable-workflow requests

All 85 tests pass against the local reusable-capable Spex executable, along with
Ruff lint/format, Pyright and whitespace checks. Tests run the checked-in
prepare-mode steps and verify the artifact-ID output. They also check that prepare
mode rejects transport credentials and receiver destinations, and that preparation
neither dispatches a workflow nor produces an acceptance result. Dispatch remains
the default.

Spex v0.1.0-rc.46 includes reusable admission and trusted runtime staging.
Hosted preparation-to-reusable-workflow qualification still requires the runtime
to pin that release and publish the Action and trusted runtime files. A successful
preparation step does not qualify the acceptance environment.

## Renewable GitHub App authentication

The Action accepts an existing `remote-token` or `app-id` plus `app-private-key`.
App authentication finds the source and receiver installations, scopes each token
to one repository and renews it five minutes before expiry. Renewal uses the API
request's remaining time, including during bounded cancellation. The Action does
not retry dispatch.

All 82 tests passed against the rc.45 executable, along with Ruff lint/format,
Pyright and diff whitespace checks. Authentication tests verify actual RSA
signatures and check that temporary signing files have restricted permissions and
are removed. They also cover
authentication-mode validation, repository scopes, token caching and renewal,
invalid expiry responses, signing failures, token masking, secret-free artifacts
and composite input wiring.

Simulated runs cover success after three hours and cancellation after a six-hour
timeout. They use no live App credentials or GitHub dispatches. Hosted App
authentication and full caller-to-receiver qualification still need to run after
publication.

The implementation follows GitHub's documented
[JWT signing](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
and
[installation token issuance](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app).

## Historical checkpoints

These checkpoints describe earlier implementations. Read their test counts,
interfaces and blockers as historical records, not as the current release status.

## Output contract corrections

Preparation supplies artifact-directory, leaving the request directory available
if upload fails. Verified scenario/version outputs also remain available when
writing the result file fails. The Action publishes result-path only after a
successful write. It writes the transport summary and publishes GitHub outputs
independently, preserving the primary failure code. Unresolved identity fields
must be absent or empty strings; the Action rejects null and other non-string
values.

All 70 regression tests passed with the legacy candidate, along with formatting,
lint, type checking and workflow validation. Tests covered upload failure, failed
acceptance outputs, both reporting failure paths and unresolved identity
validation. Hosted receiver integration remained outstanding.

## Definition-owned runtime configuration

The Action removed runtime-config from its public inputs and raw transport
envelope. The definition's runtime_config section became its sole source of
runtime configuration. Regression tests checked that the Action forwarded
definition text unchanged and ignored the obsolete environment setting, so that
setting could not introduce a second configuration layer.

Legacy CLI adapters kept their interfaces, but action.yml invoked neither. All 64
regression tests passed with the legacy candidate. Formatting, lint, type checking
and workflow validation also passed. Hosted receiver integration remained
unqualified.

## Automatic source base and glob selection

The Action removed its working-directory input. It recorded its actual current
directory relative to the source workspace, rejected directories outside that
workspace and forwarded definition patterns unchanged. Spex expanded paths and
globs on the receiver's pinned source tree, sorted matches and removed duplicates,
rejected unmatched or escaping paths, and limited scans and match counts. The
receiver chose its own storage locations. Live workflow integration remained
outstanding.

## Receiver-owned Spex checkpoint

The public Action switched to scripts/transport.py. It uploaded an opaque
spex.transport/v1 JSON envelope containing github.sha and raw definitions and
overrides, dispatched the receiver, waited and validated the receipt. The receiver
handled source fetching, dependency resolution and execution versions. This path
required no Spex installation, binary selection, parsing or canonicalization on
the caller.

Local tests ran preparation without an executable and checked the actual composite
preparation steps. An in-memory GitHub service tested submission and results,
request/source/run identity checks, uncertain dispatch, timeout and cancellation,
and output projection. Tests also checked that redirects stripped credentials and
that failed result writes preserved the primary failure. They did not qualify a
deployed receiver or cancellation on GitHub.

The 17 transport/composite tests passed without a Spex binary. The complete
61-test regression suite passed with the legacy integration candidate, as did
Ruff formatting/lint, Pyright and workflow lint.

The CI matrix stopped building or installing Spex. A separate job tested real
artifact upload and download of the raw envelope without dispatch. Hosted jobs
had not run because the repository had no GitHub remote. The repository retained
legacy adapter files and tests for transition regression checks; action.yml did
not invoke them.

## Unified definition interface

The Action adopted definition and definition-files as its public inputs. Spex
detected the source format and rejected mixed authoring modes, multiple TOML
definitions and TOML combined with extra tests. TOML dependencies replaced the
separate Action support-files list.

The 600 Spex tests and 53 Action tests passed. Coverage included inline and
file-based TOML, mixed YAML/Gherkin, declared fixture packaging, input conflicts,
missing files and escaping paths. Ruff, Pyright, Go vet and workflow lint passed.
Hosted receiver qualification remained blocked.

## Action refinement checkpoint

The local suite had 52 tests, all of which passed without skips when supplied with
a scenario-capable candidate executable. Without that executable, eight
CLI-dependent checks skipped explicitly. This qualification covered only the
Action, excluding the runtime API and receiver implementation.

| Check | Result | Scope |
| --- | --- | --- |
| Remote/local adapters and installation | PASS | Includes single-install reuse and digest verification between steps |
| Output contract | PASS | Atomic publication, bounded identity fields, expected file locations, no symlink/glob uploads |
| Primary failure preservation | PASS | Invalid responses, output failures and temporary cleanup failures |
| Composite wiring | PASS — local harness | Reads `action.yml`, executes shell steps, simulates only artifact storage |
| Package/upload failure ordering | PASS | Neither failure can dispatch tests |
| Process cancellation | PASS | Graceful receipt preservation and forced termination of an unresponsive fixture client |
| Credentials | PASS | No token in packaging/local execution, argv, output or retained fixture evidence |
| Real CLI authoring/local compatibility | PASS | Existing local tests plus remote packaging without a runtime checkout |
| Ruff, Pyright, workflow lint | PASS | Python 3.11 target; tests also run locally on Python 3.14 |
| Hosted OS/Python matrix | BLOCKED | Linux/macOS × Python 3.11/3.13 jobs configured, not run |
| Hosted remote Action wiring | BLOCKED | Real upload/download plus simulated-client success/failure jobs configured, not run |
| Live receiver dispatch | BLOCKED | Requires compatible receiver and authorized cross-repository credentials |

The repository had no GitHub remote. Hosted qualification required publication
and a CI run. The fixture client did not dispatch workflows or contact production
infrastructure.

This checkpoint preserved the existing worktree changes, including earlier IDE
and type-checking fixes. It did not publish a release or change Spex.

The public Action supported remote submission only. It required runtime-workflow
and remote-token, used `scenario-timeout` for the execution limit and exposed
artifact-directory only as an output. Composite tests covered the public input
contract and timeout forwarding. Local CLI regression tests remained, but the
candidate workflow stopped invoking the removed local Action mode.

Callers could omit the runtime input or supply a contract or contract@release.
Real-client package checks tested omission and explicit release selection without loading
a local runtime. Separate Spex tests covered trusted-catalog resolution, strict
release matching before execution and v2 receipt matching. Receiver workflow
integration and live default/version selection remained BLOCKED.

With the updated client, 598 Spex tests and 52 Action tests passed, along with Go
vet, Ruff, Pyright and workflow lint. Local scenarios still required a runtime;
only remote authoring allowed omission. A real-client packaging test covered
mixed YAML/Gherkin documents separated by `---` in the existing spex input. Spex
split the documents; the Action had no parser. These checks required an updated
candidate executable.

## Remote-model update

The Action made remote submission the default and kept the earlier adapter through
`execution-mode: local`. Spex created the package, and the Action uploaded only
that package. The Spex client handled dispatch, correlation, waiting, cancellation
and receipt validation. The caller job did not deploy an acceptance environment.

All 31 local Action qualification tests passed: the previous 25 tests, five remote
transport tests and a real CLI test for all three package authoring modes without
a runtime checkout. Spex protocol tests used a mock GitHub API to check successful
and failed results, missing or mismatched receipts, wrong commits, duplicate runs,
timeout cancellation, uncertain dispatch and secret projection. They did not run
hosted dispatch or live acceptance.

The candidate passed 576 Spex race tests, Go vet, security/release gates, 133
testbench harness tests and all suite/catalog validators. The Action passed Ruff,
Pyright and workflow lint. Receiver-consistency tests checked that incomplete or
contradictory success receipts produced errors.

The receiver contract was documented in `remote-protocol.md`. Receiver
implementation, cross-repository authentication, hosted uploads, actual
cancellation and cleanup, and published client installation remained BLOCKED.
rc.38 predated remote support. This update did not publish a release or repository,
or dispatch a live run.

## Earlier local-execution checkpoint

The local implementation used the scenario CLI from Spex commit
`f5c10b2c7afdb0e4cc18896bb2dd7f3da53953ca`. This checkpoint created no Action tag,
remote publication or new Spex release.

## Results

| Check | Result | Scope |
| --- | --- | --- |
| Adapter/installer unit tests | PASS — 18 tests | Safe argv and files, installer integrity, output framing, failure preservation |
| Real CLI adapter tests | PASS — 7 tests | Freshly built Spex binary; simulated KUTTL |
| Inline, file and committed inputs | PASS | Same builder/runtime path; committed override also covered |
| Local replay of canonical TOML | PASS | Same scenario ID, simulated executor |
| Runtime-discovered tests | PASS | Existing suite discovery |
| Cancellation forwarding | PASS | SIGTERM reaches Spex and retains a cancelled result |
| Secret sentinel checks | PASS | Backend output absent from Action logs and retained artifacts |
| Python lint/format | PASS | Pinned Ruff; no runtime Python dependencies |
| Python type checking | PASS | Pinned Pyright, Python 3.11 target; no errors or warnings |
| Workflow lint | PASS | actionlint on the checked-in qualification workflow |
| Spex regression qualification | PASS | 547 race tests, vet, security and production-candidate gates |
| Testbench harness | PASS | 122 tests; no testbench production changes |
| GitHub composite execution | BLOCKED | Workflow added, not dispatched; repositories/commits must be accessible there |
| Published-version installation end to end | BLOCKED | Scenario-capable Spex release not published; installer uses mocked assets in tests |
| Live migration acceptance | BLOCKED | Requires the authorized Kubernetes/ECR environment |

The seven adapter integration tests checked inline sources, external files,
committed scenarios, committed scenarios with overrides, runtime discovery,
failure outputs and process cancellation. They used a simulated command without
contacting MQTT or deploying services, so they provided no live acceptance evidence.

## Re-run

Install `requirements-dev.txt` for linting. Run `ruff check scripts tests` and
`ruff format --check scripts tests`, then `pyright`. Set `SPEX_TEST_EXECUTABLE` to
a candidate binary and run `python3 -m unittest discover -s tests -v`. The seven
binary-dependent tests skip explicitly when that variable is absent.

The manual GitHub workflow required a Spex commit containing the scenario CLI.
It built that commit, ran the complete adapter tests and checked `uses: ./` to
qualify the wrapper before a release.

## Remaining work

At this checkpoint, hosted qualification, a qualified scenario-capable Spex
release and live inline/file/committed acceptance gates were still required before
promotion. The testbench also needed to integrate its portable lifecycle and
long-running credential renewal with the new entrypoint. The caller chose whether
to upload artifacts.

The Action printed the failure stage and exit code without echoing raw child
output. Spex results retained the detailed execution classification. Public
repository/release metadata, including licensing, remained undecided; this
checkpoint covered the local implementation only.
