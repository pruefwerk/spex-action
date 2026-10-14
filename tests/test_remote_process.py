import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_cli_integration import read_outputs


class RemoteProcessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.client = self.root / "client"
        shutil.copyfile(
            Path(__file__).with_name("remote_client_fixture.py"), self.client
        )
        self.client.chmod(0o700)
        self.script = Path(__file__).parents[1] / "scripts/remote.py"
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("SPEX_ACTION_", "SPEX_REMOTE_", "FIXTURE_"))
        }
        self.env.update(
            SPEX_ACTION_WORKSPACE=str(self.root),
            SPEX_ACTION_EXECUTABLE=str(self.client),
            GITHUB_OUTPUT=str(self.root / "prepare-output"),
            FIXTURE_CALLS=str(self.root / "calls"),
        )
        result = subprocess.run(
            [sys.executable, str(self.script), "prepare"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = read_outputs(self.root / "prepare-output")
        self.env.update(
            SPEX_ACTION_REQUEST=outputs["request-path"],
            SPEX_ACTION_REQUEST_ID=outputs["request-id"],
            SPEX_ACTION_SCENARIO_ID=outputs["scenario-id"],
            SPEX_ACTION_RUNTIME_WORKFLOW="owner/runtime/.github/workflows/acceptance.yaml@v1",
            SPEX_ACTION_SOURCE_REPOSITORY="owner/service",
            SPEX_ACTION_SOURCE_RUN_ID="12",
            SPEX_ACTION_ARTIFACT_ID="13",
            SPEX_REMOTE_TOKEN="PRIVATE_TOKEN_SENTINEL",
            GITHUB_OUTPUT=str(self.root / "submit-output"),
        )

    def test_submit_process_returns_failure_and_safe_outputs(self):
        self.env["FIXTURE_EXIT"] = "3"
        result = subprocess.run(
            [sys.executable, str(self.script), "submit"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("PRIVATE_TOKEN_SENTINEL", result.stdout + result.stderr)
        values = read_outputs(self.root / "submit-output")
        self.assertEqual(
            json.loads(Path(values["result-path"]).read_text())["outcome"], "failed"
        )
        calls = [
            json.loads(line) for line in (self.root / "calls").read_text().splitlines()
        ]
        self.assertFalse(calls[0]["token_present"])
        self.assertTrue(calls[1]["token_present"])
        self.assertNotIn("PRIVATE_TOKEN_SENTINEL", (self.root / "calls").read_text())

    def terminate(self, driver):
        ready = self.root / "ready"
        self.env.update(FIXTURE_WAIT="1", FIXTURE_READY=str(ready))
        process = subprocess.Popen(
            driver,
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
            self.assertTrue(ready.exists(), "fixture did not start")
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertNotIn("PRIVATE_TOKEN_SENTINEL", stdout + stderr)
            return process.returncode
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    def test_submit_process_forwards_cancellation_and_preserves_receipt(self):
        self.assertEqual(
            self.terminate([sys.executable, str(self.script), "submit"]), 130
        )
        outputs = read_outputs(self.root / "submit-output")
        self.assertEqual(outputs["cancellation"], "requested")
        self.assertEqual(
            json.loads(Path(outputs["result-path"]).read_text())["outcome"], "cancelled"
        )

    def test_ignoring_client_is_killed_after_bounded_grace(self):
        self.env["FIXTURE_IGNORE_TERM"] = "1"
        driver = self.root / "driver.py"
        # Shorten only the test's timer; production retains its forty-second grace.
        driver.write_text(f"""import sys
sys.path.insert(0, {str(self.script.parent)!r})
import run
timer = run.threading.Timer
run.threading.Timer = lambda delay, callback: timer(0.1, callback)
code, response = run.command({[str(self.client), "scenario", "submit", "--workspace", str(self.root), "--request", self.env["SPEX_ACTION_REQUEST"], "--runtime-workflow", self.env["SPEX_ACTION_RUNTIME_WORKFLOW"]]!r}, {str(self.root)!r})
sys.exit(code)
""")
        self.assertEqual(self.terminate([sys.executable, str(driver)]), 137)


if __name__ == "__main__":
    unittest.main()
