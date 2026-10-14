#!/usr/bin/env python3
"""Simulate the Spex transport contract, never a remote acceptance environment."""

import json
import os
import signal
import sys
import time
import zipfile
from pathlib import Path


def main():
    stage = sys.argv[2]
    flags = dict(zip(sys.argv[3::2], sys.argv[4::2]))
    root = Path(flags["--workspace"])
    if os.getenv("FIXTURE_CALLS"):
        with open(os.environ["FIXTURE_CALLS"], "a") as stream:
            stream.write(
                json.dumps(
                    {
                        "stage": stage,
                        "token_present": bool(os.getenv("SPEX_REMOTE_TOKEN")),
                        "argv": sys.argv[1:],
                    }
                )
                + "\n"
            )
    if os.getenv("FIXTURE_FAIL_PACKAGE") and stage == "package":
        print("PRIVATE_STDERR_SENTINEL", file=sys.stderr)
        return 2
    if stage == "package":
        canonical = root / flags["--out"]
        base = canonical.parent
        base.mkdir(parents=True)
        canonical.write_text(
            'schema="spex.scenario/v1"\nruntime="migration-testbench/v1"\n'
        )
        with zipfile.ZipFile(base / "scenario-package.zip", "w") as archive:
            archive.write(canonical, "scenario.toml")
        request = {
            "request_id": os.getenv("FIXTURE_REQUEST_ID", "a" * 32),
            "scenario_id": "b" * 64,
            "runtime": "migration-testbench/v1",
            "package_path": str(base / "scenario-package.zip"),
            "request_path": str(base / "request.json"),
            "scenario_path": str(canonical),
        }
        (base / "request.json").write_text(json.dumps(request))
        print(json.dumps(request))
        return 0
    base = (root / flags["--request"]).parent
    summary = {
        "request_id": json.loads((base / "request.json").read_text())["request_id"],
        "scenario_id": "b" * 64,
        "runtime": "migration-testbench/v1",
        "run_id": 42,
        "run_url": "https://github.com/"
        + flags["--runtime-workflow"].split("/.github/workflows/")[0]
        + "/actions/runs/42",
        "artifact_directory": str(base),
        "result_path": str(base / "result.json"),
        "cancellation": "not_requested",
    }

    def finish(code):
        (base / "result.json").write_text(
            json.dumps(
                {
                    "outcome": "cancelled"
                    if code == 130
                    else "failed"
                    if code
                    else "passed"
                }
            )
        )
        print(json.dumps(summary), flush=True)
        return code

    if os.getenv("FIXTURE_WAIT"):

        def cancel(_signum, _frame):
            summary["cancellation"] = "requested"
            raise SystemExit(finish(130))

        signal.signal(
            signal.SIGTERM,
            signal.SIG_IGN if os.getenv("FIXTURE_IGNORE_TERM") else cancel,
        )
        Path(os.environ["FIXTURE_READY"]).touch()
        while True:
            time.sleep(0.05)
    return finish(int(os.getenv("FIXTURE_EXIT", "0")))


if __name__ == "__main__":
    sys.exit(main())
