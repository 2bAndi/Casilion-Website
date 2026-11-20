"""A minimal client for OpenAI-compatible chat endpoints (Ollama, vLLM, a hosted API).

Standard library only, so the worker image stays small. The model is
configuration: LLM_BASE_URL and LLM_MODEL.
"""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(Exception):
    pass


@dataclass
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0

    def add(self, other: "Usage") -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out


class OpenAICompatible:
    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 300):
        if not model:
            raise LLMError("LLM_MODEL is not set.")
        self.url, self.model, self.api_key, self.timeout = base_url + "/chat/completions", model, api_key, timeout

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise LLMError(f"model endpoint answered {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise LLMError(f"model endpoint not usable: {type(exc).__name__}") from exc

    def _chat(self, system: str, user: str, response_format: dict | None) -> tuple[str, Usage]:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if response_format:
            payload["response_format"] = response_format
        data = self._post(payload)
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("unexpected answer from the model endpoint") from exc
        usage = data.get("usage") or {}
        return content, Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))

    def text(self, system: str, user: str) -> tuple[str, Usage]:
        return self._chat(system, user, None)

    def json(self, system: str, user: str, schema: dict, name: str) -> tuple[dict, Usage]:
        """Ask for an object that matches the schema. Falls back to plain JSON mode if the endpoint lacks schemas."""
        try:
            content, usage = self._chat(
                system, user, {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}
            )
        except LLMError:
            content, usage = self._chat(system, user, {"type": "json_object"})
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`").removeprefix("json").strip()
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            raise LLMError("the model did not return JSON") from exc
        if not isinstance(parsed, dict):
            raise LLMError("the model did not return an object")
        return parsed, usage
