#!/usr/bin/env python3
"""Transport Action inputs to Spex. Spex owns parsing, merging and execution."""

import contextlib
import hashlib
import json
import os
import platform
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ActionError(Exception):
    pass


def download(url, destination, limit):
    # URLs originate only from the fixed release host and a validated tag.
    with (
        urllib.request.urlopen(url, timeout=30) as response,
        destination.open("xb") as out,
    ):
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ActionError("Spex release asset exceeds the size limit")
            out.write(chunk)


def install(version, directory):
    if not re.fullmatch(
        r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", version
    ):
        raise ActionError("spex-version must be an explicit version tag")
    systems = {"Linux": "linux", "Darwin": "darwin"}
    architectures = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    system, architecture = (
        systems.get(platform.system()),
        architectures.get(platform.machine().lower()),
    )
    if not system or not architecture:
        raise ActionError("unsupported runner platform")
    archive_name = f"spex_{version[1:]}_{system}_{architecture}.tar.gz"
    url = (
        "https://github.com/pruefwerk/spex/releases/download/"
        + urllib.parse.quote(version, safe="")
        + "/"
        + urllib.parse.quote(archive_name, safe="")
    )
    archive, checksum = directory / archive_name, directory / "checksum"
    download(url, archive, 512 * 1024 * 1024)
    download(url + ".sha256", checksum, 65536)
    fields = checksum.read_text().strip().split()
    if (
        len(fields) != 2
        or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0])
        or fields[1].lstrip("*") != archive_name
    ):
        raise ActionError("invalid Spex archive checksum file")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != fields[0].lower():
        raise ActionError("Spex archive checksum mismatch")
    executable = directory / "spex"
    # Extract exactly one regular file, never archive paths, links or devices.
    with tarfile.open(archive, "r:gz") as bundle:
        members = [entry for entry in bundle if entry.name in ("spex", "./spex")]
        if (
            len(members) != 1
            or not members[0].isfile()
            or members[0].size > 128 * 1024 * 1024
        ):
            raise ActionError("Spex archive has no unique regular executable")
        with bundle.extractfile(members[0]) as stream, executable.open("xb") as out:
            while chunk := stream.read(1024 * 1024):
                out.write(chunk)
    executable.chmod(0o700)
    return executable


