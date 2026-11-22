"""Authenticated loopback client for the Forge construction backend."""

from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import re


class BridgeError(RuntimeError):
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status} {body.get('error', 'bridge_error')}: {body.get('message', '')}")


class BridgeClient:
    def __init__(self, config: Path, *, timeout=10):
        self.timeout = timeout
        settings = json.loads(config.read_text(encoding="utf-8"))
        self.port = settings["port"]
        self.token = settings["token"]
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("Invalid bridge port.")
        if not isinstance(self.token, str) or not re.fullmatch(r"[0-9a-f]{64}", self.token):
            raise ValueError("Invalid bridge token.")

    def _call(self, endpoint: str, body: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        method = "POST" if data is not None else "GET"
        with closing(HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)) as connection:
            connection.request(method, f"/{endpoint}", body=data, headers=headers)
            with connection.getresponse() as response:
                result = json.load(response)
                if response.status != 200:
                    raise BridgeError(response.status, result)
                return result

    def status(self) -> dict:
        return self._call("status")

    def health(self) -> dict:
        return self._call("health")

    def progress(self) -> dict:
        return self._call("progress")

    def submit(self, plan: dict) -> dict:
        return self._call("build", plan)

    def propose(self, job_id: str, plan: dict) -> dict:
        return self._call("candidate", {"job_id": job_id, "plan": plan})

    def change(self) -> dict:
        return self._call("change")

    def approve(self, change_id: str, _legacy_review=None) -> dict:
        return self._call("approve", {"change_id": change_id})

    def reject(self, change_id: str) -> dict:
        return self._call("reject", {"change_id": change_id})

    def versions(self) -> dict:
        return self._call("versions")

    def control(self, operation: str, job_id: str) -> dict:
        if operation not in ("pause", "resume", "cancel"):
            raise ValueError(f"Unsupported operation: {operation}")
        return self._call(operation, {"job_id": job_id})
