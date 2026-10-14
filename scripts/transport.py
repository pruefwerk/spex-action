#!/usr/bin/env python3
"""GitHub transport only. The receiver owns all definition/runtime semantics."""

import base64
import hashlib
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

REPO = r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9][A-Za-z0-9_.-]*"
SHA = r"[a-f0-9]{40}"
DIGEST = r"[a-f0-9]{64}"
MAX_REQUEST = 4 << 20
MAX_RECEIPT = 2 << 20


class TransportError(Exception):
    pass


class Cancelled(TransportError):
    pass


def require(condition, message):
    if not condition:
        raise TransportError(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def integer(value):
    return type(value) is int and value > 0


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_private(path, data):
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(data)


def outputs(env, values):
    if not env.get("GITHUB_OUTPUT"):
        return
    with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        for key, value in values.items():
            marker = "spex_" + uuid.uuid4().hex
            stream.write(f"{key}<<{marker}\n{value}\n{marker}\n")


def relative(value, allow_root=False):
    path = PurePosixPath(value)
    require(
        bool(value)
        and not path.is_absolute()
        and ".." not in path.parts
        and not any(c in value for c in "\\\x00\r\n")
        and (allow_root or str(path) != "."),
        "paths must remain inside the source workspace",
    )
    return str(path)


def workflow(value):
    match = re.fullmatch(
        rf"({REPO})/\.github/workflows/([A-Za-z0-9_-]+\.ya?ml)@([^@\s]+)", value
    )
    require(match is not None, "runtime-workflow must name repository/workflow@ref")
    assert match is not None
    return match[1], match[2], match[3]


def duration(value):
    parts = re.findall(r"([0-9]+)(h|m|s)", value)
    require(parts and "".join(a + b for a, b in parts) == value, "invalid wait-timeout")
    seconds = sum(int(a) * {"h": 3600, "m": 60, "s": 1}[b] for a, b in parts)
    require(0 < seconds <= 86400, "wait-timeout must be between 1s and 24h")
    return seconds


def execution_mode(env):
    mode = env.get("SPEX_ACTION_EXECUTION_MODE") or "dispatch"
    require(
        mode in ("dispatch", "prepare"), "execution-mode must be dispatch or prepare"
    )
    if mode == "prepare":
        require(
            not any(
                env.get(key)
                for key in (
                    "SPEX_ACTION_RUNTIME_WORKFLOW",
                    "SPEX_REMOTE_TOKEN",
                    "SPEX_REMOTE_APP_ID",
                    "SPEX_REMOTE_APP_PRIVATE_KEY",
                )
            ),
            "prepare mode does not accept a receiver destination or transport credentials",
        )
    else:
        workflow(env.get("SPEX_ACTION_RUNTIME_WORKFLOW", ""))
    return mode


def prepare(env):
    execution_mode(env)
    duration(env.get("SPEX_ACTION_WAIT_TIMEOUT") or "6h")
    repo, sha = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_SHA", "")
    require(
        matches(REPO, repo) and matches(SHA, sha), "immutable GitHub source is required"
    )
    run_id = env.get("GITHUB_RUN_ID", "")
    require(run_id.isdigit() and int(run_id) > 0, "source run ID is required")
    checkout = Path(env.get("GITHUB_WORKSPACE", ".")).resolve()
    try:
        root = Path.cwd().resolve().relative_to(checkout).as_posix()
    except ValueError:
        raise TransportError(
            "caller directory must be inside the source workspace"
        ) from None
    inline = env.get("SPEX_ACTION_DEFINITION", "")
    files = [
        relative(p.strip())
        for p in env.get("SPEX_ACTION_DEFINITION_FILES", "").splitlines()
        if p.strip()
    ]
    require(not (inline and files), "choose definition or definition-files, not both")
    # Local files are never uploaded. Reject a checkout that could mislead the
    # caller about the source revision the receiver will actually read.
    if (checkout / ".git").exists():

        def git(*args):
            result = subprocess.run(
                ["git", "--literal-pathspecs", "-C", str(checkout), *args],
                capture_output=True,
                text=True,
                timeout=30,
            )
            require(result.returncode == 0, "cannot verify source checkout")
            return result.stdout.strip()

        require(
            git("rev-parse", "HEAD") == sha,
            "checkout differs from the workflow source commit",
        )
        require(
            not git("status", "--porcelain", "--untracked-files=all", "--", root),
            "source workspace has uncommitted files; commit them or use inline definitions",
        )
    request_id = uuid.uuid4().hex
    request = {
        "schema": "spex.transport/v1",
        "request_id": request_id,
        "source": {
            "repository": repo,
            "commit": sha,
            "root": root,
            "run_id": int(run_id),
        },
        "authoring": {
            "definition": inline,
            "definition_files": files,
            "runtime": env.get("SPEX_ACTION_RUNTIME", ""),
            "name": env.get("SPEX_ACTION_NAME", ""),
            "description": env.get("SPEX_ACTION_DESCRIPTION", ""),
            "timeout": env.get("SPEX_ACTION_TIMEOUT", ""),
        },
    }
    data = encode(request)
    require(len(data) <= MAX_REQUEST, "submission exceeds size limit")
    base = Path(
        tempfile.mkdtemp(prefix="spex-request-", dir=env.get("RUNNER_TEMP"))
    ).resolve()
    require(
        not any(c in str(base) for c in "\r\n*?[]!{}"),
        "artifact directory contains pattern characters",
    )
    path = base / "request.json"
    write_private(path, data)
    result = {
        "request-id": request_id,
        "request-path": str(path),
        "request-sha256": hashlib.sha256(data).hexdigest(),
        "artifact-directory": str(base),
    }
    outputs(env, result)
    print("Spex request prepared: " + request_id)
    return result


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(
            urllib.parse.urlsplit(newurl).scheme == "https", "unsafe artifact redirect"
        )
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


def credentials(env):
    token = env.get("SPEX_REMOTE_TOKEN", "")
    app_id = env.get("SPEX_REMOTE_APP_ID", "")
    key = env.get("SPEX_REMOTE_APP_PRIVATE_KEY", "")
    require(
        bool(token) != bool(app_id or key),
        "choose remote-token or both app-id and app-private-key",
    )
    if not token:
        require(
            app_id.isdigit() and int(app_id) > 0 and bool(key),
            "app-id must be positive and app-private-key must be provided",
        )
        require(len(key) <= 65536, "app-private-key exceeds size limit")
    return token, app_id, key


class AppCredentials:
    """Cache a separate, renewable installation token for each repository."""

    def __init__(self, app_id, private_key, receiver, mask=False):
        self.app_id, self.private_key, self.receiver = app_id, private_key, receiver
        self.mask = mask
        self.tokens = {}

    def jwt(self, timeout):
        def b64(data):
            return base64.urlsafe_b64encode(data).rstrip(b"=")

        now = int(time.time())
        message = b".".join(
            (
                b64(encode({"alg": "RS256", "typ": "JWT"})),
                b64(encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id})),
            )
        )
        # Keep the key out of argv and remove its private temporary file as soon
        # as signing finishes. Never surface OpenSSL diagnostics.
        with tempfile.TemporaryDirectory(prefix="spex-auth-") as directory:
            path = Path(directory) / "key.pem"
            write_private(path, self.private_key.encode())
            try:
                signed = subprocess.run(
                    ["openssl", "dgst", "-sha256", "-sign", str(path)],
                    input=message,
                    capture_output=True,
                    timeout=timeout,
                    env={
                        k: v
                        for k, v in os.environ.items()
                        if k not in ("SPEX_REMOTE_TOKEN", "SPEX_REMOTE_APP_PRIVATE_KEY")
                    },
                )
            except (OSError, subprocess.TimeoutExpired):
                raise TransportError("GitHub App signing failed") from None
            require(signed.returncode == 0, "GitHub App signing failed")
        return (message + b"." + b64(signed.stdout)).decode()

    def token(self, repo, request, timeout):
        cached = self.tokens.get(repo)
        if cached and time.monotonic() < cached[1]:
            return cached[0]
        jwt = self.jwt(timeout())
        installation = request(
            "GET", f"/repos/{repo}/installation", jwt, timeout=timeout()
        )
        require(integer(installation.get("id")), "GitHub App installation unavailable")
        result = request(
            "POST",
            f"/app/installations/{installation['id']}/access_tokens",
            jwt,
            body={
                "repositories": [repo.split("/")[1]],
                "permissions": {
                    "actions": "write" if repo == self.receiver else "read",
                    "contents": "read",
                },
            },
            timeout=timeout(),
        )
        token = result.get("token")
        require(
            isinstance(token, str)
            and bool(token)
            and not any(c in token for c in "\r\n"),
            "invalid GitHub App token response",
        )
        try:
            expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
            require(expiry.tzinfo is not None, "invalid GitHub App token expiry")
            lifetime = expiry.timestamp() - time.time()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise TransportError("invalid GitHub App token expiry") from None
        require(lifetime > 300, "GitHub App token expires too soon")
        if self.mask:
            print("::add-mask::" + token.replace("%", "%25"))
        self.tokens[repo] = (token, time.monotonic() + lifetime - 300)
        return token


