"""In-memory GitHub service for transport tests; never dispatches a workflow."""

import io
import json
import zipfile


class API:
    def __init__(self, request, digest, mode="passed"):
        self.request, self.digest, self.mode = request, digest, mode
        self.calls = []
        self.receipt_hook = lambda value: value
        self.workflow_sha = "c" * 40

    def call(self, method, path, body=None, timeout=30, binary=False):
        self.calls.append((method, path, body, timeout))
        if "/commits/" in path:
            return {"sha": self.workflow_sha}
        if path.endswith("/artifacts/123"):
            return {
                "name": "spex-request-" + self.request["request_id"],
                "expired": self.mode == "expired",
                "workflow_run": {
                    "id": self.request["source"]["run_id"],
                    "head_sha": self.request["source"]["commit"],
                },
            }
        if path.endswith("/dispatches"):
            if self.mode == "lost-ack":
                raise OSError("PRIVATE_SERVER_SENTINEL")
            return {}
        if "workflow_dispatch" in path:
            run = {
                "id": 42,
                "display_title": "spex:" + self.request["request_id"],
                "run_attempt": 2 if self.mode == "rerun" else 1,
                "head_sha": "d" * 40 if self.mode == "wrong-sha" else self.workflow_sha,
                "status": "in_progress" if self.mode == "waiting" else "completed",
                "conclusion": "success"
                if self.mode in ("passed", "incomplete")
                else "failure",
            }
            return {"workflow_runs": [run, run] if self.mode == "duplicate" else [run]}
        if path.endswith("/cancel"):
            return {}
        if "/runs/42/artifacts" in path:
            return {
                "artifacts": []
                if self.mode == "missing"
                else [
                    {
                        "id": 456,
                        "name": "spex-result-" + self.request["request_id"],
                        "expired": False,
                    }
                ]
            }
        if path.endswith("/artifacts/456/zip"):
            outcome = (
                self.mode if self.mode in ("failed", "error", "cancelled") else "passed"
            )
            receipt = {
                "schema": "spex.transport-result/v1",
                "request_id": self.request["request_id"],
                "request_sha256": self.digest,
                "source_commit": self.request["source"]["commit"],
                "workflow_sha": self.workflow_sha,
                "run_id": 42,
                "run_attempt": 1,
                "planned_tests": 2 if self.mode == "incomplete" else 1,
                "result": {
                    "schema": "spex.result/v1",
                    "scenario_id": "e" * 64,
                    "runtime": "example/v1",
                    "runtime_release": "v2.0.0",
                    "spex_version": "v0.2.0",
                    "outcome": outcome,
                    "cleanup": "succeeded",
                    "tests": [{"outcome": outcome, "name": "PRIVATE_SERVER_SENTINEL"}],
                    "diagnostics": "PRIVATE_SERVER_SENTINEL",
                },
            }
            content = json.dumps(self.receipt_hook(receipt)).encode()
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("receipt.json", content)
            return stream.getvalue()
        raise AssertionError("unhandled fixture API path: " + path)
