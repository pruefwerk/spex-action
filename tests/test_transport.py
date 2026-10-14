import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from test_cli_integration import read_outputs
from transport_fixture import API

SPEC = importlib.util.spec_from_file_location(
    "transport", Path(__file__).parents[1] / "scripts/transport.py"
)
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


class TransportCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cwd = self.root
        cwd = mock.patch.object(transport.Path, "cwd", side_effect=lambda: self.cwd)
        cwd.start()
        self.addCleanup(cwd.stop)
        self.env = {
            "RUNNER_TEMP": str(self.root),
            "GITHUB_WORKSPACE": str(self.root),
            "GITHUB_REPOSITORY": "owner/source",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "9",
            "GITHUB_OUTPUT": str(self.root / "outputs"),
            "SPEX_ACTION_RUNTIME_WORKFLOW": "owner/runtime/.github/workflows/acceptance.yaml@v1",
            "SPEX_ACTION_DEFINITION": "opaque text; receiver parses this",
            "SPEX_REMOTE_TOKEN": "PRIVATE_TOKEN_SENTINEL",
        }

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            prepared = transport.prepare(self.env)
        self.env.update(
            SPEX_ACTION_REQUEST=prepared["request-path"],
            SPEX_ACTION_REQUEST_ID=prepared["request-id"],
            SPEX_ACTION_REQUEST_SHA=prepared["request-sha256"],
            SPEX_ACTION_ARTIFACT_ID="123",
        )
        request = json.loads(Path(prepared["request-path"]).read_text())
        return request, prepared["request-sha256"]

    def submit(self, mode="passed", hook=None, **kwargs):
        request, digest = self.prepare()
        api = API(request, digest, mode)
        if hook:
            api.receipt_hook = hook
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = transport.submit(self.env, api=api, **kwargs)
        self.assertNotIn("PRIVATE_", output.getvalue())
        return code, api