class GitHub:
    def __init__(self, token):
        require(bool(token), "remote-token is required")
        self.token = token

    @classmethod
    def from_env(cls, env, receiver):
        token, app_id, key = credentials(env)
        return cls(
            token
            or AppCredentials(
                app_id, key, receiver, env.get("GITHUB_ACTIONS") == "true"
            )
        )

    def call(self, method, path, body=None, timeout: float = 30, binary=False):
        require(path.startswith("/repos/"), "invalid GitHub API path")
        deadline = time.monotonic() + timeout

        def remaining():
            value = deadline - time.monotonic()
            require(value > 0, "GitHub request deadline exceeded")
            return value

        token = self.token
        if isinstance(token, AppCredentials):
            repo = "/".join(path.split("/")[2:4])
            require(matches(REPO, repo), "invalid GitHub repository")
            token = token.token(repo, self._request, remaining)
        return self._request(method, path, token, body, remaining(), binary)

    @staticmethod
    def _request(method, path, token, body=None, timeout: float = 30, binary=False):
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=None if body is None else encode(body),
            method=method,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
        )
        try:
            with urllib.request.build_opener(SafeRedirect()).open(
                request, timeout=timeout
            ) as response:
                data = response.read(MAX_RECEIPT + 1)
            require(len(data) <= MAX_RECEIPT, "GitHub response exceeds size limit")
            return data if binary else (json.loads(data) if data else {})
        except (OSError, ValueError, urllib.error.URLError):
            raise TransportError(
                "GitHub request failed; check access and remote run"
            ) from None


