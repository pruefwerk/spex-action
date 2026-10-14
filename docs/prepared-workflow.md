# Prepare a request for a reusable workflow

Use `execution-mode: prepare` when your runtime provides a reusable workflow.
The Action uploads the same request as dispatch mode and stops after the upload.
Prepare mode rejects `runtime-workflow` and transport credentials. The default
mode is `dispatch`.

Pass the uploaded artifact ID and request identity to the next job. The example
shows the intended interface; publish and qualify both components before using it:

```yaml
jobs:
  prepare-acceptance:
    runs-on: ubuntu-latest
    outputs:
      request-id: ${{ steps.prepare.outputs.request-id }}
      request-sha256: ${{ steps.prepare.outputs.request-sha256 }}
      request-artifact-id: ${{ steps.prepare.outputs.request-artifact-id }}
    steps:
      - uses: pruefwerk/spex-action@v1
        id: prepare
        with:
          execution-mode: prepare
          definition: |
            @smoke @connectivity @mongodb @kind
            Feature: Kind MongoDB connectivity
              Scenario: MongoDB connection smoke
                Given migration tenant "migration-smoke"
                Then the migration testbench can connect to MongoDB

  acceptance:
    needs: prepare-acceptance
    permissions:
      contents: write
      actions: read
      id-token: write
    uses: your-org/testbench/.github/workflows/acceptance-reusable.yaml@<runtime-release>
    with:
      request-id: ${{ needs.prepare-acceptance.outputs.request-id }}
      request-sha256: ${{ needs.prepare-acceptance.outputs.request-sha256 }}
      request-artifact-id: ${{ needs.prepare-acceptance.outputs.request-artifact-id }}
      runtime-profile: runtime/mongodb-connectivity.toml
```

Add `needs: release` to the preparation job when it follows an application
release. Wait for the release job to succeed, whether it publishes a chart or
decides to reuse one. Requiring `published == true` would exclude chart reuse.

Both modes handle inline definitions, committed definition files and metadata
overrides the same way. The Action forwards authoring data unchanged. The runtime
applies inherited catalogs and bindings, followed by any caller overrides its
policy permits.

Prepare mode provides request outputs, including `request-artifact-id`. Because
it does not run tests, scenario identity, acceptance results and cancellation
outputs stay empty. Pass the artifact ID between jobs; a local file path does
not transfer the file. The reusable acceptance job reports the test outcome and
supplies the evidence.

To run the existing baseline instead, omit caller tests and provide this inline
definition with the default gateway-migration runtime profile:

```toml
schema = "spex.scenario-request/v1"

[runtime_config.selection]
include_tags = ["ci-baseline"]
```

Neither example needs a commit tag. Add a `workflow_dispatch` entrypoint to the
caller workflow to request manual runs without a new commit. The MongoDB test
checks the request and runtime integration. It does not validate the service's
newly published image. The baseline uses the runtime's existing artifact pins
unless supported scenario configuration explicitly changes them.

The runtime must supply trusted files through a shared Action or runtime bundle
because the caller's `GITHUB_TOKEN` cannot clone another private repository. The
reusable job reads the request and source from its own caller run. Grant Contents
write on that repository for durable replay claims, and authorize the calling
repository to use runtime OIDC and fixture secrets.
