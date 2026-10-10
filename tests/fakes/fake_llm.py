"""A scripted stand-in for the local model. CI has no model server."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from worker.errors import ModelOutputInvalid
from worker.local_llm import LLMResult

T = TypeVar("T", bound=BaseModel)

Responder = Callable[[list[dict[str, str]]], str]


def echo_nonce(messages: list[dict[str, str]]) -> str:
    """Answers the healthcheck prompt correctly."""
    import json

    return json.dumps({"ok": True, "echo": messages[-1]["content"]})


class FakeLLM:
    model_name = "fake-model"

    def __init__(self, responder: Responder = echo_nonce) -> None:
        self.responder = responder
        self.calls: list[list[dict[str, str]]] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        prompt_version: str,
        temperature: float = 0.0,
    ) -> LLMResult[T]:
        for attempt in (1, 2):
            self.calls.append(list(messages))
            try:
                value = schema.model_validate_json(self.responder(messages))
            except ValidationError:
                continue
            return LLMResult(
                value=value,
                model=self.model_name,
                prompt_version=prompt_version,
                tokens_in=10,
                tokens_out=5,
                latency_ms=1,
                attempts=attempt,
            )
        raise ModelOutputInvalid()