def command(argv, workspace):
    """Do not echo argv, input files, or raw subprocess output."""
    process = None
    timer = None
    pending_signal = None

    def cancel(signum, _frame):
        nonlocal timer, pending_signal
        pending_signal = signum
        if process is None or process.poll() is not None:
            return
        try:
            process.send_signal(signum)
        except ProcessLookupError:
            return
        if timer is None:

            def force_stop():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            timer = threading.Timer(40, force_stop)
            timer.daemon = True
            timer.start()

    previous = {
        sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if pending_signal is not None:
            cancel(pending_signal, None)
        stdout, _stderr = process.communicate()
        code = process.returncode
        if code == 0 and pending_signal is not None:
            code = 128 + pending_signal
        return (128 - code if code < 0 else code), stdout
    finally:
        if timer is not None:
            timer.cancel()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def json_object(text):
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        raise ActionError("Spex did not return a valid JSON response") from None
    if not isinstance(value, dict):
        raise ActionError("Spex returned an invalid response shape")
    return value


def write_outputs(path, values):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            delimiter = uuid.uuid4().hex
            while delimiter in value:
                delimiter = uuid.uuid4().hex
            output.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")


def run(env, invoke=command, installer=install):
    secondary_failed = False

    def emit(values):
        nonlocal secondary_failed
        try:
            write_outputs(env.get("GITHUB_OUTPUT"), values)
        except OSError:
            secondary_failed = True
            print(
                "Could not expose Action outputs; preserving any execution failure",
                file=sys.stderr,
            )

    def cleanup_problem():
        nonlocal secondary_failed
        secondary_failed = True
        print(
            "Temporary authoring cleanup failed; preserving any execution failure",
            file=sys.stderr,
        )

    code = execute(env, invoke, installer, emit, cleanup_problem)
    return code or (1 if secondary_failed else 0)


@contextlib.contextmanager
def temporary_authoring(cleanup_problem):
    temporary = tempfile.TemporaryDirectory(prefix="spex-action-")
    try:
        yield Path(temporary.name)
    finally:
        try:
            temporary.cleanup()
        except OSError:
            cleanup_problem()


def execute(env, invoke, installer, emit, cleanup_problem):
    def input_value(name, default=""):
        return env.get("SPEX_ACTION_" + name, default)

    workspace = Path(input_value("WORKSPACE") or ".").resolve()
    if not workspace.is_dir():
        raise ActionError("working-directory is not a directory")
    version, explicit = input_value("VERSION"), input_value("EXECUTABLE")
    if bool(version) == bool(explicit):
        raise ActionError("provide exactly one of spex-version and spex-executable")
    outputs = {}
    try:
        with temporary_authoring(cleanup_problem) as temporary:
            if explicit:
                executable = Path(explicit)
                if not executable.is_absolute():
                    executable = workspace / executable
                executable = executable.resolve()
                if not executable.is_file() or not os.access(executable, os.X_OK):
                    raise ActionError(
                        "spex-executable is unavailable or not executable"
                    )
            else:
                executable = installer(version, temporary)
            canonical = ".spex/action/" + uuid.uuid4().hex + "/scenario.toml"
            build = [
                str(executable),
                "scenario",
                "build",
                "--workspace",
                str(workspace),
                "--out",
                canonical,
            ]
            for name, flag in (
                ("SCENARIO", "--scenario"),
                ("RUNTIME", "--runtime"),
                ("NAME", "--name"),
                ("DESCRIPTION", "--description"),
                ("TIMEOUT", "--timeout"),
            ):
                if input_value(name):
                    build.extend([flag, input_value(name)])
            for name, flag in (
                ("INLINE", "--inline-file"),
                ("CONFIG", "--runtime-config-file"),
            ):
                if input_value(name):
                    path = temporary / name.lower()
                    path.write_text(input_value(name), encoding="utf-8")
                    path.chmod(0o600)
                    build.extend([flag, str(path)])
            for path in input_value("FILES").splitlines():
                if path.strip():
                    build.extend(["--spex-file", path.strip()])
            code, _ = invoke(build, workspace)
            if code:
                print(
                    f"Spex scenario build failed (exit {code}); no execution started",
                    file=sys.stderr,
                )
                return code
            canonical_path = str(workspace / canonical)
            outputs["scenario-path"] = canonical_path
            code, response = invoke(
                [
                    str(executable),
                    "scenario",
                    "explain",
                    "--workspace",
                    str(workspace),
                    canonical_path,
                ],
                workspace,
            )
            if code:
                print(
                    f"Spex scenario explain failed (exit {code}); no execution started",
                    file=sys.stderr,
                )
                return code
            explanation = json_object(response)
            for key, field in (("scenario-id", "scenario_id"), ("runtime", "runtime")):
                if not isinstance(explanation.get(field), str):
                    raise ActionError("Spex explanation is missing identity fields")
                outputs[key] = explanation[field]
            print("Spex scenario: " + json.dumps(explanation.get("name", "")))
            print("Scenario ID: " + json.dumps(outputs["scenario-id"]))
            print("Runtime: " + json.dumps(outputs["runtime"]))
            source = (
                "scenario-file"
                if input_value("SCENARIO")
                else "inline"
                if input_value("INLINE")
                else "files"
                if input_value("FILES")
                else "runtime-discovered"
            )
            print("Source: " + source)
            tests = (
                explanation.get("plan", {}).get("tests")
                if isinstance(explanation.get("plan"), dict)
                else None
            )
            print(
                "Tests: "
                + (str(len(tests)) if isinstance(tests, list) else "runtime-discovered")
            )
            print("Configuration: inherited + scenario overlay")
            code, response = invoke(
                [
                    str(executable),
                    "scenario",
                    "run",
                    "--workspace",
                    str(workspace),
                    "--artifact-directory",
                    input_value("ARTIFACTS") or ".spex/runs",
                    canonical_path,
                ],
                workspace,
            )
            if response.strip():
                try:
                    summary = json_object(response)
                    updated = {}
                    for key, field in (
                        ("scenario-id", "scenario_id"),
                        ("scenario-path", "scenario_path"),
                        ("result-path", "result_path"),
                        ("artifact-directory", "artifact_directory"),
                        ("runtime", "runtime"),
                    ):
                        value = summary.get(field)
                        if not isinstance(value, str):
                            raise ActionError(
                                "Spex run summary is missing output fields"
                            )
                        updated[key] = value
                    outputs.update(updated)
                    print("Artifacts: " + json.dumps(outputs["artifact-directory"]))
                    print("Outcome: " + json.dumps(summary.get("outcome", "unknown")))
                except ActionError:
                    if code == 0:
                        raise
                    print(
                        "Spex run summary unavailable; preserving the execution exit code",
                        file=sys.stderr,
                    )
            elif code == 0:
                raise ActionError("Spex returned success without a run summary")
            if code:
                print(
                    f"Spex scenario run failed (exit {code}); inspect retained result and evidence",
                    file=sys.stderr,
                )
            return code
    finally:
        emit(outputs)


def main():
    if sys.version_info < (3, 11):
        print("Spex Action requires Python 3.11 or newer", file=sys.stderr)
        return 1
    try:
        return run(os.environ)
    except ActionError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        # OS/network exceptions can include URLs, paths or input-derived text.
        print(
            "Spex Action could not complete; check executable, release assets and filesystem access",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
