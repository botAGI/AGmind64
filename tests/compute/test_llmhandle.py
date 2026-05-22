"""Tests для LLMHandle ABC + LlamaServerHandle wrapper.

Покрывает:
- LLMHandle.chat fallback к generate (_messages_to_prompt)
- LLMHandle.generate_stream fallback к одиночному generate
- LlamaServerHandle generate/chat/streams через mock client
- close() поведение
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest

from agmind.compute.backends._engines.llama_server_handle import LlamaServerHandle
from agmind.compute.base import LLMHandle
from agmind.compute.clients import LlamaServerClient

pytestmark = pytest.mark.backend_any


class _FakeHandle(LLMHandle):
    """Minimal LLMHandle subclass для тестов default методов."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> str:
        self.calls.append(("generate", prompt))
        return f"reply to: {prompt[:30]}"

    def close(self) -> None:
        self.calls.append(("close", ""))


def test_llmhandle_chat_fallback_to_generate() -> None:
    h = _FakeHandle()
    messages = [
        {"role": "system", "content": "Be brief"},
        {"role": "user", "content": "Hi"},
    ]
    result = h.chat(messages)
    assert "reply to" in result
    # Verify generate was called с naive chat template
    assert h.calls[0][0] == "generate"
    prompt = h.calls[0][1]
    assert "System: Be brief" in prompt
    assert "User: Hi" in prompt
    assert prompt.endswith("Assistant:")


def test_llmhandle_generate_stream_fallback() -> None:
    h = _FakeHandle()
    chunks = list(h.generate_stream("test prompt"))
    # Default = single chunk
    assert len(chunks) == 1
    assert "reply to" in chunks[0]


def test_llmhandle_chat_stream_uses_generate_stream() -> None:
    h = _FakeHandle()
    messages = [{"role": "user", "content": "Hi"}]
    chunks = list(h.chat_stream(messages))
    assert len(chunks) == 1


def test_llmhandle_messages_to_prompt_format() -> None:
    msgs = [
        {"role": "system", "content": "sys-msg"},
        {"role": "user", "content": "user-msg"},
        {"role": "assistant", "content": "asst-msg"},
        {"role": "user", "content": "follow-up"},
    ]
    prompt = LLMHandle._messages_to_prompt(msgs)
    assert "System: sys-msg" in prompt
    assert "User: user-msg" in prompt
    assert "Assistant: asst-msg" in prompt
    assert "User: follow-up" in prompt
    assert prompt.endswith("Assistant:")


def test_llmhandle_context_manager() -> None:
    h = _FakeHandle()
    with h as ctx:
        assert ctx is h
        ctx.generate("inside")
    assert ("close", "") in h.calls


# ---- LlamaServerHandle ----


def _make_mocked_handle() -> tuple[LlamaServerHandle, MagicMock]:
    client = MagicMock(spec=LlamaServerClient)
    handle = LlamaServerHandle(client, model="test-model")
    return handle, client


def test_llama_server_handle_is_llmhandle() -> None:
    handle, _ = _make_mocked_handle()
    assert isinstance(handle, LLMHandle)


def test_llama_server_handle_generate_delegates_to_client() -> None:
    handle, client = _make_mocked_handle()
    client.complete.return_value = "generated text"
    result = handle.generate("prompt", max_tokens=100)
    assert result == "generated text"
    client.complete.assert_called_once()
    _, kwargs = client.complete.call_args
    assert kwargs["max_tokens"] == 100


def test_llama_server_handle_chat_uses_server_side_template() -> None:
    """Override от LLMHandle base — chat() вызывает client.chat (server template)."""
    handle, client = _make_mocked_handle()
    client.chat.return_value = "assistant reply"
    messages = [{"role": "user", "content": "Hi"}]
    result = handle.chat(messages)
    assert result == "assistant reply"
    client.chat.assert_called_once()
    # client.chat — не fallback к generate (что было бы naive template)
    client.complete.assert_not_called()


def test_llama_server_handle_chat_passes_tools() -> None:
    handle, client = _make_mocked_handle()
    client.chat.return_value = ""
    tools = [{"type": "function", "function": {"name": "f"}}]
    handle.chat([{"role": "user", "content": "x"}], tools=tools)
    _, kwargs = client.chat.call_args
    assert kwargs["tools"] == tools


def test_llama_server_handle_generate_stream() -> None:
    handle, client = _make_mocked_handle()
    client.complete_stream.return_value = iter(["a", "b", "c"])
    chunks = list(handle.generate_stream("prompt"))
    assert chunks == ["a", "b", "c"]


def test_llama_server_handle_chat_stream() -> None:
    handle, client = _make_mocked_handle()
    client.chat_stream.return_value = iter(["hi", " there"])
    chunks = list(handle.chat_stream([{"role": "user", "content": "x"}]))
    assert chunks == ["hi", " there"]


def test_llama_server_handle_sampling_params_from_kwargs() -> None:
    """top_p/seed/etc kwargs → SamplingParams для client."""
    handle, client = _make_mocked_handle()
    client.complete.return_value = "x"
    handle.generate("p", temperature=0.3, top_p=0.5, seed=42)
    _, kwargs = client.complete.call_args
    sp = kwargs["sampling"]
    assert sp.temperature == 0.3
    assert sp.top_p == 0.5
    assert sp.seed == 42


def test_llama_server_handle_close_marks_closed() -> None:
    handle, _ = _make_mocked_handle()
    handle.close()
    with pytest.raises(RuntimeError, match="closed"):
        handle.generate("p")


def test_llama_server_handle_health_passthrough() -> None:
    handle, client = _make_mocked_handle()
    client.health.return_value = {"status": "ok"}
    assert handle.health() == {"status": "ok"}


def test_llama_server_handle_model_info() -> None:
    handle, client = _make_mocked_handle()
    client.props.return_value = {"model": "test", "n_ctx": 8192}
    info = handle.model_info()
    assert info["n_ctx"] == 8192


# ---- HTTP integration via env var ----


def test_backends_use_http_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGMIND_LLAMA_SERVER_URL set → backend.load_llm() → LlamaServerHandle."""
    monkeypatch.setenv("AGMIND_LLAMA_SERVER_URL", "http://test:8080")
    from agmind.compute.backends.cpu import CPUBackend

    b = CPUBackend.make()
    handle = b.load_llm("some-model")
    assert isinstance(handle, LlamaServerHandle)


def test_backends_use_inprocess_without_env(
    monkeypatch: pytest.MonkeyPatch,
    has_llama_cpp: bool,
) -> None:
    """Без env var — fallback in-process llama_cpp (или RuntimeError если не installed)."""
    monkeypatch.delenv("AGMIND_LLAMA_SERVER_URL", raising=False)
    from agmind.compute.backends.cpu import CPUBackend

    b = CPUBackend.make()
    if has_llama_cpp:
        with pytest.raises((FileNotFoundError, OSError, ValueError, RuntimeError)):
            b.load_llm("/tmp/nonexistent.gguf")
    else:
        with pytest.raises(RuntimeError, match="llama-cpp-python is not installed"):
            b.load_llm("/tmp/x.gguf")
