"""Client for the job API. The worker opens every connection; nothing connects to the worker."""
import json
import urllib.error
import urllib.request


class JobApiError(Exception):
    pass


class JobApi:
    def __init__(self, base_url: str, token: str, worker_id: str, timeout: float = 30):
        self.base, self.token, self.worker_id, self.timeout = base_url, token, worker_id, timeout

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.base}/internal/jobs/{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raise JobApiError(f"job API answered {exc.code} for {path}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise JobApiError(f"job API not reachable: {type(exc).__name__}") from exc

    def claim(self) -> dict | None:
        status, body = self._post("claim", {"worker_id": self.worker_id})
        return body if status == 200 and body else None

    def result(self, job_id: int, result: dict) -> dict:
        return self._post(f"{job_id}/result", result)[1]

    def fail(self, job_id: int, reason: str) -> None:
        self._post(f"{job_id}/fail", {"reason": reason})

    def heartbeat(self, job_id: int) -> None:
        self._post(f"{job_id}/heartbeat", {})
