from __future__ import annotations

import io
import json
import logging

import httpx
import pytest
from pydantic import BaseModel

from worker import logs
from worker.errors import LocalModelUnavailable, ModelOutputInvalid
from worker.local_llm import LocalLLM


class Out(BaseModel):
    ok: bool
    echo: str


def _chat(content: str, prompt_tokens: int = 7, completion_tokens: int = 3) -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def _client(handler) -> LocalLLM:
    return LocalLLM(
        "http://127.0.0.1:8080/v1", "test-model", transport=httpx.MockTransport(handler)
    )


def test_valid_output_and_request_shape():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_chat('{"ok": true, "echo": "abc123"}'))

    result = _client(handler).complete(
        [{"role": "user", "content": "abc123"}], Out, prompt_version="healthcheck/echo.v1"
    )
    assert result.value == Out(ok=True, echo="abc123")
    assert (result.tokens_in, result.tokens_out, result.attempts) == (7, 3, 1)
    body = seen[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"]["title"] == "Out"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.0


def test_retries_once_with_structural_hint_then_succeeds():
    replies = iter(['{"ok": "not-a-bool-or-anything"}', '{"ok": true, "echo": "x1y2z3"}'])
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_chat(next(replies)))

    result = _client(handler).complete(
        [{"role": "user", "content": "x1y2z3"}], Out, prompt_version="p.v1"
    )
    assert result.attempts == 2
    assert result.tokens_in == 14
    hint = bodies[1]["messages"][-1]["content"]
    assert "echo" in hint
    assert "not-a-bool-or-anything" not in hint  # the hint never quotes model output


def test_two_invalid_outputs_raise_model_output_invalid():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat("not json at all"))

    with pytest.raises(ModelOutputInvalid):
        _client(handler).complete([{"role": "user", "content": "q"}], Out, prompt_version="p.v1")


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_errors_are_retryable(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    with pytest.raises(LocalModelUnavailable):
        _client(handler).complete([{"role": "user", "content": "q"}], Out, prompt_version="p.v1")


def test_connection_refused_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LocalModelUnavailable):
        _client(handler).complete([{"role": "user", "content": "q"}], Out, prompt_version="p.v1")


@pytest.mark.parametrize(
    "url", ["https://api.openai.com/v1", "http://10.0.0.5:8080/v1", "http://my-gpu-box:8080/v1"]
)
def test_refuses_non_local_base_url(url: str):
    with pytest.raises(ValueError):
        LocalLLM(url, "m")


def test_logs_never_contain_prompt_or_output(sentinels):
    secret_in = sentinels["transcript_sentences"][0]
    secret_out = sentinels["names"][0]
    replies = iter(
        [json.dumps({"ok": True}) + secret_out, json.dumps({"ok": True, "echo": secret_out})]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat(next(replies)))

    stream = io.StringIO()
    logs.configure(level=logging.DEBUG, stream=stream)
    _client(handler).complete([{"role": "user", "content": secret_in}], Out, prompt_version="p.v1")
    output = stream.getvalue()
    assert "local model call ok" in output
    assert secret_in not in output
    assert secret_out not in output