class TransportTests(TransportCase):
    def test_prepare_mode_preserves_the_dispatch_envelope(self):
        self.env.update(
            SPEX_ACTION_EXECUTION_MODE="prepare",
            SPEX_ACTION_RUNTIME_WORKFLOW="",
            SPEX_REMOTE_TOKEN="",
        )
        request, digest = self.prepare()
        self.assertEqual(request["schema"], "spex.transport/v1")
        self.assertEqual(
            request["authoring"]["definition"], self.env["SPEX_ACTION_DEFINITION"]
        )
        self.assertEqual(len(digest), 64)
        self.assertNotIn("execution_mode", request)

    def test_prepare_refuses_irrelevant_credentials_and_destination(self):
        base = {
            **self.env,
            "SPEX_ACTION_EXECUTION_MODE": "prepare",
            "SPEX_ACTION_RUNTIME_WORKFLOW": "",
            "SPEX_REMOTE_TOKEN": "",
        }
        for key in (
            "SPEX_REMOTE_TOKEN",
            "SPEX_REMOTE_APP_ID",
            "SPEX_REMOTE_APP_PRIVATE_KEY",
            "SPEX_ACTION_RUNTIME_WORKFLOW",
        ):
            with self.subTest(key=key), self.assertRaises(transport.TransportError):
                transport.prepare({**base, key: "PRIVATE_SENTINEL"})
        with self.assertRaises(transport.TransportError):
            transport.prepare({**base, "SPEX_ACTION_EXECUTION_MODE": "unknown"})

    def test_runtime_configuration_stays_inside_definition(self):
        definition = (
            'schema = "spex.scenario-request/v1"\n'
            "[runtime_config.execution]\nfail_fast = false\n"
        )
        self.env["SPEX_ACTION_DEFINITION"] = definition
        self.env["SPEX_ACTION_CONFIG"] = "obsolete override must not be forwarded"
        request, _ = self.prepare()
        self.assertEqual(request["authoring"]["definition"], definition)
        self.assertNotIn("runtime_config", request["authoring"])
        self.assertNotIn("obsolete override", json.dumps(request))

    def test_reporting_failure_preserves_test_failure(self):
        for mode, expected in (("failed", 3), ("passed", 1), ("cancelled", 130)):
            request, digest = self.prepare()
            self.env["GITHUB_OUTPUT"] = str(
                Path(self.env["SPEX_ACTION_REQUEST"]).parent / "github-output"
            )
            api = API(request, digest, mode)
            original = transport.write_private

            def fail_result(path, data):
                if path.name == "result.json":
                    raise OSError("disk full")
                return original(path, data)

            with (
                self.subTest(mode=mode),
                mock.patch.object(transport, "write_private", side_effect=fail_result),
            ):
                self.assertEqual(transport.submit(self.env, api=api), expected)
            values = read_outputs(Path(self.env["GITHUB_OUTPUT"]))
            self.assertEqual(values["scenario-id"], "e" * 64)
            self.assertEqual(values["runtime"], "example/v1")
            self.assertEqual(values["runtime-release"], "v2.0.0")
            self.assertEqual(values["spex-version"], "v0.2.0")
            self.assertNotIn("result-path", values)

    def test_summary_write_failure_still_publishes_outputs(self):
        for mode, expected in (("passed", 1), ("failed", 3), ("cancelled", 130)):
            request, digest = self.prepare()
            api = API(request, digest, mode)
            original = transport.write_private

            def fail_summary(path, data):
                if path.name == "transport-result.json":
                    raise OSError("disk full")
                return original(path, data)

            with (
                self.subTest(mode=mode),
                mock.patch.object(transport, "write_private", side_effect=fail_summary),
            ):
                self.assertEqual(transport.submit(self.env, api=api), expected)
            values = read_outputs(Path(self.env["GITHUB_OUTPUT"]))
            self.assertTrue(Path(values["result-path"]).is_file())
            self.assertEqual(values["scenario-id"], "e" * 64)
            self.assertEqual(values["cancellation"], "not_requested")

    def test_output_publication_failure_keeps_summary_and_primary_failure(self):
        request, digest = self.prepare()
        with mock.patch.object(
            transport, "outputs", side_effect=OSError("output unavailable")
        ):
            self.assertEqual(
                transport.submit(self.env, api=API(request, digest, "failed")), 3
            )
        summary = json.loads(
            (
                Path(self.env["SPEX_ACTION_REQUEST"]).parent / "transport-result.json"
            ).read_text()
        )
        self.assertEqual(summary["scenario-id"], "e" * 64)
        self.assertTrue(Path(summary["result-path"]).is_file())

    def test_unresolved_identity_fields_reject_non_strings(self):
        for mode in ("error", "cancelled"):
            for i, value in enumerate((None, False, 0, [], {})):
                self.env["GITHUB_OUTPUT"] = str(self.root / f"invalid-{mode}-{i}")

                def invalid(receipt):
                    for field in (
                        "scenario_id",
                        "runtime",
                        "runtime_release",
                        "spex_version",
                    ):
                        receipt["result"][field] = value
                    return receipt

                with self.subTest(mode=mode, value=value):
                    code, _ = self.submit(mode, hook=invalid)
                    self.assertEqual(code, 1)
                    values = read_outputs(Path(self.env["GITHUB_OUTPUT"]))
                    for field in (
                        "scenario-id",
                        "runtime",
                        "runtime-release",
                        "spex-version",
                        "result-path",
                    ):
                        self.assertNotIn(field, values)

    def test_unresolved_identity_fields_are_empty_when_absent(self):
        def unresolved(receipt):
            for field in ("scenario_id", "runtime", "runtime_release", "spex_version"):
                receipt["result"].pop(field)
            return receipt

        code, _ = self.submit("error", hook=unresolved)
        self.assertEqual(code, 1)
        values = read_outputs(Path(self.env["GITHUB_OUTPUT"]))
        for field in ("scenario-id", "runtime", "runtime-release", "spex-version"):
            self.assertEqual(values[field], "")

    def test_https_redirect_does_not_forward_credentials(self):
        request = urllib.request.Request(
            "https://api.github.com/repos/a/b/actions/artifacts/1/zip",
            headers={"Authorization": "Bearer PRIVATE_TOKEN_SENTINEL"},
        )
        redirected = transport.SafeRedirect().redirect_request(
            request, None, 302, "", {}, "https://artifacts.example.test/data"
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))
        with self.assertRaises(transport.TransportError):
            transport.SafeRedirect().redirect_request(
                request, None, 302, "", {}, "http://artifacts.example.test/data"
            )

    def test_api_bounds_response_and_hides_http_errors(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"{}"
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(
            transport.urllib.request, "build_opener", return_value=opener
        ):
            client = transport.GitHub("PRIVATE_TOKEN_SENTINEL")
            self.assertEqual(
                client.call("GET", "/repos/a/b/commits/v1", timeout=2.5), {}
            )
            request = opener.open.call_args.args[0]
            self.assertEqual(
                request.get_header("Authorization"), "Bearer PRIVATE_TOKEN_SENTINEL"
            )
            self.assertGreater(opener.open.call_args.kwargs["timeout"], 0)
            self.assertLessEqual(opener.open.call_args.kwargs["timeout"], 2.5)
            response.read.return_value = b"x" * (transport.MAX_RECEIPT + 1)
            with self.assertRaises(transport.TransportError):
                client.call("GET", "/repos/a/b/commits/v1")
            opener.open.side_effect = OSError("PRIVATE_TOKEN_SENTINEL")
            with self.assertRaises(transport.TransportError) as failure:
                client.call("GET", "/repos/a/b/commits/v1")
            self.assertNotIn("PRIVATE_", str(failure.exception))

    def test_deadline_after_dispatch_uses_bounded_cancellation(self):
        request, digest = self.prepare()
        api = API(request, digest, "waiting")
        original = api.call
        now = [0.0]

        def call(method, path, body=None, **kwargs):
            result = original(method, path, body, **kwargs)
            if path.endswith("/dispatches"):
                now[0] = 2.0
            return result

        api.call = call
        self.env["SPEX_ACTION_WAIT_TIMEOUT"] = "1s"
        self.assertEqual(transport.submit(self.env, api=api, clock=lambda: now[0]), 130)
        cancellations = [
            timeout for _, path, _, timeout in api.calls if path.endswith("/cancel")
        ]
        self.assertEqual(cancellations, [10.0])

    def test_source_commit_mismatch_prevents_dispatch(self):
        request, digest = self.prepare()
        api = API(request, digest)
        original = api.call

        def call(method, path, body=None, **kwargs):
            result = original(method, path, body, **kwargs)
            if path.endswith("/artifacts/123"):
                result["workflow_run"]["head_sha"] = "b" * 40
            return result

        api.call = call
        self.assertEqual(transport.submit(self.env, api=api), 1)
        self.assertFalse(
            any(path.endswith("/dispatches") for _, path, _, _ in api.calls)
        )

    def test_receiver_admission_error_needs_no_invented_scenario(self):
        def unresolved(receipt):
            for field in ("scenario_id", "runtime", "runtime_release", "spex_version"):
                receipt["result"].pop(field)
            receipt["result"]["tests"] = []
            receipt["result"]["cleanup"] = "not_run"
            return receipt

        code, _ = self.submit("error", hook=unresolved)
        self.assertEqual(code, 1)
        result = json.loads(
            (Path(self.env["SPEX_ACTION_REQUEST"]).parent / "result.json").read_text()
        )
        self.assertEqual(result["scenario_id"], "")

    def test_prepare_keeps_raw_inputs_and_immutable_source_without_spex(self):
        with mock.patch.object(
            subprocess, "run", side_effect=AssertionError("no executable needed")
        ):
            request, digest = self.prepare()
        self.assertEqual(
            request["authoring"]["definition"], self.env["SPEX_ACTION_DEFINITION"]
        )
        self.assertEqual(request["source"]["commit"], "a" * 40)
        data = Path(self.env["SPEX_ACTION_REQUEST"]).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        self.assertNotIn(b"PRIVATE_TOKEN_SENTINEL", data)
        self.assertNotIn("scenario_id", request)

    def test_modes_and_paths(self):
        for key, value in (
            ("SPEX_ACTION_DEFINITION_FILES", "one.feature"),
            ("GITHUB_SHA", "main"),
            ("SPEX_ACTION_RUNTIME_WORKFLOW", "missing-ref"),
            ("SPEX_ACTION_WAIT_TIMEOUT", "0s"),
        ):
            with self.subTest(key=key), self.assertRaises(transport.TransportError):
                transport.prepare({**self.env, key: value})
        env = {
            **self.env,
            "SPEX_ACTION_DEFINITION": "",
            "SPEX_ACTION_DEFINITION_FILES": "a.feature\n./a.feature",
        }
        with contextlib.redirect_stdout(io.StringIO()):
            prepared = transport.prepare(env)
        request = json.loads(Path(prepared["request-path"]).read_text())
        self.assertEqual(
            request["authoring"]["definition_files"], ["a.feature", "a.feature"]
        )

    def test_source_base_comes_from_current_directory(self):
        self.cwd = self.root / "services" / "gateway"
        self.cwd.mkdir(parents=True)
        self.env["SPEX_ACTION_WORKSPACE"] = "ignored-old-setting"
        request, _ = self.prepare()
        self.assertEqual(request["source"]["root"], "services/gateway")
        self.cwd = self.root.parent
        with self.assertRaises(transport.TransportError):
            transport.prepare(self.env)

    def test_globs_are_not_expanded_on_the_caller(self):
        self.env["SPEX_ACTION_DEFINITION"] = ""
        self.env["SPEX_ACTION_DEFINITION_FILES"] = (
            "acceptance/**/*.feature\nacceptance/*.yaml"
        )
        request, _ = self.prepare()
        self.assertEqual(
            request["authoring"]["definition_files"],
            ["acceptance/**/*.feature", "acceptance/*.yaml"],
        )

    def test_result_success_and_projection(self):
        code, api = self.submit()
        self.assertEqual(code, 0)
        result = Path(self.env["SPEX_ACTION_REQUEST"]).parent / "result.json"
        self.assertEqual(json.loads(result.read_text())["spex_version"], "v0.2.0")
        self.assertNotIn("PRIVATE_", result.read_text())
        self.assertEqual(
            sum(path.endswith("/dispatches") for _, path, _, _ in api.calls), 1
        )

    def test_failures_never_report_success(self):
        for mode, expected in (
            ("failed", 3),
            ("error", 1),
            ("cancelled", 130),
            ("missing", 1),
            ("incomplete", 1),
            ("wrong-sha", 1),
            ("duplicate", 1),
            ("rerun", 1),
            ("lost-ack", 1),
            ("expired", 1),
        ):
            with self.subTest(mode=mode):
                code, api = self.submit(mode)
                self.assertEqual(code, expected)
                self.assertLessEqual(
                    sum(path.endswith("/dispatches") for _, path, _, _ in api.calls), 1
                )

    def test_receipt_identity_mismatch(self):
        for field in (
            "request_id",
            "request_sha256",
            "source_commit",
            "workflow_sha",
            "run_id",
            "run_attempt",
            "schema",
        ):

            def corrupt(receipt):
                receipt[field] = "wrong"
                return receipt

            with self.subTest(field=field):
                code, _ = self.submit(hook=corrupt)
                self.assertEqual(code, 1)

    def test_cancellation_before_dispatch(self):
        stop = threading.Event()
        stop.set()
        code, api = self.submit(stop=stop)
        self.assertEqual(code, 130)
        self.assertEqual(api.calls, [])

    def test_cancellation_after_dispatch(self):
        request, digest = self.prepare()
        stop = threading.Event()
        api = API(request, digest, "waiting")
        call = api.call

        def interrupt(method, path, **kwargs):
            result = call(method, path, **kwargs)
            if "workflow_dispatch" in path:
                stop.set()
            return result

        # Keep the method's positional-body signature intact.
        def wrapper(method, path, body=None, **kwargs):
            return interrupt(method, path, body=body, **kwargs)

        api.call = wrapper
        self.assertEqual(transport.submit(self.env, api=api, stop=stop), 130)
        self.assertTrue(any(path.endswith("/cancel") for _, path, _, _ in api.calls))

    def test_changed_request_and_duplicate_submission(self):
        request, digest = self.prepare()
        api = API(request, digest)
        self.assertEqual(transport.submit(self.env, api=api), 0)
        self.assertEqual(transport.submit(self.env, api=api), 1)
        self.assertEqual(
            sum(path.endswith("/dispatches") for _, path, _, _ in api.calls), 1
        )
        Path(self.env["SPEX_ACTION_REQUEST"]).write_text("changed")
        with self.assertRaises(transport.TransportError):
            transport.submit(self.env, api=api)

    def test_checkout_mismatch_and_uncommitted_changes(self):
        (self.root / ".git").mkdir()
        for head, status in (
            ("b" * 40, ""),
            ("a" * 40, " M modified.feature"),
            ("a" * 40, "?? generated.feature"),
        ):
            values = [
                subprocess.CompletedProcess([], 0, head, ""),
                subprocess.CompletedProcess([], 0, status, ""),
            ]
            with (
                mock.patch.object(subprocess, "run", side_effect=values),
                self.assertRaises(transport.TransportError),
            ):
                transport.prepare(self.env)


if __name__ == "__main__":
    unittest.main()