def paginated(api, path, field, timeout):
    for page in range(1, 11):
        result = api.call(
            "GET",
            path + ("&" if "?" in path else "?") + f"per_page=100&page={page}",
            timeout=timeout(),
        )
        values = result.get(field)
        require(isinstance(values, list), "invalid GitHub list response")
        yield from values
        if len(values) < 100:
            return
    raise TransportError("GitHub listing exceeds correlation limit")


def find_run(api, repo, file, request_id, sha, timeout):
    found = []
    for run in paginated(
        api,
        f"/repos/{repo}/actions/workflows/{file}/runs?event=workflow_dispatch",
        "workflow_runs",
        timeout,
    ):
        if run.get("display_title") != "spex:" + request_id:
            continue
        require(
            run.get("head_sha") == sha
            and run.get("run_attempt") == 1
            and integer(run.get("id")),
            "remote run identity mismatch",
        )
        found.append(run)
    require(len(found) <= 1, "multiple remote runs match this request")
    return found[0] if found else None


def receipt_result(api, repo, run, request, digest, workflow_sha, timeout):
    artifacts = [
        a
        for a in paginated(
            api,
            f"/repos/{repo}/actions/runs/{run['id']}/artifacts",
            "artifacts",
            timeout,
        )
        if a.get("name") == "spex-result-" + request["request_id"]
    ]
    require(
        len(artifacts) == 1
        and not artifacts[0].get("expired")
        and integer(artifacts[0].get("id")),
        "missing or ambiguous result artifact",
    )
    data = api.call(
        "GET",
        f"/repos/{repo}/actions/artifacts/{artifacts[0]['id']}/zip",
        binary=True,
        timeout=timeout(),
    )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1, "invalid result archive")
        entry = entries[0]
        mode = entry.external_attr >> 16
        require(
            entry.filename == "receipt.json"
            and not entry.is_dir()
            and (not stat.S_IFMT(mode) or stat.S_ISREG(mode))
            and entry.file_size <= MAX_RECEIPT,
            "invalid result archive entry",
        )
        with archive.open(entry) as stream:
            content = stream.read(MAX_RECEIPT + 1)
        require(len(content) <= MAX_RECEIPT, "receipt exceeds size limit")
        receipt = json.loads(content)
    expected = {
        "schema": "spex.transport-result/v1",
        "request_id": request["request_id"],
        "request_sha256": digest,
        "source_commit": request["source"]["commit"],
        "workflow_sha": workflow_sha,
        "run_id": run["id"],
        "run_attempt": 1,
    }
    require(
        all(receipt.get(k) == v for k, v in expected.items()),
        "result receipt identity mismatch",
    )
    require(
        integer(receipt.get("run_id")) and type(receipt.get("run_attempt")) is int,
        "invalid receipt run identity",
    )
    result = receipt.get("result", {})
    outcome = result.get("outcome")
    require(
        outcome in ("passed", "failed", "error", "cancelled"), "invalid result outcome"
    )
    require(result.get("schema") == "spex.result/v1", "unsupported result schema")
    resolved = {
        key: result.get(key, "")
        for key in ("scenario_id", "runtime", "runtime_release", "spex_version")
    }
    require(
        all(isinstance(value, str) for value in resolved.values()),
        "resolved identity fields must be strings or omitted",
    )
    if outcome in ("passed", "failed") or any(resolved.values()):
        require(
            matches(DIGEST, resolved["scenario_id"])
            and matches(r"[a-z][a-z0-9-]*/v[1-9][0-9]*", resolved["runtime"]),
            "invalid resolved scenario",
        )
        for key in ("runtime_release", "spex_version"):
            require(
                matches(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", resolved[key])
                and resolved[key] != "latest",
                "exact execution versions required",
            )
    tests = result.get("tests", [])
    require(
        isinstance(tests, list)
        and all(
            isinstance(t, dict)
            and t.get("outcome") in ("passed", "failed", "error", "cancelled")
            for t in tests
        ),
        "invalid test results",
    )
    cleanup = result.get("cleanup")
    require(
        cleanup in ("succeeded", "not_run", "failed", "incomplete"),
        "invalid cleanup status",
    )
    if outcome == "passed":
        require(
            run.get("conclusion") == "success"
            and integer(receipt.get("planned_tests"))
            and len(tests) == receipt["planned_tests"]
            and all(t["outcome"] == "passed" for t in tests)
            and not result.get("problems")
            and cleanup in ("succeeded", "not_run"),
            "receipt does not establish complete success",
        )
    # Project only structured fields; receiver diagnostics may contain secrets.
    return {
        "schema": "spex.result/v1",
        **resolved,
        "outcome": outcome,
        "cleanup": cleanup,
        "tests": [{"outcome": t["outcome"]} for t in tests],
    }


def submit(env, api=None, stop=None, clock=time.monotonic):
    stop = stop or threading.Event()
    deadline = clock() + duration(env.get("SPEX_ACTION_WAIT_TIMEOUT") or "6h")
    repo, file, ref = workflow(env.get("SPEX_ACTION_RUNTIME_WORKFLOW", ""))
    api = api or GitHub.from_env(env, repo)
    path = Path(env.get("SPEX_ACTION_REQUEST", ""))
    require(
        path.name == "request.json" and not path.is_symlink() and path.is_file(),
        "prepared request unavailable",
    )
    with path.open("rb") as stream:
        data = stream.read(MAX_REQUEST + 1)
    digest = hashlib.sha256(data).hexdigest()
    require(
        len(data) <= MAX_REQUEST and digest == env.get("SPEX_ACTION_REQUEST_SHA"),
        "prepared request changed",
    )
    request = json.loads(data)
    require(
        request.get("schema") == "spex.transport/v1"
        and request.get("request_id") == env.get("SPEX_ACTION_REQUEST_ID")
        and matches(r"[a-f0-9]{32}", request.get("request_id")),
        "invalid request metadata",
    )
    artifact_id = env.get("SPEX_ACTION_ARTIFACT_ID", "")
    require(
        artifact_id.isdigit() and int(artifact_id) > 0, "uploaded artifact ID required"
    )
    values = {
        "artifact-directory": str(path.parent),
        "request-id": request["request_id"],
        "cancellation": "not_requested",
    }
    run, workflow_sha, dispatched, code = None, "", False, 1

    def timeout():
        if stop.is_set() or clock() >= deadline:
            raise Cancelled("remote wait cancelled or expired")
        return min(30, max(0.01, deadline - clock()))

    try:
        revision = api.call(
            "GET",
            f"/repos/{repo}/commits/{urllib.parse.quote(ref, safe='')}",
            timeout=timeout(),
        )
        workflow_sha = revision.get("sha", "")
        require(matches(SHA, workflow_sha), "invalid workflow revision")
        source = request["source"]
        artifact = api.call(
            "GET",
            f"/repos/{source['repository']}/actions/artifacts/{artifact_id}",
            timeout=timeout(),
        )
        require(
            artifact.get("name") == "spex-request-" + request["request_id"]
            and not artifact.get("expired")
            and artifact.get("workflow_run", {}).get("id") == source["run_id"]
            and artifact.get("workflow_run", {}).get("head_sha") == source["commit"],
            "uploaded artifact does not belong to source run",
        )
        # Exclusive marker prevents unsafe retry after an uncertain dispatch.
        write_private(
            path.parent / "dispatch.json",
            encode({"workflow_sha": workflow_sha, "request_id": request["request_id"]}),
        )
        dispatched = True
        api.call(
            "POST",
            f"/repos/{repo}/actions/workflows/{file}/dispatches",
            {
                "ref": ref,
                "inputs": {
                    "transport_schema": "spex.transport/v1",
                    "request_id": request["request_id"],
                    "request_sha256": digest,
                    "source_repository": source["repository"],
                    "source_run_id": str(source["run_id"]),
                    "source_artifact_id": artifact_id,
                },
            },
            timeout=timeout(),
        )
        while True:
            timeout()
            run = find_run(
                api, repo, file, request["request_id"], workflow_sha, timeout
            )
            if run:
                values["run-url"] = (
                    f"https://github.com/{repo}/actions/runs/{run['id']}"
                )
            if run and run.get("status") == "completed":
                break
            stop.wait(min(10, timeout()))
        result = receipt_result(api, repo, run, request, digest, workflow_sha, timeout)
        code = {"passed": 0, "failed": 3, "error": 1, "cancelled": 130}[
            result["outcome"]
        ]
        for output, field in (
            ("scenario-id", "scenario_id"),
            ("runtime", "runtime"),
            ("runtime-release", "runtime_release"),
            ("spex-version", "spex_version"),
        ):
            values[output] = result[field]
        result_path = path.parent / "result.json"
        write_private(result_path, encode(result))
        values["result-path"] = str(result_path)
    except Cancelled:
        code = 130
        print(
            "Remote wait cancelled or expired; inspect the receiver run",
            file=sys.stderr,
        )
    except Exception:
        code = code or 1
        print(
            "Remote submission failed; inspect retained request and receiver evidence. Do not blindly resubmit.",
            file=sys.stderr,
        )
    finally:
        if dispatched and (not run or run.get("status") != "completed"):
            values["cancellation"] = "unconfirmed"
            cleanup_deadline = clock() + 10

            def cleanup_timeout():
                require(clock() < cleanup_deadline, "cancellation deadline exceeded")
                return max(0.01, cleanup_deadline - clock())

            try:
                if not run:
                    run = find_run(
                        api,
                        repo,
                        file,
                        request["request_id"],
                        workflow_sha,
                        cleanup_timeout,
                    )
                if run:
                    values["run-url"] = (
                        f"https://github.com/{repo}/actions/runs/{run['id']}"
                    )
                    api.call(
                        "POST",
                        f"/repos/{repo}/actions/runs/{run['id']}/cancel",
                        timeout=cleanup_timeout(),
                    )
                    values["cancellation"] = "requested"
            except Exception:
                pass
        try:
            write_private(path.parent / "transport-result.json", encode(values))
        except OSError:
            print("Could not retain transport summary", file=sys.stderr)
            code = code or 1
        try:
            outputs(env, values)
        except OSError:
            print("Could not publish GitHub outputs", file=sys.stderr)
            code = code or 1
    return code


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        if len(sys.argv) == 2 and sys.argv[1] == "validate":
            if execution_mode(os.environ) == "dispatch":
                credentials(os.environ)
            return 0
        if len(sys.argv) == 2 and sys.argv[1] == "prepare":
            prepare(os.environ)
            return 0
        if len(sys.argv) == 2 and sys.argv[1] == "submit":
            return submit(os.environ, stop=stop)
        raise TransportError("expected validate, prepare or submit")
    except TransportError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print("Transport failed; check inputs and source access", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
