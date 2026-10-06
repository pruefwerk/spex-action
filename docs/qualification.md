# Action qualification checkpoint

The local implementation uses the scenario CLI from Spex commit
`f5c10b2c7afdb0e4cc18896bb2dd7f3da53953ca`. No Action tag, remote publication or
new Spex release has been created by this checkpoint.

## Results

| Check | Result | Scope |
| --- | --- | --- |
| Adapter/installer unit tests | PASS — 17 tests | Safe argv and files, installer integrity, output framing, failure preservation |
| Real CLI adapter tests | PASS — 7 tests | Freshly built Spex binary; simulated KUTTL |
| Inline, file and committed inputs | PASS | Same builder/runtime path; committed override also covered |
| Local replay of canonical TOML | PASS | Same scenario ID, simulated executor |
| Runtime-discovered tests | PASS | Existing suite discovery |
| Cancellation forwarding | PASS | SIGTERM reaches Spex and retains a cancelled result |
| Secret sentinel checks | PASS | Backend output absent from Action logs and retained artifacts |
| Python lint/format | PASS | Pinned Ruff; no runtime Python dependencies |
| Workflow lint | PASS | actionlint on the checked-in qualification workflow |
| Spex regression qualification | PASS | 547 race tests, vet, security and production-candidate gates |
| Testbench harness | PASS | 122 tests; no testbench production changes |
| GitHub composite execution | BLOCKED | Workflow added, not dispatched; repositories/commits must be accessible there |
| Published-version installation end to end | BLOCKED | Scenario-capable Spex release not published; installer uses mocked assets in tests |
| Live migration acceptance | BLOCKED | Requires the authorized Kubernetes/ECR environment |

The seven adapter integration tests cover inline sources, external files,
committed scenarios, committed scenarios with overrides, runtime discovery,
failure outputs, and process cancellation. They do not contact MQTT or deploy
services. Their simulated command must not be used as acceptance evidence.

## Re-run

Install `requirements-dev.txt` for linting. Run `ruff check scripts tests` and
`ruff format --check scripts tests`. Set `SPEX_TEST_EXECUTABLE` to a candidate
binary and run `python3 -m unittest discover -s tests -v`. Without that variable,
the seven binary-dependent tests explicitly skip.

The manual GitHub workflow requires a Spex commit containing the scenario CLI.
It builds that commit, runs the complete adapter tests and exercises `uses: ./`.
This avoids requiring a release merely to qualify the wrapper.

## Remaining work

Run the hosted qualification, publish a qualified scenario-capable Spex release,
and run the live inline/file/committed acceptance gates before promotion. The
testbench's portable lifecycle and long-running credential renewal still need
integration with the new entrypoint. Artifact upload remains a caller decision.

The Action prints failure stage and exit code without echoing raw child output.
Detailed execution classification stays in Spex results. Public repository/release
metadata, including licensing, has not been established here; this is a local
implementation checkpoint, not a publication decision.
