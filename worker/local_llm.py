"""Client for the local model server (llama.cpp llama-server, OpenAI-compatible API).

The only model client prospect data may reach. It refuses any base URL that is not
this machine (checked in config and again here).

Request shape confirmed against llama.cpp tools/server (2026-10-10, see
docs/integrations.md): POST {base}/chat/completions with the OpenAI nested
`response_format = {"type": "json_schema", "json_schema": {"schema": ...}}`, and
`chat_template_kwargs = {"enable_thinking": false}` to keep Qwen's thinking off.

Logs: model, latency, token counts. Never prompt or output text.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ValidationError

from worker.config import LOCAL_HOSTS
from worker.errors import LocalModelUnavailable, ModelOutputInvalid
from worker.logs import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    attempts: int


class StructuredLLM(Protocol):
    """What a job handler depends on. Tests pass a fake that implements this."""

    model_name: str

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        prompt_version: str,
        temperature: float = 0.0,
    ) -> LLMResult[T]: ...


def _validation_summary(exc: ValidationError) -> str:
    """Field locations and error types only; never the offending input."""
    parts = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['type']}")
    return "; ".join(parts)[:400]


class LocalLLM:
    def __init__(
        self,
        base_url: str,
        model_name: str,
        *,
        thinking: bool = False,
        timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        host = urlparse(base_url).hostname or ""
        if host not in LOCAL_HOSTS:
            raise ValueError("LocalLLM only talks to a model server on this machine")
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self.thinking = thinking
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _post(self, body: dict) -> dict:
        try:
            resp = self._http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TransportError as exc:
            raise LocalModelUnavailable() from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            log.warning("local model server error", extra={"http_status": resp.status_code})
            raise LocalModelUnavailable()
        if resp.status_code >= 400:
            log.error("local model rejected request", extra={"http_status": resp.status_code})
            raise ModelOutputInvalid()
        return resp.json()

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        prompt_version: str,
        temperature: float = 0.0,
    ) -> LLMResult[T]:
        body: dict = {
            "model": self.model_name,
            "messages": list(messages),
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            },
            "chat_template_kwargs": {"enable_thinking": self.thinking},
        }
        tokens_in = tokens_out = 0
        started = time.monotonic()
        last_summary = ""
        for attempt in (1, 2):
            data = self._post(body)
            usage = data.get("usage") or {}
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            try:
                content = data["choices"][0]["message"]["content"]
                value = schema.model_validate_json(content)
            except ValidationError as exc:
                last_summary = _validation_summary(exc)
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                last_summary = "response did not contain a message"
            else:
                latency_ms = int((time.monotonic() - started) * 1000)
                log.info(
                    "local model call ok",
                    extra={
                        "model": self.model_name,
                        "prompt_version": prompt_version,
                        "latency_ms": latency_ms,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "attempt": attempt,
                    },
                )
                return LLMResult(
                    value=value,
                    model=self.model_name,
                    prompt_version=prompt_version,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    attempts=attempt,
                )
            log.warning(
                "local model output failed validation",
                extra={"model": self.model_name, "prompt_version": prompt_version, "attempt": attempt},
            )
            if attempt == 1:
                # One retry, telling the model what was wrong (structure only, no data).
                body["messages"] = [
                    *messages,
                    {
                        "role": "user",
                        "content": "Your previous reply did not match the required JSON schema "
                        f"({last_summary}). Reply again with only valid JSON.",
                    },
                ]
        raise ModelOutputInvalid()
