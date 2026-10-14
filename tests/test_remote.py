import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_action import action

SPEC = importlib.util.spec_from_file_location(
    "spex_remote_action", Path(__file__).parents[1] / "scripts/remote.py"
)
remote = importlib.util.module_from_spec(SPEC)
with mock.patch.dict(sys.modules, {"run": action}):
    SPEC.loader.exec_module(remote)


class RemoteActionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.env = {
            "SPEX_ACTION_WORKSPACE": str(self.root),
            "SPEX_ACTION_EXECUTABLE": sys.executable,
            "GITHUB_OUTPUT": str(self.root / "outputs"),
        }
        self.calls = []
        self.code = 0
        self.respond_on_failure = False
        self.response_hook = lambda result: result

    def invoke(self, argv, workspace):
        self.calls.append(argv)
        self.assertEqual(workspace, self.root)
        if "--definition-inline-file" in argv:
            self.assertEqual(
                Path(argv[argv.index("--definition-inline-file") + 1]).read_text(),
                "SENTINEL\n$(no execution)",
            )
        if self.code and not self.respond_on_failure:
            return self.code, "SENTINEL_SECRET"
        base = (
            (workspace / argv[argv.index("--out") + 1]).parent
            if argv[2] == "package"
            else (workspace / argv[argv.index("--request") + 1]).parent
        )
        base.mkdir(parents=True, exist_ok=True)
        for name in (
            "scenario-package.zip",
            "request.json",
            "scenario.toml",
            "result.json",
        ):
            (base / name).write_text("fixture")
        result = {
            "request_id": "a" * 32,
            "scenario_id": "b" * 64,
            "runtime": "migration-testbench/v1",
            "package_path": str(base / "scenario-package.zip"),
            "request_path": str(base / "request.json"),
            "scenario_path": str(base / "scenario.toml"),
            "result_path": str(base / "result.json"),
            "artifact_directory": str(base),
            "run_id": 1,
            "run_url": "https://github.com/runtime/repo/actions/runs/1",
            "cancellation": "not_requested",
        }
        return self.code, json.dumps(self.response_hook(result))

    def execute(self, stage):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = remote.run(stage, self.env, invoke=self.invoke)
        self.assertNotIn("SENTINEL", output.getvalue())
        return code

    def test_prepare_only_packages_sources(self):
        self.env.update(
            SPEX_ACTION_DEFINITION="SENTINEL\n$(no execution)",
        )
        self.assertEqual(self.execute("prepare"), 0)
        self.assertEqual(self.calls[0][1:3], ["scenario", "package"])
        self.assertIn("--definition-inline-file", self.calls[0])
        self.assertNotIn("--support-file", self.calls[0])
        self.assertEqual(len(self.calls), 1)

    def submission(self):
        (self.root / "out").mkdir(exist_ok=True)
        (self.root / "out/request.json").write_text("fixture")
        self.env.update(
            SPEX_ACTION_REQUEST=str(self.root / "out/request.json"),
            SPEX_ACTION_RUNTIME_WORKFLOW="runtime/repo/.github/workflows/acceptance.yaml@v1",
            SPEX_ACTION_SOURCE_REPOSITORY="source/repo",
            SPEX_ACTION_SOURCE_RUN_ID="9",
            SPEX_ACTION_ARTIFACT_ID="11",
            SPEX_REMOTE_TOKEN="SENTINEL_TOKEN",
            SPEX_ACTION_REQUEST_ID="a" * 32,
            SPEX_ACTION_SCENARIO_ID="b" * 64,
        )

    def test_submit_delegates_protocol_without_token_in_argv(self):
        self.submission()
        self.assertEqual(self.execute("submit"), 0)
        self.assertEqual(self.calls[0][1:3], ["scenario", "submit"])
        self.assertIn("out/request.json", self.calls[0])
        self.assertNotIn("SENTINEL_TOKEN", self.calls[0])

    def test_failure_and_cancellation_survive_malformed_response(self):
        self.submission()
        for code in (3, 4, 130):
            self.code = code
            self.assertEqual(self.execute("submit"), code)

    def test_missing_destination_does_not_submit(self):
        with self.assertRaises(action.ActionError):
            self.execute("submit")
        self.assertEqual(self.calls, [])

    def test_output_failure_preserves_primary_exit(self):
        self.submission()
        self.code = 3
        with mock.patch.object(remote, "write_outputs", side_effect=OSError):
            self.assertEqual(self.execute("submit"), 3)

    def test_output_failure_fails_success(self):
        self.submission()
        with mock.patch.object(remote, "write_outputs", side_effect=OSError):
            self.assertEqual(self.execute("submit"), 1)

    def test_cancelled_receipt_without_result_still_exposes_run(self):
        self.submission()
        self.code, self.respond_on_failure = 130, True
        self.response_hook = lambda result: dict(
            result, result_path="", cancellation="requested"
        )
        self.assertEqual(self.execute("submit"), 130)
        outputs = (self.root / "outputs").read_text()
        self.assertIn("run-url<<", outputs)
        self.assertIn("requested", outputs)

    def test_invalid_response_never_exposes_partial_outputs(self):
        self.submission()
        invalid = [
            {"request_id": "invalid"},
            {"scenario_id": "c" * 64},
            {"runtime": "SENTINEL\n::warning::bad"},
            {"run_id": True},
            {"run_url": "https://untrusted.invalid/SENTINEL"},
            {"artifact_directory": str(self.root.parent)},
            {"result_path": "../SENTINEL"},
            {"result_path": ""},
            {"cancellation": "SENTINEL"},
        ]
        for fields in invalid:
            with self.subTest(fields=list(fields)):
                self.response_hook = lambda result, fields=fields: dict(
                    result, **fields
                )
                with self.assertRaises(action.ActionError):
                    self.execute("submit")
                self.assertEqual((self.root / "outputs").read_text(), "")

    def test_symlink_package_is_not_uploaded(self):
        def replace(result):
            path = Path(result["package_path"])
            target = self.root / "private-file"
            target.write_text("SENTINEL")
            path.unlink()
            path.symlink_to(target)
            return result

        self.response_hook = replace
        with self.assertRaises(action.ActionError):
            self.execute("prepare")
        self.assertEqual((self.root / "outputs").read_text(), "")

    def test_missing_package_is_not_uploaded(self):
        def remove(result):
            Path(result["package_path"]).unlink()
            return result

        self.response_hook = remove
        with self.assertRaises(action.ActionError):
            self.execute("prepare")

    def test_glob_working_directory_is_rejected_for_upload(self):
        nested = self.root / "unsafe[glob]"
        nested.mkdir()
        self.root = nested
        self.env["SPEX_ACTION_WORKSPACE"] = str(nested)
        with self.assertRaises(action.ActionError):
            self.execute("prepare")

    def test_cleanup_failure_preserves_execution_code(self):
        self.submission()
        original = tempfile.TemporaryDirectory
        for code in (0, 3, 130):
            self.code = code
            temporary = original()
            self.addCleanup(temporary.cleanup)
            real_cleanup = temporary.cleanup

            def fail_cleanup():
                real_cleanup()
                raise OSError("SENTINEL")

            with (
                mock.patch.object(
                    remote.tempfile, "TemporaryDirectory", return_value=temporary
                ),
                mock.patch.object(temporary, "cleanup", side_effect=fail_cleanup),
            ):
                self.assertEqual(self.execute("submit"), code or 1)

    def test_missing_token_fails_before_installation(self):
        self.submission()
        self.env.pop("SPEX_REMOTE_TOKEN")
        self.env.pop("SPEX_ACTION_EXECUTABLE")
        self.env["SPEX_ACTION_VERSION"] = "v1.0.0"
        installer = mock.Mock()
        with self.assertRaises(action.ActionError):
            remote.run("submit", self.env, installer=installer)
        installer.assert_not_called()

    def test_changed_client_fails_before_submission(self):
        self.submission()
        self.env["SPEX_ACTION_CLIENT_SHA"] = "0" * 64
        with self.assertRaises(action.ActionError):
            self.execute("submit")
        self.assertEqual(self.calls, [])

    def test_published_client_is_installed_only_once(self):
        from test_cli_integration import read_outputs

        self.env.pop("SPEX_ACTION_EXECUTABLE")
        self.env.update(SPEX_ACTION_VERSION="v1.0.0", RUNNER_TEMP=str(self.root))

        def installed(_version, directory):
            path = directory / "spex"
            path.write_text("fixture executable")
            path.chmod(0o700)
            return path

        installer = mock.Mock(side_effect=installed)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                remote.run(
                    "prepare", self.env, invoke=self.invoke, installer=installer
                ),
                0,
            )
        outputs = read_outputs(self.root / "outputs")
        self.assertTrue(Path(outputs["client-path"]).is_file())
        self.submission()
        self.env.pop("SPEX_ACTION_VERSION")
        self.env.update(
            SPEX_ACTION_REQUEST=outputs["request-path"],
            SPEX_ACTION_EXECUTABLE=outputs["client-path"],
            SPEX_ACTION_CLIENT_SHA=outputs["client-sha256"],
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                remote.run("submit", self.env, invoke=self.invoke, installer=installer),
                0,
            )
        self.assertEqual(installer.call_count, 1)


