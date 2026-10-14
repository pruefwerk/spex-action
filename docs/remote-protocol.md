# Receiver contract: opaque submissions

The Action uploads raw authoring data and an immutable source reference using
`spex.transport/v1`. The receiver runs Spex to create the scenario. Earlier
`spex.submission/v1` and `/v2` clients use separate interfaces, and this transport
rejects their receipts. Hosted receiver qualification is still pending.

## Dispatch and admission

Accept these string `workflow_dispatch` inputs:

| Input | Meaning |
| --- | --- |
| `transport_schema` | Exactly `spex.transport/v1` |
| `request_id` | 32 lowercase hex characters |
| `request_sha256` | SHA-256 of the exact uploaded request.json bytes |
| `source_repository` | Caller repository |
| `source_run_id` | Caller workflow run ID |
| `source_artifact_id` | Artifact containing the request |

Set `run-name: spex:${{ inputs.request_id }}`. The Action requires exactly one
matching run, on its first attempt, at the commit resolved before dispatch.
Persist request IDs to reject duplicates across runs. Workflow concurrency alone
does not prevent repeated execution. Choose concurrency keys that cannot cancel
unrelated requests.

Authorize the actor, source repository, workflow and source revision before
fetching files or executing tests. A transport token alone must not authorize
arbitrary definitions on privileged infrastructure. Read the source with the
receiver's own credentials.

Fetch the artifact by ID and verify its source run, head commit, expiry and name
(`spex-request-<request_id>`). The ZIP must contain exactly one regular
`request.json` file, at most 4 MiB uncompressed. Reject duplicate entries, links
and paths that escape the destination. Check the exact file digest before decoding
the JSON.

The JSON envelope contains:

```json
{
  "schema": "spex.transport/v1",
  "request_id": "<32 hex characters>",
  "source": {
    "repository": "owner/source",
    "commit": "<40-character workflow source SHA>",
    "root": "<automatically captured caller directory, relative to source repo>",
    "run_id": 123
  },
  "authoring": {
    "definition": "<raw inline content, or empty>",
    "definition_files": [],
    "runtime": "",
    "name": "",
    "description": "",
    "timeout": ""
  }
}
```

Treat empty optional strings as absent. Put runtime configuration in the
definition's `[runtime_config]` section. Reject requests containing the obsolete
authoring.runtime_config override field; silently ignoring it would hide an error.

Verify the source fields against the authenticated GitHub run and dispatch context
before trusting the envelope. The source root records the caller's actual working
directory and cannot configure receiver storage. Check that it stays within the
source repository. Choose checkout and execution locations on the receiver, and
fetch the exact source commit instead of a moving branch.

Keep source data separate from trusted receiver code so submitted files cannot
replace infrastructure scripts. Require an explicit policy before executing source
hooks or initializing submodules/LFS. Keep definition and dependency paths inside
the selected source root, and check symlink targets too.

`definition_files` contains paths or glob patterns. Pass them unchanged to Spex
on the receiver. Expand patterns against the pinned source tree, sort the
matches, remove duplicates and reject patterns that match nothing. Support `*`, `?`,
character classes and whole-component `**`. Regex and exclusions are unsupported.
Skip `.git` and directory symlinks. Reject matches that are not files, including
file symlinks whose targets escape the source root. Apply the one-TOML-or-test-files
rule after expansion.

## Receiver-owned interpretation

Use the receiver's Spex version to detect and parse definitions, reject conflicting
authoring modes, resolve declared dependencies and apply metadata. Resolve default
or explicitly requested releases through the trusted runtime catalog. Reject
unsupported selections without falling back to another release.

Apply the selected runtime's typed configuration merger. Before setup, enforce
admission policies for suite paths, namespaces, images, bindings, catalogs and hooks.

Spex's `pkg/receiver` validates transport data, handles shared authoring, selects
and executes runtimes, and generates receipts. Spex includes an example workflow.
The runtime repository hosts the receiver and must provide authenticated source
fetching, durable admission claims and workflow orchestration. Existing generic
workflows also need these capabilities to meet the contract. Qualify the workflow
on GitHub before claiming live receiver support.

Save the final canonical `scenario.toml`, its SHA-256, the exact runtime release
and Spex version, and the source commit. Do not expand credentials into artifacts.
Use the same scenario/runtime execution path locally and on the receiver. A runtime
that declares a pinned release must return that release from `Release() string`.

## Receipt

Upload `spex-result-<request_id>` containing exactly one regular `receipt.json`,
at most 2 MiB uncompressed:

```json
{
  "schema": "spex.transport-result/v1",
  "request_id": "<request ID>",
  "request_sha256": "<request file digest>",
  "source_commit": "<source commit>",
  "workflow_sha": "<actual receiver workflow commit>",
  "run_id": 456,
  "run_attempt": 1,
  "planned_tests": 1,
  "result": {
    "schema": "spex.result/v1",
    "scenario_id": "<final canonical scenario digest>",
    "runtime": "migration-testbench/v1",
    "runtime_release": "v0.4.0",
    "spex_version": "v0.2.0",
    "outcome": "passed",
    "cleanup": "succeeded",
    "tests": [{"outcome": "passed"}]
  }
}
```

Return Spex's actual results and record the plan's test count. A successful workflow
exit status does not establish the test outcome. Success also requires a passing
result for every planned test, no reported problems, and cleanup that succeeded
or was unnecessary. Preserve the primary failure if evidence collection or
cleanup also fails.

Before runtime resolution, an `error` or `cancelled` receipt may omit all four
resolved identity/version fields. Do not invent a scenario or version. If you
include these fields, use strings: unresolved values must be empty strings, not
JSON null, booleans or numbers. All four fields must either remain unresolved or
describe the resolved execution.

The Action keeps selected result fields and discards arbitrary diagnostics and
artifact paths. Full evidence and canonical TOML stay on the receiver. The Action
rejects missing or inconsistent receipts, including mismatched identities.

## Cancellation and limits

The Action uses GitHub.com REST APIs, dispatches once and polls every ten seconds.
Run and artifact searches stop after 1,000 records. The Action limits response
sizes and gives each call a maximum 30-second timeout. If waiting fails or is
interrupted, it spends up to ten additional seconds attempting cancellation.

Runner loss, late run discovery or expired credentials can leave work running.
Set a receiver job deadline, bound its cleanup time and upload a failure receipt
when possible.

GitHub App authentication renews repository-scoped installation tokens before
expiry, including during cancellation. The Action cannot renew caller-provided
tokens supplied through `remote-token`. Renewal does not change the receiver
protocol or include App credentials in the package. Removing an installation or
failing to issue a token can still prevent cancellation.

The transport does not support GHES, detached monitoring or generated local-file
uploads. Live receiver qualification remains outstanding.
