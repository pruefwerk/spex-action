"""Execute checked-in transport wiring with an in-memory GitHub service."""

import json
import os
import re
import subprocess
from pathlib import Path
from unittest import mock

import yaml
from test_cli_integration import read_outputs
from test_transport import TransportCase, transport
from transport_fixture import API

ROOT = Path(__file__).parents[1]


class CompositeTests(TransportCase):
    def run_composite(
        self, upload_fails=False, mode="passed", app=False, prepare=False
    ):
        action = yaml.safe_load((ROOT / "action.yml").read_text())
        inputs = {
            key: value.get("default", "") for key, value in action["inputs"].items()
        }
        inputs.update(
            {
                "runtime-workflow": self.env["SPEX_ACTION_RUNTIME_WORKFLOW"],
                "remote-token": "PRIVATE_TOKEN_SENTINEL",
                "definition": "opaque input",
            }
        )
        if app:
            inputs.update(
                {
                    "remote-token": "",
                    "app-id": "123",
                    "app-private-key": "PRIVATE_KEY_SENTINEL",
                }
            )
        if prepare:
            inputs.update(
                {
                    "execution-mode": "prepare",
                    "runtime-workflow": "",
                    "remote-token": "",
                }
            )
        step_outputs = {}

        def expand(value):
            def replace(match):
                expression = match[1]
                if expression.startswith("inputs."):
                    return inputs[expression[7:]]
                _, step, _, field = expression.split(".")
                return step_outputs.get(step, {}).get(field, "")

            return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", replace, str(value))

        for i, step in enumerate(action["runs"]["steps"]):
            if "if" in step:
                self.assertEqual(step["if"], "inputs.execution-mode == 'dispatch'")
                if inputs["execution-mode"] != "dispatch":
                    continue
            if "uses" in step:
                self.assertEqual(step["uses"], "actions/upload-artifact@v4")
                path = Path(expand(step["with"]["path"]))
                self.assertEqual(path.name, "request.json")
                self.assertNotIn("PRIVATE_TOKEN", path.read_text())
                if upload_fails:
                    break
                step_outputs[step["id"]] = {"artifact-id": "123"}
                continue
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("GITHUB_", "SPEX_"))
            }
            env.update(self.env)
            env.update(
                {key: expand(value) for key, value in step.get("env", {}).items()}
            )
            env.update(
                GITHUB_ACTION_PATH=str(ROOT), GITHUB_OUTPUT=str(self.root / f"step-{i}")
            )
            if step.get("id") == "submit":
                request = json.loads(Path(env["SPEX_ACTION_REQUEST"]).read_text())
                api = API(request, env["SPEX_ACTION_REQUEST_SHA"], mode)
                self.assertEqual(
                    step["run"],
                    'python3 "$GITHUB_ACTION_PATH/scripts/transport.py" submit',
                )
                transport.credentials(env)
                with mock.patch.object(transport.GitHub, "from_env", return_value=api):
                    self.assertEqual(
                        transport.submit(env), 3 if mode == "failed" else 0
                    )
            else:
                result = subprocess.run(
                    ["bash", "-e", "-c", step["run"]],
                    env=env,
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("PRIVATE_TOKEN", result.stdout + result.stderr)
                if step.get("id") == "prepare":
                    self.assertEqual(env["SPEX_REMOTE_TOKEN"], "")
                    self.assertEqual(env["SPEX_REMOTE_APP_ID"], "")
                    self.assertEqual(env["SPEX_REMOTE_APP_PRIVATE_KEY"], "")
            output_file = Path(env["GITHUB_OUTPUT"])
            step_outputs[step.get("id", str(i))] = (
                read_outputs(output_file) if output_file.exists() else {}
            )
        outputs = {
            key: expand(value["value"]) for key, value in action["outputs"].items()
        }
        return outputs, step_outputs

    def test_checked_in_steps_without_a_spex_binary(self):
        outputs, _ = self.run_composite()
        self.assertEqual(outputs["scenario-id"], "e" * 64)
        self.assertEqual(outputs["spex-version"], "v0.2.0")
        self.assertTrue(Path(outputs["result-path"]).is_file())

    def test_prepare_uploads_without_credentials_or_dispatch(self):
        outputs, steps = self.run_composite(prepare=True)
        self.assertNotIn("submit", steps)
        self.assertEqual(outputs["request-artifact-id"], "123")
        self.assertTrue(Path(outputs["request-path"]).is_file())
        for key in ("scenario-id", "result-path", "runtime", "run-url", "cancellation"):
            self.assertEqual(outputs[key], "")

    def test_app_authentication_wiring_excludes_private_key_from_packaging(self):
        outputs, _ = self.run_composite(app=True)
        self.assertEqual(outputs["scenario-id"], "e" * 64)
        for path in Path(outputs["artifact-directory"]).iterdir():
            self.assertNotIn("PRIVATE_KEY_SENTINEL", path.read_text())

    def test_upload_failure_keeps_prepared_artifact_outputs(self):
        outputs, steps = self.run_composite(upload_fails=True)
        self.assertNotIn("submit", steps)
        self.assertTrue(Path(outputs["artifact-directory"]).is_dir())
        self.assertTrue(Path(outputs["request-path"]).is_file())
        self.assertRegex(outputs["request-id"], r"^[a-f0-9]{32}$")
        self.assertRegex(outputs["request-sha256"], r"^[a-f0-9]{64}$")
        for field in (
            "scenario-id",
            "runtime",
            "runtime-release",
            "spex-version",
            "result-path",
            "run-url",
            "cancellation",
        ):
            self.assertEqual(outputs[field], "")

    def test_failed_acceptance_keeps_verified_receiver_outputs(self):
        outputs, _ = self.run_composite(mode="failed")
        self.assertEqual(outputs["scenario-id"], "e" * 64)
        self.assertEqual(outputs["runtime"], "example/v1")
        self.assertEqual(outputs["runtime-release"], "v2.0.0")
        self.assertEqual(outputs["spex-version"], "v0.2.0")
        self.assertEqual(
            outputs["run-url"], "https://github.com/owner/runtime/actions/runs/42"
        )
        self.assertEqual(outputs["cancellation"], "not_requested")
        self.assertEqual(
            json.loads(Path(outputs["result-path"]).read_text())["outcome"], "failed"
        )

    def test_public_inputs_have_no_caller_client_selection(self):
        action = yaml.safe_load((ROOT / "action.yml").read_text())
        for name in (
            "runtime-config",
            "working-directory",
            "spex-version",
            "spex-executable",
            "scenario",
            "spex",
            "spex-files",
            "support-files",
        ):
            self.assertNotIn(name, action["inputs"])
        self.assertNotIn("scripts/remote.py", (ROOT / "action.yml").read_text())
        self.assertNotIn("scripts/run.py", (ROOT / "action.yml").read_text())