@unittest.skipUnless(os.getenv("SPEX_TEST_EXECUTABLE"), "candidate executable required")
class RemotePackageCLITests(unittest.TestCase):
    def test_all_authoring_modes_without_a_runtime_checkout(self):
        for mode in (
            "inline",
            "inline-multiple",
            "file",
            "committed",
            "inline-toml",
            "default-runtime",
            "pinned-runtime",
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env = {
                    "SPEX_ACTION_WORKSPACE": directory,
                    "SPEX_ACTION_EXECUTABLE": os.environ["SPEX_TEST_EXECUTABLE"],
                    "SPEX_ACTION_RUNTIME": "migration-testbench/v1",
                }
                (root / "source.feature").write_text("Feature: Smoke\n")
                if mode in ("inline", "default-runtime", "pinned-runtime"):
                    env["SPEX_ACTION_DEFINITION"] = "Feature: Smoke\n"
                    if mode == "default-runtime":
                        env.pop("SPEX_ACTION_RUNTIME")
                    elif mode == "pinned-runtime":
                        env["SPEX_ACTION_RUNTIME"] = "another/v1@v2.3.0"
                elif mode == "inline-multiple":
                    env["SPEX_ACTION_DEFINITION"] = (
                        "Feature: Smoke\n---\napiVersion: spex.scenario.v0.1\n"
                        "kind: Scenario\n---\nFeature: Second\n"
                    )
                elif mode == "file":
                    env["SPEX_ACTION_DEFINITION_FILES"] = "source.feature"
                else:
                    definition = (
                        'schema="spex.scenario/v1"\nruntime="migration-testbench/v1"\n'
                        'dependencies=["fixture.json"]\n[[tests]]\nfile="source.feature"\n'
                    )
                    (root / "fixture.json").write_text("{}")
                    if mode == "inline-toml":
                        env["SPEX_ACTION_DEFINITION"] = definition
                    else:
                        (root / "scenario.toml").write_text(definition)
                        env["SPEX_ACTION_DEFINITION_FILES"] = "scenario.toml"
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(remote.run("prepare", env), 0)
                packages = list(root.glob(".spex/action/*/scenario-package.zip"))
                self.assertEqual(len(packages), 1)
                with zipfile.ZipFile(packages[0]) as archive:
                    self.assertIn("scenario.toml", archive.namelist())
                    self.assertEqual(
                        "source.feature" in archive.namelist(),
                        mode in ("file", "committed", "inline-toml"),
                    )
                    self.assertEqual(
                        "fixture.json" in archive.namelist(),
                        mode in ("committed", "inline-toml"),
                    )
                    document = tomllib.loads(archive.read("scenario.toml").decode())
                    self.assertEqual(
                        len(document["tests"]), 3 if mode == "inline-multiple" else 1
                    )
                    if mode == "default-runtime":
                        self.assertEqual(document["runtime"], "")
                    if mode == "pinned-runtime":
                        self.assertEqual(document["runtime"], "another/v1@v2.3.0")
                    self.assertEqual(document["schema"], "spex.scenario-request/v1")


if __name__ == "__main__":
    unittest.main()
