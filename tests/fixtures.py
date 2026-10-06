"""Synthetic adapter fixture, not a live acceptance environment."""

import sys
from pathlib import Path

SOURCE = """apiVersion: spex.scenario.v0.1
kind: Scenario
metadata:
  name: action-smoke
spec:
  defaults:
    timeout: 60s
    pollInterval: 1s
  correlation:
    scenarioRunId: auto
    strategy: payloadTemplate
  payloadTemplates:
    sample:
      contentType: application/json
      body: '{"scenarioRunId":"${scenarioRunId}","correlationId":"${correlationId}"}'
  operations:
    - id: publish
      type: mqtt.publish
      mqtt:
        topic: telemetry/test
        payloadTemplateRef: sample
        correlationId: one
"""


def prepare(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "test.spex").write_text(SOURCE)
    (root / "scenario.toml").write_text(
        'schema = "spex.scenario/v1"\nruntime = "migration-testbench/v1"\n[[tests]]\nfile = "test.spex"\n'
    )
    (root / "suite.yaml").write_text("""apiVersion: spex.suite.v0.1
kind: ScenarioSuite
metadata:
  name: action-fixture
spec:
  bindingRef: binding.yaml
  scenarios:
    - test.spex
""")
    (root / "binding.yaml").write_text("""apiVersion: spex.binding.v0.1
kind: TargetBinding
metadata:
  name: action-fixture
spec:
  namespace: action-fixture
  rbac:
    create: true
  probe:
    image: spex-probe:test
  mqtt:
    brokerURL: tcp://localhost:1883
    clientIdPrefix: action-fixture
""")
    binary = root / "fake-bin"
    binary.mkdir(exist_ok=True)
    command = binary / "kubectl"
    command.write_text("""#!/bin/sh
printf '%s\n' "${ACTION_TEST_SENTINEL:-}"
if [ "$1" = kuttl ]; then
  if [ -n "${ACTION_TEST_READY:-}" ]; then
    touch "$ACTION_TEST_READY"
    exec sleep 30
  fi
  exit "${ACTION_TEST_EXIT_CODE:-0}"
fi
exit 0
""")
    command.chmod(0o700)
    return binary


if __name__ == "__main__":
    prepare(sys.argv[1])
