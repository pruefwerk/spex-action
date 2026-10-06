"""Exercise the real Spex binary with a simulated KUTTL command.

Set SPEX_TEST_EXECUTABLE to a freshly built binary. These tests qualify the
adapter contract, not Kubernetes provisioning or service acceptance behavior.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fixtures import SOURCE, prepare


def read_outputs(path):
    lines = path.read_text().splitlines()
    values = {}
    while lines:
        key, delimiter = lines.pop(0).split("<<", 1)
        value = []
        while lines[0] != delimiter:
            value.append(lines.pop(0))
        lines.pop(0)
        values[key] = "\n".join(value)
    return values


@unittest.skipUnless(
    os.environ.get("SPEX_TEST_EXECUTABLE"), "requires a just-built Spex binary"
)
class RealCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        binary = prepare(self.root)
        self.executable = str(Path(os.environ["SPEX_TEST_EXECUTABLE"]).resolve())
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("SPEX_ACTION_")
        }
        self.env.update(
            SPEX_ACTION_WORKSPACE=str(self.root),
            SPEX_ACTION_EXECUTABLE=self.executable,
            SPEX_ACTION_RUNTIME="migration-testbench/v1",
            SPEX_SUITE=str(self.root / "suite.yaml"),
            GITHUB_OUTPUT=str(self.root / "outputs"),
            PATH=str(binary) + os.pathsep + os.environ["PATH"],
            ACTION_TEST_SENTINEL="PRIVATE_BACKEND_SENTINEL",
        )
        self.bootstrap = str(Path(__file__).parents[1] / "scripts/run.py")

    def run_action(self):
        result = subprocess.run(
            [sys.executable, self.bootstrap],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertNotIn("PRIVATE_BACKEND_SENTINEL", result.stdout + result.stderr)
        return result, read_outputs(self.root / "outputs")

    def verify(self, values):
        result = json.loads(Path(values["result-path"]).read_text())
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(len(values["scenario-id"]), 64)
        for path in Path(values["artifact-directory"]).rglob("*"):
            if path.is_file():
                self.assertNotIn("PRIVATE_BACKEND_SENTINEL", path.read_text())
        reproduced = subprocess.run(
            [
                self.executable,
                "scenario",
                "run",
                "--workspace",
                str(self.root),
                values["scenario-path"],
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(reproduced.returncode, 0, reproduced.stderr)
        self.assertEqual(
            json.loads(reproduced.stdout)["scenario_id"], values["scenario-id"]
        )

    def test_inline(self):
        self.env["SPEX_ACTION_INLINE"] = SOURCE
        result, values = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify(values)

    def test_external_file(self):
        self.env["SPEX_ACTION_FILES"] = "test.spex"
        result, values = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify(values)

    def test_committed(self):
        self.env["SPEX_ACTION_SCENARIO"] = "scenario.toml"
        result, values = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify(values)

    def test_committed_with_overlay(self):
        self.env.update(
            SPEX_ACTION_SCENARIO="scenario.toml",
            SPEX_ACTION_CONFIG="[execution]\nfail_fast = false\nrepetitions = 2",
        )
        result, values = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify(values)
        self.assertEqual(
            len(json.loads(Path(values["result-path"]).read_text())["tests"]), 2
        )

    def test_runtime_discovery(self):
        result, values = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.verify(values)

    def test_failure_retains_outputs_and_exit_code(self):
        self.env["ACTION_TEST_EXIT_CODE"] = "7"
        result, values = self.run_action()
        self.assertEqual(result.returncode, 4)
        self.assertEqual(
            json.loads(Path(values["result-path"]).read_text())["outcome"], "error"
        )

    def test_termination_reaches_spex(self):
        ready = self.root / "ready"
        self.env["ACTION_TEST_READY"] = str(ready)
        process = subprocess.Popen(
            [sys.executable, self.bootstrap],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "runtime never started")
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 130, stdout + stderr)
            values = read_outputs(self.root / "outputs")
            self.assertEqual(
                json.loads(Path(values["result-path"]).read_text())["outcome"],
                "cancelled",
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
