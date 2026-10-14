"""Exercise renewal without real credentials or GitHub network access."""

import base64
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from test_transport import TransportCase, transport
from transport_fixture import API


class AuthenticationTests(TransportCase):
    def app_env(self):
        self.env.pop("SPEX_REMOTE_TOKEN", None)
        self.env.update(
            SPEX_REMOTE_APP_ID="123", SPEX_REMOTE_APP_PRIVATE_KEY="PRIVATE_KEY_SENTINEL"
        )
        return self.env

    def test_exactly_one_complete_authentication_mode(self):
        self.assertEqual(transport.credentials(self.env)[0], "PRIVATE_TOKEN_SENTINEL")
        self.app_env()
        self.assertEqual(transport.credentials(self.env)[1], "123")
        for changes in (
            {"SPEX_REMOTE_TOKEN": "PRIVATE_TOKEN_SENTINEL"},
            {"SPEX_REMOTE_APP_ID": ""},
            {"SPEX_REMOTE_APP_PRIVATE_KEY": ""},
            {"SPEX_REMOTE_APP_ID": "0"},
            {"SPEX_REMOTE_APP_ID": "$(command)"},
        ):
            with (
                self.subTest(changes=list(changes)),
                self.assertRaises(transport.TransportError) as error,
            ):
                transport.credentials({**self.env, **changes})
            self.assertNotIn("PRIVATE_", str(error.exception))

    def test_app_credentials_never_enter_request(self):
        self.app_env()
        request, _ = self.prepare()
        self.assertNotIn("PRIVATE_", json.dumps(request))

    def test_renewal_scopes_tokens_and_preserves_request_deadlines(self):
        client = transport.GitHub.from_env(self.app_env(), "owner/runtime")
        calls, now = [], [0.0]

        def request(method, path, token, body=None, timeout=30, binary=False):
            calls.append((method, path, token, body, timeout))
            if path.endswith("/installation"):
                return {"id": 10 if "owner/runtime" in path else 20}
            if path.endswith("/access_tokens"):
                return {
                    "token": "PRIVATE_" + str(len(calls)),
                    "expires_at": datetime.fromtimestamp(
                        10000 + now[0] + 3600, timezone.utc
                    ).isoformat(),
                }
            return {}

        with (
            mock.patch.object(transport.time, "monotonic", side_effect=lambda: now[0]),
            mock.patch.object(
                transport.time, "time", side_effect=lambda: 10000 + now[0]
            ),
            mock.patch.object(
                transport.AppCredentials, "jwt", return_value="PRIVATE_JWT"
            ),
            mock.patch.object(client, "_request", side_effect=request),
        ):
            client.call("GET", "/repos/owner/runtime/commits/v1", timeout=10)
            client.call("GET", "/repos/owner/source/actions/artifacts/1")
            client.call("GET", "/repos/owner/runtime/actions/runs")
            self.assertEqual(sum(p.endswith("/access_tokens") for _, p, *_ in calls), 2)
            now[0] = 3300
            client.call(
                "POST", "/repos/owner/runtime/actions/runs/42/cancel", timeout=5
            )
            self.assertEqual(sum(p.endswith("/access_tokens") for _, p, *_ in calls), 3)
        issued = [c for c in calls if c[1].endswith("/access_tokens")]
        self.assertEqual(
            issued[0][3],
            {
                "repositories": ["runtime"],
                "permissions": {"actions": "write", "contents": "read"},
            },
        )
        self.assertEqual(issued[1][3]["permissions"]["actions"], "read")
        self.assertEqual(calls[-1][2], "PRIVATE_9")
        self.assertLessEqual(calls[-1][4], 5)

    def test_refresh_failure_never_reuses_expired_token_or_retries_dispatch(self):
        client = transport.GitHub.from_env(self.app_env(), "owner/runtime")
        client.token.tokens["owner/runtime"] = ("PRIVATE_EXPIRED", -1)
        with (
            mock.patch.object(
                transport.AppCredentials, "jwt", return_value="PRIVATE_JWT"
            ),
            mock.patch.object(
                client,
                "_request",
                side_effect=transport.TransportError("issuance failed"),
            ) as request,
            self.assertRaises(transport.TransportError),
        ):
            client.call(
                "POST", "/repos/owner/runtime/actions/workflows/test.yaml/dispatches"
            )
        self.assertEqual(request.call_count, 1)
        self.assertTrue(request.call_args.args[1].endswith("/installation"))

    def test_invalid_expiry_fails_without_leaking_response(self):
        for expiry in (
            None,
            "not a date",
            "2026-01-01T00:00:00",
            "1970-01-01T00:00:00Z",
        ):
            app = transport.AppCredentials("123", "PRIVATE_KEY", "owner/runtime")
            request = mock.Mock(
                side_effect=[
                    {"id": 1},
                    {"token": "PRIVATE_TOKEN", "expires_at": expiry},
                ]
            )
            with (
                mock.patch.object(app, "jwt", return_value="PRIVATE_JWT"),
                self.assertRaises(transport.TransportError) as error,
            ):
                app.token("owner/runtime", request, lambda: 5)
            self.assertNotIn("PRIVATE_", str(error.exception))
            self.assertEqual(app.tokens, {})

    def test_refresh_consumes_the_original_budget(self):
        client = transport.GitHub.from_env(self.app_env(), "owner/runtime")
        with (
            mock.patch.object(transport.time, "monotonic", side_effect=[0, 0, 6]),
            mock.patch.object(
                transport.AppCredentials, "jwt", return_value="PRIVATE_JWT"
            ),
            mock.patch.object(client, "_request") as request,
            self.assertRaises(transport.TransportError),
        ):
            client.call("GET", "/repos/owner/runtime/actions/runs", timeout=5)
        request.assert_not_called()

    def test_mask_before_using_issued_token(self):
        app = transport.AppCredentials("123", "PRIVATE_KEY", "owner/runtime", mask=True)
        expiry = datetime.fromtimestamp(
            transport.time.time() + 3600, timezone.utc
        ).isoformat()
        output = io.StringIO()
        with (
            mock.patch.object(app, "jwt", return_value="PRIVATE_JWT"),
            contextlib.redirect_stdout(output),
        ):
            token = app.token(
                "owner/runtime",
                mock.Mock(
                    side_effect=[
                        {"id": 1},
                        {"token": "PRIVATE_TOKEN", "expires_at": expiry},
                    ]
                ),
                lambda: 5,
            )
        self.assertEqual(token, "PRIVATE_TOKEN")
        self.assertEqual(output.getvalue(), "::add-mask::PRIVATE_TOKEN\n")

    def test_transport_uses_renewable_credentials_for_submission_and_result(self):
        self.app_env()
        request, digest = self.prepare()
        fixture = API(request, digest, "passed")
        client = transport.GitHub.from_env(self.env, "owner/runtime")
        with (
            mock.patch.object(transport.GitHub, "from_env", return_value=client),
            mock.patch.object(
                transport.AppCredentials, "token", return_value="PRIVATE_TOKEN"
            ) as token,
            mock.patch.object(
                client,
                "_request",
                side_effect=lambda method,
                path,
                bearer,
                body=None,
                timeout=30,
                binary=False: fixture.call(
                    method, path, body, timeout=timeout, binary=binary
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(transport.submit(self.env), 0)
        repos = {call.args[0] for call in token.call_args_list}
        self.assertEqual(repos, {"owner/runtime", "owner/source"})

    def test_multi_hour_result_and_timeout_cancellation_refresh_tokens(self):
        for finish, expected in ((True, 0), (False, 130)):
            with self.subTest(finish=finish):
                self.app_env()
                request, digest = self.prepare()
                fixture = API(request, digest, "waiting")
                client = transport.GitHub.from_env(self.env, "owner/runtime")
                now, issued, used = [0.0], [], []
                stop = mock.Mock()
                stop.is_set.return_value = False

                def wait(_seconds):
                    now[0] += 3600
                    if finish and now[0] >= 10800:
                        fixture.mode = "passed"

                stop.wait.side_effect = wait

                def exchange(method, path, bearer, body=None, timeout=30, binary=False):
                    if path.endswith("/installation"):
                        return {"id": 1}
                    if path.endswith("/access_tokens"):
                        token = "PRIVATE_TOKEN_" + str(len(issued))
                        issued.append(token)
                        return {
                            "token": token,
                            "expires_at": datetime.fromtimestamp(
                                10000 + now[0] + 3600, timezone.utc
                            ).isoformat(),
                        }
                    used.append((path, bearer))
                    return fixture.call(
                        method, path, body, timeout=timeout, binary=binary
                    )

                with (
                    mock.patch.object(
                        transport.time, "monotonic", side_effect=lambda: now[0]
                    ),
                    mock.patch.object(
                        transport.time, "time", side_effect=lambda: 10000 + now[0]
                    ),
                    mock.patch.object(
                        transport.AppCredentials, "jwt", return_value="PRIVATE_JWT"
                    ),
                    mock.patch.object(
                        transport.GitHub, "from_env", return_value=client
                    ),
                    mock.patch.object(client, "_request", side_effect=exchange),
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual(
                        transport.submit(self.env, stop=stop, clock=lambda: now[0]),
                        expected,
                    )
                self.assertGreaterEqual(len(issued), 5)
                suffix = "/zip" if finish else "/cancel"
                self.assertTrue(used[-1][0].endswith(suffix))
                self.assertEqual(used[-1][1], issued[-1])
                self.assertEqual(sum(p.endswith("/dispatches") for p, _ in used), 1)
                for path in Path(self.env["SPEX_ACTION_REQUEST"]).parent.iterdir():
                    self.assertNotIn("PRIVATE_", path.read_text())


class SigningTests(unittest.TestCase):
    def test_real_rsa_signature_and_private_file_cleanup(self):
        key = subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
            ],
            capture_output=True,
            check=True,
        ).stdout
        app = transport.AppCredentials("123", key.decode(), "owner/runtime")
        original, paths = transport.write_private, []

        def write(path, data):
            original(path, data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            paths.append(path)

        with mock.patch.object(transport, "write_private", side_effect=write):
            jwt = app.jwt(5)
        header, payload, signature = jwt.split(".")

        def decode(value):
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

        self.assertEqual(json.loads(decode(header))["alg"], "RS256")
        claims = json.loads(decode(payload))
        self.assertEqual(claims["iss"], "123")
        self.assertEqual(claims["exp"] - claims["iat"], 600)
        self.assertTrue(all(not path.exists() for path in paths))
        with tempfile.TemporaryDirectory() as directory:
            public = subprocess.run(
                ["openssl", "pkey", "-pubout"],
                input=key,
                capture_output=True,
                check=True,
            ).stdout
            root = Path(directory)
            (root / "public.pem").write_bytes(public)
            (root / "signature").write_bytes(decode(signature))
            verified = subprocess.run(
                [
                    "openssl",
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(root / "public.pem"),
                    "-signature",
                    str(root / "signature"),
                ],
                input=f"{header}.{payload}".encode(),
                capture_output=True,
            )
            self.assertEqual(verified.returncode, 0)

    def test_signing_failure_hides_diagnostics_and_removes_key(self):
        app = transport.AppCredentials("123", "PRIVATE_KEY_SENTINEL", "owner/runtime")
        with self.assertRaises(transport.TransportError) as error:
            app.jwt(5)
        self.assertNotIn("PRIVATE_", str(error.exception))
