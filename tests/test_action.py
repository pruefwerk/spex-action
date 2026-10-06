import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "spex_action", Path(__file__).parents[1] / "scripts/run.py"
)
action = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(action)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {
            "SPEX_ACTION_WORKSPACE": str(self.root),
            "SPEX_ACTION_EXECUTABLE": sys.executable,
            "GITHUB_OUTPUT": str(self.root / "outputs"),
        }
        self.calls = []
        self.sources = {}
        self.failure = ""
        self.code = 3
        self.malformed = False

    def invoke(self, argv, workspace):
        self.calls.append(argv)
        stage = argv[2]
        if stage == "build":
            for flag in ("--inline-file", "--runtime-config-file"):
                if flag in argv:
                    self.sources[flag] = Path(argv[argv.index(flag) + 1]).read_text()
        if stage == self.failure:
            return self.code, "PRIVATE_SENTINEL" if self.malformed else ""
        if stage == "explain":
            return 0, json.dumps(
                {
                    "scenario_id": "a" * 64,
                    "runtime": "migration-testbench/v1",
                    "name": "test\n::warning::not-a-command",
                    "plan": {"tests": [{}]},
                }
            )
        if stage == "run":
            return 0, json.dumps(
                {
                    "scenario_id": "a" * 64,
                    "scenario_path": str(self.root / "scenario.toml"),
                    "result_path": str(self.root / "result.json"),
                    "artifact_directory": str(self.root / "artifacts"),
                    "runtime": "migration-testbench/v1",
                    "outcome": "passed",
                }
            )
        return 0, ""

    def execute(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = action.run(self.env, invoke=self.invoke)
        return code, output.getvalue()

    def test_inline_and_configuration_are_files_not_shell(self):
        self.env.update(
            SPEX_ACTION_RUNTIME="migration-testbench/v1",
            SPEX_ACTION_NAME="$(touch should-not-exist); 'quoted'",
            SPEX_ACTION_INLINE="inline\nPRIVATE_SENTINEL\n",
            SPEX_ACTION_CONFIG='password_ref = "${PASSWORD}"',
        )
        code, output = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(self.sources["--inline-file"], self.env["SPEX_ACTION_INLINE"])
        self.assertEqual(
            self.sources["--runtime-config-file"], self.env["SPEX_ACTION_CONFIG"]
        )
        self.assertIn(self.env["SPEX_ACTION_NAME"], self.calls[0])
        self.assertNotIn("PRIVATE_SENTINEL", output)
        self.assertNotIn("\n::warning", output)
        self.assertFalse((self.root / "should-not-exist").exists())
        self.assertEqual([call[2] for call in self.calls], ["build", "explain", "run"])
        self.assertFalse(
            Path(self.calls[0][self.calls[0].index("--inline-file") + 1]).exists()
        )

    def test_external_file_paths_are_separate_arguments(self):
        self.env["SPEX_ACTION_FILES"] = "one file.spex\nsecond;literal.feature\n"
        self.assertEqual(self.execute()[0], 0)
        argv = self.calls[0]
        self.assertEqual(
            [argv[i + 1] for i, value in enumerate(argv) if value == "--spex-file"],
            ["one file.spex", "second;literal.feature"],
        )

    def test_committed_scenario_and_overlay_are_delegated(self):
        self.env.update(
            SPEX_ACTION_SCENARIO="acceptance/scenario.toml",
            SPEX_ACTION_CONFIG="[execution]\nfail_fast = false",
        )
        self.assertEqual(self.execute()[0], 0)
        self.assertIn("--scenario", self.calls[0])
        self.assertEqual(
            self.sources["--runtime-config-file"], self.env["SPEX_ACTION_CONFIG"]
        )

    def test_conflicting_sources_reach_spex_validation(self):
        self.env.update(
            SPEX_ACTION_SCENARIO="scenario.toml", SPEX_ACTION_INLINE="inline"
        )
        self.failure, self.code = "build", 2
        self.assertEqual(self.execute()[0], 2)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("--scenario", self.calls[0])
        self.assertIn("--inline-file", self.calls[0])

    def test_execution_failure_is_not_replaced_by_bad_summary(self):
        self.failure, self.malformed = "run", True
        code, output = self.execute()
        self.assertEqual(code, 3)
        self.assertNotIn("PRIVATE_SENTINEL", output)
        self.assertIn("scenario-path<<", (self.root / "outputs").read_text())

    def test_explanation_failure_prevents_execution(self):
        self.failure, self.code = "explain", 2
        self.assertEqual(self.execute()[0], 2)
        self.assertEqual(len(self.calls), 2)

    def test_output_failure_preserves_execution_failure(self):
        self.failure, self.code = "run", 130
        with mock.patch.object(
            action, "write_outputs", side_effect=OSError("PRIVATE_SENTINEL")
        ):
            code, output = self.execute()
        self.assertEqual(code, 130)
        self.assertNotIn("PRIVATE_SENTINEL", output)

    def test_output_failure_fails_otherwise_successful_action(self):
        with mock.patch.object(
            action, "write_outputs", side_effect=OSError("PRIVATE_SENTINEL")
        ):
            code, output = self.execute()
        self.assertEqual(code, 1)
        self.assertNotIn("PRIVATE_SENTINEL", output)

    def test_cleanup_failure_preserves_execution_failure(self):
        self.failure, self.code = "run", 3
        temporary = SimpleNamespace(
            name=str(self.root),
            cleanup=mock.Mock(side_effect=OSError("PRIVATE_SENTINEL")),
        )
        with mock.patch.object(
            action.tempfile, "TemporaryDirectory", return_value=temporary
        ):
            code, output = self.execute()
        self.assertEqual(code, 3)
        self.assertNotIn("PRIVATE_SENTINEL", output)

    def test_cleanup_failure_fails_otherwise_successful_action(self):
        temporary = SimpleNamespace(
            name=str(self.root),
            cleanup=mock.Mock(side_effect=OSError("PRIVATE_SENTINEL")),
        )
        with mock.patch.object(
            action.tempfile, "TemporaryDirectory", return_value=temporary
        ):
            code, output = self.execute()
        self.assertEqual(code, 1)
        self.assertNotIn("PRIVATE_SENTINEL", output)

    def test_outputs_use_delimited_values(self):
        path = self.root / "outputs"
        action.write_outputs(path, {"scenario-path": "one\nforged=value"})
        lines = path.read_text().splitlines()
        delimiter = lines[0].split("<<")[1]
        self.assertEqual(lines[-1], delimiter)
        self.assertEqual(lines[1:-1], ["one", "forged=value"])

    def test_executable_selection_is_explicit(self):
        for version, executable in (("", ""), ("v1.0.0", sys.executable)):
            self.env.update(
                SPEX_ACTION_VERSION=version, SPEX_ACTION_EXECUTABLE=executable
            )
            with self.assertRaises(action.ActionError):
                self.execute()

    def release_download(self, mismatch=False, link=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            member = tarfile.TarInfo("spex")
            payload = b"test executable"
            if link:
                member.type, member.linkname = tarfile.SYMTYPE, "../../outside"
                archive.addfile(member)
            else:
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            extra = tarfile.TarInfo("../../do-not-extract")
            extra.size = 1
            archive.addfile(extra, io.BytesIO(b"x"))
        data = buffer.getvalue()

        def download(url, destination, limit):
            if url.endswith(".sha256"):
                digest = "0" * 64 if mismatch else hashlib.sha256(data).hexdigest()
                name = url.split("/")[-1].removesuffix(".sha256")
                destination.write_text(digest + "  " + name + "\n")
            else:
                destination.write_bytes(data)

        return download

    def test_installer_verifies_checksum_and_extracts_only_binary(self):
        with (
            mock.patch.object(action, "download", self.release_download()),
            mock.patch.object(action.platform, "system", return_value="Linux"),
            mock.patch.object(action.platform, "machine", return_value="x86_64"),
        ):
            executable = action.install("v0.1.0-rc.99", self.root)
        self.assertEqual(executable.read_bytes(), b"test executable")
        self.assertEqual(executable.stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.root.parent / "do-not-extract").exists())

    def test_installer_refuses_checksum_mismatch(self):
        with mock.patch.object(
            action, "download", self.release_download(mismatch=True)
        ):
            with self.assertRaisesRegex(action.ActionError, "checksum mismatch"):
                action.install("v0.1.0", self.root)
        self.assertFalse((self.root / "spex").exists())

    def test_installer_refuses_link_binary(self):
        with mock.patch.object(action, "download", self.release_download(link=True)):
            with self.assertRaises(action.ActionError):
                action.install("v0.1.0", self.root)

    def test_installer_refuses_unversioned_or_unsafe_input(self):
        with mock.patch.object(action, "download") as download:
            for value in ("latest", "main", "../../bad", "v1.2.3;echo bad"):
                with self.assertRaises(action.ActionError):
                    action.install(value, self.root)
            download.assert_not_called()

    def test_missing_checksum_never_installs(self):
        def download(url, destination, limit):
            if url.endswith(".sha256"):
                raise OSError("PRIVATE_SENTINEL")
            destination.write_bytes(b"archive")

        with mock.patch.object(action, "download", download):
            with self.assertRaises(OSError):
                action.install("v0.1.0", self.root)
        self.assertFalse((self.root / "spex").exists())


if __name__ == "__main__":
    unittest.main()
