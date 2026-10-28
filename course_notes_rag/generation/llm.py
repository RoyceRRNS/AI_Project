"""LLM clients: Ollama (default, local Qwen), any OpenAI-compatible server, and a fake."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import httpx

Message = dict[str, str]


class LLMError(RuntimeError):
    """The model call failed or returned something unusable."""


class LLMUnavailableError(LLMError):
    """The model server cannot be reached or the model is missing."""


class LLMClient(Protocol):
    model: str

    def chat(
        self, messages: list[Message], *, temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> str: ...

    def stream_chat(
        self, messages: list[Message], *, temperature: float | None = None
    ) -> Iterator[str]: ...

    def health(self) -> dict[str, Any]: ...


class OllamaClient:
    """Talks to Ollama's native ``/api/chat`` (supports JSON-schema structured outputs)."""

    def __init__(
        self, base_url: str, model: str, *, temperature: float = 0.2, num_ctx: int = 8192,
        timeout: float = 300.0, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
        )

    def _payload(self, messages: list[Message], temperature: float | None, stream: bool) -> dict:
        if not messages:
            raise ValueError("messages must not be empty")
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": "30m",
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_ctx": self.num_ctx,
            },
        }

    def _raise_for(self, response: httpx.Response) -> None:
        if response.status_code == 404:
            raise LLMUnavailableError(
                f"Model '{self.model}' is not available in Ollama. Run: ollama pull {self.model}"
            )
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:
                detail = response.text
            raise LLMError(f"Ollama error {response.status_code}: {detail}")

    def chat(
        self, messages: list[Message], *, temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        payload = self._payload(messages, temperature, stream=False)
        if json_schema is not None:
            payload["format"] = json_schema
        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is Ollama running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("The model took too long to respond") from exc
        self._raise_for(response)
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError("Malformed response from Ollama") from exc
        return content

    def stream_chat(
        self, messages: list[Message], *, temperature: float | None = None
    ) -> Iterator[str]:
        payload = self._payload(messages, temperature, stream=True)
        try:
            with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for(response)
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError as exc:
                        raise LLMError("Malformed stream chunk from Ollama") from exc
                    if chunk.get("error"):
                        raise LLMError(str(chunk["error"]))
                    delta = (chunk.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        return
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url}. Is Ollama running?"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("The model took too long to respond") from exc

    def health(self) -> dict[str, Any]:
        try:
            response = self._client.get("/api/tags", timeout=3.0)
            response.raise_for_status()
            names = {m.get("name", "") for m in response.json().get("models", [])}
        except (httpx.HTTPError, ValueError) as exc:
            return {"ok": False, "provider": "ollama", "model": self.model,
                    "detail": f"Ollama not reachable at {self.base_url}: {exc.__class__.__name__}"}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in names:
            return {"ok": False, "provider": "ollama", "model": self.model,
                    "detail": f"Model not pulled. Run: ollama pull {self.model}"}
        return {"ok": True, "provider": "ollama", "model": self.model, "detail": "ready"}


class OpenAICompatClient:
    """Any ``/v1/chat/completions`` server: vLLM, DashScope (Qwen cloud), OpenRouter, OpenAI."""

    def __init__(
        self, base_url: str, model: str, api_key: str | None = None, *, temperature: float = 0.2,
        timeout: float = 300.0, transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers,
            timeout=httpx.Timeout(timeout, connect=5.0), transport=transport,
        )

    def _post_json(self, payload: dict) -> httpx.Response:
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"Cannot reach LLM server at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError("The model took too long to respond") from exc
        if response.status_code >= 400:
            raise LLMError(f"LLM server error {response.status_code}: {response.text[:300]}")
        return response

    def chat(
        self, messages: list[Message], *, temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        if not messages:
            raise ValueError("messages must not be empty")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": json_schema.get("title", "output"), "schema": json_schema},
            }
        response = self._post_json(payload)
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Malformed response from LLM server") from exc

    def stream_chat(
        self, messages: list[Message], *, temperature: float | None = None
    ) -> Iterator[str]:
        if not messages:
            raise ValueError("messages must not be empty")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "stream": True,
        }
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    raise LLMError(f"LLM server error {response.status_code}: {response.text[:300]}")
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                    except (ValueError, KeyError, IndexError) as exc:
                        raise LLMError("Malformed stream chunk from LLM server") from exc
                    if delta:
                        yield delta
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"Cannot reach LLM server at {self.base_url}") from exc

    def health(self) -> dict[str, Any]:
        try:
            self._client.get("/models", timeout=3.0).raise_for_status()
        except httpx.HTTPError as exc:
            return {"ok": False, "provider": "openai_compat", "model": self.model,
                    "detail": f"LLM server not reachable: {exc.__class__.__name__}"}
        return {"ok": True, "provider": "openai_compat", "model": self.model, "detail": "ready"}


Handler = Callable[[list[Message], dict[str, Any] | None], str]


class FakeLLM:
    """Scripted LLM for tests: returns queued responses or calls a handler."""

    model = "fake"

    def __init__(self, responses: list[str] | None = None, handler: Handler | None = None) -> None:
        self.responses = list(responses or [])
        self.handler = handler
        self.calls: list[dict[str, Any]] = []
        self.healthy = True

    def _next(self, messages: list[Message], schema: dict[str, Any] | None) -> str:
        self.calls.append({"messages": messages, "json_schema": schema})
        if self.handler is not None:
            return self.handler(messages, schema)
        if not self.responses:
            raise LLMError("FakeLLM has no scripted responses left")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def chat(self, messages, *, temperature=None, json_schema=None) -> str:
        return self._next(messages, json_schema)

    def stream_chat(self, messages, *, temperature=None) -> Iterator[str]:
        text = self._next(messages, None)
        for i in range(0, len(text), 12):
            yield text[i : i + 12]

    def health(self) -> dict[str, Any]:
        return {"ok": self.healthy, "provider": "fake", "model": self.model,
                "detail": "ready" if self.healthy else "down"}
