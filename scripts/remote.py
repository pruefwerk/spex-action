#!/usr/bin/env python3
"""GitHub input/output transport. Spex owns packaging and remote execution."""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

from run import ActionError, command, install, json_object, write_outputs


def response_outputs(stage, response, workspace, base, env, code):
    """Validate the client envelope atomically before exposing artifact paths."""
    result = json_object(response)
    values = {}
    for key, field, pattern in (
        ("request-id", "request_id", r"[a-f0-9]{32}"),
        ("scenario-id", "scenario_id", r"[a-f0-9]{64}"),
        (
            "runtime",
            "runtime",
            r"(?:[a-z0-9][a-z0-9._/-]{0,255}(?:@[A-Za-z0-9][A-Za-z0-9._+-]{0,127})?)?",
        ),
    ):
        value = result.get(field)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ActionError("invalid Spex remote identity output")
        values[key] = value

    def file_output(field, filename, optional=False):
        value = result.get(field)
        if optional and value == "":
            return ""
        if not isinstance(value, str) or not value:
            raise ActionError("Spex remote response lacks a path")
        path = workspace / value
        expected = base / filename
        if (
            path.is_symlink()
            or path.resolve() != expected.resolve()
            or not path.is_file()
        ):
            raise ActionError("unexpected Spex output file")
        path.resolve().relative_to(workspace)
        return str(path.resolve())

    if stage == "prepare":
        for key, field, filename in (
            ("package-path", "package_path", "scenario-package.zip"),
            ("request-path", "request_path", "request.json"),
            ("scenario-path", "scenario_path", "scenario.toml"),
        ):
            values[key] = file_output(field, filename)
        # upload-artifact interprets its input as patterns, not literal argv.
        if any(char in values["package-path"] for char in "\r\n*?[]!{}"):
            raise ActionError("package path cannot contain artifact pattern characters")
    else:
        for key, expected in (
            ("request-id", "REQUEST_ID"),
            ("scenario-id", "SCENARIO_ID"),
        ):
            if values[key] != env.get("SPEX_ACTION_" + expected):
                raise ActionError("submission response does not match prepared input")
        directory = result.get("artifact_directory")
        if (
            not isinstance(directory, str)
            or not directory
            or (workspace / directory).resolve() != base.resolve()
        ):
            raise ActionError("unexpected remote artifact directory")
        values["artifact-directory"] = str(base.resolve())
        values["result-path"] = file_output(
            "result_path", "result.json", optional=code != 0
        )
        cancellation = result.get("cancellation")
        if cancellation not in ("not_requested", "requested", "unconfirmed"):
            raise ActionError("invalid remote cancellation output")
        values["cancellation"] = cancellation
        run_id = result.get("run_id")
        if type(run_id) is not int or run_id < 0:
            raise ActionError("invalid remote run identifier")
        expected_url = result.get("run_url")
        match = re.fullmatch(
            rf"https://github.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/actions/runs/{run_id}",
            expected_url if isinstance(expected_url, str) else "",
        )
        if run_id and (
            not match
            or not env["SPEX_ACTION_RUNTIME_WORKFLOW"].startswith(
                match[1] + "/.github/workflows/"
            )
        ):
            raise ActionError("unexpected remote run URL")
        if not run_id:
            expected_url = ""
        if result.get("run_url") != expected_url or (code == 0 and not run_id):
            raise ActionError("unexpected remote run URL")
        values["run-url"] = expected_url
        release = result.get("runtime_release", "")
        resolved_id = result.get("resolved_scenario_id", "")
        if release or resolved_id:
            if (
                not isinstance(release, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", release)
                or release == "latest"
                or not isinstance(resolved_id, str)
                or not re.fullmatch(r"[a-f0-9]{64}", resolved_id)
            ):
                raise ActionError("invalid resolved runtime output")
            values["runtime-release"] = release
            values["resolved-scenario-id"] = resolved_id
    return values


def run(stage, env, invoke=command, installer=install):
    workspace = Path(env.get("SPEX_ACTION_WORKSPACE") or ".").resolve()
    if not workspace.is_dir():
        raise ActionError("working-directory is unavailable")
    version = env.get("SPEX_ACTION_VERSION", "")
    explicit = env.get("SPEX_ACTION_EXECUTABLE", "")
    if bool(version) == bool(explicit):
        raise ActionError("provide exactly one of spex-version and spex-executable")
    if stage not in ("prepare", "submit"):
        raise ActionError("invalid remote adapter stage")
    canonical = ""
    if stage == "prepare":
        canonical = ".spex/action/" + uuid.uuid4().hex + "/scenario.toml"
        base = (workspace / canonical).parent
    else:
        if not env.get("SPEX_REMOTE_TOKEN"):
            raise ActionError("remote authentication is missing")
        for key in (
            "REQUEST",
            "REQUEST_ID",
            "SCENARIO_ID",
            "RUNTIME_WORKFLOW",
            "SOURCE_REPOSITORY",
            "SOURCE_RUN_ID",
            "ARTIFACT_ID",
        ):
            if not env.get("SPEX_ACTION_" + key):
                raise ActionError("remote submission input is missing")
        request_path = (workspace / env["SPEX_ACTION_REQUEST"]).resolve()
        request_path.relative_to(workspace)
        if not request_path.is_file() or request_path.name != "request.json":
            raise ActionError("prepared request is unavailable")
        base = request_path.parent
    outputs = {}
    code = 0
    temporary = tempfile.TemporaryDirectory(prefix="spex-remote-authoring-")
    try:
        if explicit:
            executable = Path(explicit)
            if not executable.is_absolute():
                executable = workspace / executable
            if not executable.is_file() or not os.access(executable, os.X_OK):
                raise ActionError("spex-executable is unavailable")
        else:
            executable = installer(version, Path(temporary.name))
        if stage == "submit" and env.get("SPEX_ACTION_CLIENT_SHA"):
            with executable.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != env["SPEX_ACTION_CLIENT_SHA"]:
                raise ActionError("Spex executable changed after package preparation")
        if stage == "prepare":
            argv = [
                str(executable),
                "scenario",
                "package",
                "--workspace",
                str(workspace),
                "--out",
                canonical,
            ]
            for key, flag in (
                ("RUNTIME", "--runtime"),
                ("NAME", "--name"),
                ("DESCRIPTION", "--description"),
                ("TIMEOUT", "--timeout"),
            ):
                if env.get("SPEX_ACTION_" + key):
                    argv.extend([flag, env["SPEX_ACTION_" + key]])
            for key, flag in (
                ("DEFINITION", "--definition-inline-file"),
                ("CONFIG", "--runtime-config-file"),
            ):
                if env.get("SPEX_ACTION_" + key):
                    path = Path(temporary.name) / key.lower()
                    path.write_text(env["SPEX_ACTION_" + key], encoding="utf-8")
                    path.chmod(0o600)
                    argv.extend([flag, str(path)])
            for path in env.get("SPEX_ACTION_DEFINITION_FILES", "").splitlines():
                if path.strip():
                    argv.extend(["--definition-file", path.strip()])
        else:
            argv = [
                str(executable),
                "scenario",
                "submit",
                "--workspace",
                str(workspace),
            ]
            for key, flag in (
                ("REQUEST", "--request"),
                ("RUNTIME_WORKFLOW", "--runtime-workflow"),
                ("SOURCE_REPOSITORY", "--source-repository"),
                ("SOURCE_RUN_ID", "--source-run-id"),
                ("ARTIFACT_ID", "--artifact-id"),
            ):
                value = env.get("SPEX_ACTION_" + key, "")
                if not value:
                    raise ActionError("remote submission input is missing")
                if key == "REQUEST":
                    value = str((workspace / value).resolve().relative_to(workspace))
                argv.extend([flag, value])
            argv.extend(["--wait-timeout", env.get("SPEX_ACTION_WAIT_TIMEOUT") or "6h"])
        code, response = invoke(argv, workspace)
        if response.strip():
            try:
                validated = response_outputs(
                    stage, response, workspace, base, env, code
                )
                if stage == "prepare" and code == 0:
                    # Keep only the verified executable across composite steps.
                    # It contains no inputs or credentials and is never uploaded.
                    client = executable.resolve()
                    if not explicit:
                        cache = Path(
                            tempfile.mkdtemp(
                                prefix="spex-action-client-", dir=env.get("RUNNER_TEMP")
                            )
                        )
                        try:
                            shutil.copyfile(client, cache / "spex")
                            client = cache / "spex"
                            client.chmod(0o700)
                        except OSError:
                            shutil.rmtree(cache)
                            raise
                    with client.open("rb") as stream:
                        validated["client-sha256"] = hashlib.file_digest(
                            stream, "sha256"
                        ).hexdigest()
                    validated["client-path"] = str(client)
                outputs = validated
                print("Spex remote stage: " + stage)
                print("Request: " + json.dumps(outputs["request-id"]))
                if outputs.get("run-url"):
                    print("Remote run: " + json.dumps(outputs["run-url"]))
            except ActionError:
                if code == 0:
                    raise
                print(
                    "Remote response unavailable; preserving execution failure",
                    file=sys.stderr,
                )
            except (ValueError, OSError):
                if code == 0:
                    raise ActionError("invalid Spex remote response") from None
                print(
                    "Remote response unavailable; preserving execution failure",
                    file=sys.stderr,
                )
        elif code == 0:
            raise ActionError("Spex returned no remote response")
        if code:
            print(
                f"Spex remote {stage} failed (exit {code}); inspect retained evidence",
                file=sys.stderr,
            )
    finally:
        try:
            write_outputs(env.get("GITHUB_OUTPUT"), outputs)
        except OSError:
            print("Could not expose remote outputs", file=sys.stderr)
            code = code or 1
        try:
            temporary.cleanup()
        except OSError:
            print("Remote authoring cleanup failed", file=sys.stderr)
            code = code or 1
    return code


def main():
    try:
        return run(sys.argv[1] if len(sys.argv) == 2 else "", os.environ)
    except ActionError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print(
            "Remote Action failed; check inputs, client version and access",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
