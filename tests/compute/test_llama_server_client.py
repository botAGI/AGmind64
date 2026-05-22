"""Tests для agmind.compute.clients.llama_server — HTTP client.

Tests мокают urllib через unittest.mock (без реального server).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from agmind.compute.clients.llama_server import (
    LlamaServerClient,
    LlamaServerError,
    SamplingParams,
    _extract_chat_delta,
    _extract_chat_text,
    _extract_completion_text,
)

pytestmark = pytest.mark.backend_any


# ---- SamplingParams ----


def test_sampling_params_defaults() -> None:
    sp = SamplingParams()
    assert sp.temperature == 0.7
    assert sp.top_p == 0.9
    assert sp.seed == -1


def test_sampling_params_to_dict() -> None:
    sp = SamplingParams(temperature=0.5, top_p=0.95, seed=42)
    d = sp.to_dict()
    assert d["temperature"] == 0.5
    assert d["top_p"] == 0.95
    assert d["seed"] == 42
    # Все expected keys
    expected_keys = {
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "frequency_penalty",
        "presence_penalty",
        "seed",
    }
    assert set(d.keys()) == expected_keys


def test_sampling_params_frozen() -> None:
    sp = SamplingParams()
    with pytest.raises((AttributeError, Exception)):
        sp.temperature = 0.5  # type: ignore[misc]


# ---- LlamaServerClient construction ----


def test_client_base_url() -> None:
    c = LlamaServerClient("http://localhost:8080")
    assert c.base_url == "http://localhost:8080"


def test_client_with_trailing_slash() -> None:
    c = LlamaServerClient("http://localhost:8080/")
    # _request strips trailing slash; check via url construction
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp
        c.health()
        called_url = mock_urlopen.call_args[0][0].full_url
        assert called_url == "http://localhost:8080/health"


def test_client_default_timeout() -> None:
    c = LlamaServerClient("http://localhost:8080")
    assert c.timeout == 120.0


def test_client_custom_timeout() -> None:
    c = LlamaServerClient("http://localhost:8080", timeout=30.0)
    assert c.timeout == 30.0


# ---- health / is_alive ----


def test_health_returns_dict() -> None:
    c = LlamaServerClient("http://localhost:8080")
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok", "slots_idle": 4}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_resp
        result = c.health()
    assert result["status"] == "ok"
    assert result["slots_idle"] == 4


def test_is_alive_true_on_status_ok() -> None:
    c = LlamaServerClient("http://localhost:8080")
    with patch.object(c, "health", return_value={"status": "ok"}):
        assert c.is_alive() is True


def test_is_alive_false_on_error() -> None:
    c = LlamaServerClient("http://localhost:8080")
    with patch.object(c, "health", side_effect=LlamaServerError("connection refused")):
        assert c.is_alive() is False


# ---- completion ----


def test_complete_full_response() -> None:
    c = LlamaServerClient("http://localhost:8080")
    payload = {
        "choices": [{"text": "Hello, world!"}],
    }
    with patch.object(c, "_post", return_value=payload) as mock_post:
        result = c.complete("Hello")
    assert result == "Hello, world!"
    mock_post.assert_called_once()
    # Check body has stream=False
    body = mock_post.call_args[0][1]
    assert body["stream"] is False
    assert body["prompt"] == "Hello"


def test_complete_with_sampling_params() -> None:
    c = LlamaServerClient("http://localhost:8080")
    sp = SamplingParams(temperature=0.3, top_p=0.5)
    payload = {"choices": [{"text": "deterministic"}]}
    with patch.object(c, "_post", return_value=payload) as mock_post:
        c.complete("x", sampling=sp)
    body = mock_post.call_args[0][1]
    assert body["temperature"] == 0.3
    assert body["top_p"] == 0.5


def test_complete_with_stop_tokens() -> None:
    c = LlamaServerClient("http://localhost:8080")
    payload = {"choices": [{"text": "stop"}]}
    with patch.object(c, "_post", return_value=payload) as mock_post:
        c.complete("x", stop=["</end>", "###"])
    body = mock_post.call_args[0][1]
    assert body["stop"] == ["</end>", "###"]


# ---- chat ----


def test_chat_extracts_message_content() -> None:
    c = LlamaServerClient("http://localhost:8080")
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "Hi there"}}],
    }
    with patch.object(c, "_post", return_value=payload) as mock_post:
        result = c.chat([{"role": "user", "content": "Hi"}])
    assert result == "Hi there"
    body = mock_post.call_args[0][1]
    assert body["messages"] == [{"role": "user", "content": "Hi"}]


def test_chat_with_tools() -> None:
    c = LlamaServerClient("http://localhost:8080")
    payload = {"choices": [{"message": {"content": ""}}]}
    tools = [{"type": "function", "function": {"name": "weather"}}]
    with patch.object(c, "_post", return_value=payload) as mock_post:
        c.chat([{"role": "user", "content": "x"}], tools=tools)
    body = mock_post.call_args[0][1]
    assert body["tools"] == tools


# ---- embed ----


def test_embed_returns_vectors() -> None:
    c = LlamaServerClient("http://localhost:8080")
    payload = {
        "data": [
            {"embedding": [0.1, 0.2, 0.3]},
            {"embedding": [0.4, 0.5, 0.6]},
        ],
    }
    with patch.object(c, "_post", return_value=payload) as mock_post:
        vectors = c.embed(["a", "b"])
    assert vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    body = mock_post.call_args[0][1]
    assert body["input"] == ["a", "b"]


def test_embed_empty_response() -> None:
    c = LlamaServerClient("http://localhost:8080")
    with patch.object(c, "_post", return_value={"data": []}):
        assert c.embed(["x"]) == []


def test_embed_missing_data_field() -> None:
    c = LlamaServerClient("http://localhost:8080")
    with patch.object(c, "_post", return_value={}):
        assert c.embed(["x"]) == []


# ---- rerank ----


def test_rerank_orders_scores_by_index() -> None:
    c = LlamaServerClient("http://localhost:8080")
    payload = {
        "results": [
            {"index": 2, "relevance_score": 0.95},
            {"index": 0, "relevance_score": 0.42},
            {"index": 1, "relevance_score": 0.78},
        ],
    }
    with patch.object(c, "_post", return_value=payload):
        scores = c.rerank("query", ["doc0", "doc1", "doc2"])
    assert scores == [0.42, 0.78, 0.95]


def test_rerank_partial_results() -> None:
    """Missing scores → 0.0 default."""
    c = LlamaServerClient("http://localhost:8080")
    payload = {"results": [{"index": 1, "relevance_score": 0.9}]}
    with patch.object(c, "_post", return_value=payload):
        scores = c.rerank("q", ["doc0", "doc1", "doc2"])
    assert scores == [0.0, 0.9, 0.0]


def test_rerank_fallback_to_legacy_endpoint() -> None:
    """If /v1/rerank fails — try /rerank."""
    c = LlamaServerClient("http://localhost:8080")
    counter = {"calls": 0}

    def fake_post(path: str, body: dict) -> dict:
        counter["calls"] += 1
        if path == "/v1/rerank":
            raise LlamaServerError("404 not found")
        return {"results": [{"index": 0, "score": 0.5}]}

    with patch.object(c, "_post", side_effect=fake_post):
        scores = c.rerank("q", ["d"])
    assert scores == [0.5]
    assert counter["calls"] == 2


# ---- streaming SSE ----


def test_complete_stream_parses_sse() -> None:
    """SSE parsing — yields incremental text chunks."""
    c = LlamaServerClient("http://localhost:8080")

    sse_events = [
        {"choices": [{"text": "Hello"}]},
        {"choices": [{"text": ", "}]},
        {"choices": [{"text": "world"}]},
    ]

    def fake_sse(path: str, body: dict) -> Iterator[dict]:
        for ev in sse_events:
            yield ev

    with patch.object(c, "_post_sse", side_effect=fake_sse):
        chunks = list(c.complete_stream("Hi"))
    assert chunks == ["Hello", ", ", "world"]


def test_chat_stream_parses_delta_chunks() -> None:
    c = LlamaServerClient("http://localhost:8080")

    sse_events = [
        {"choices": [{"delta": {"content": "Hi"}}]},
        {"choices": [{"delta": {"content": " there"}}]},
        {"choices": [{"delta": {}}]},  # empty delta — skip
    ]

    def fake_sse(path: str, body: dict) -> Iterator[dict]:
        for ev in sse_events:
            yield ev

    with patch.object(c, "_post_sse", side_effect=fake_sse):
        chunks = list(c.chat_stream([{"role": "user", "content": "x"}]))
    assert chunks == ["Hi", " there"]


# ---- error handling ----


def test_http_error_raises_llamaserver_error() -> None:
    c = LlamaServerClient("http://localhost:8080", timeout=1.0)
    import urllib.error

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:8080/health",
            code=503,
            msg="Service Unavailable",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with pytest.raises(LlamaServerError, match="HTTP 503"):
            c.health()


def test_network_error_raises_llamaserver_error() -> None:
    c = LlamaServerClient("http://localhost:8080", timeout=1.0)
    import urllib.error

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        with pytest.raises(LlamaServerError, match="Network error"):
            c.health()


# ---- extractor helpers ----


def test_extract_completion_text_openai_format() -> None:
    payload = {"choices": [{"text": "abc"}]}
    assert _extract_completion_text(payload) == "abc"


def test_extract_completion_text_native_format() -> None:
    payload = {"content": "native llama.cpp /completion response"}
    assert _extract_completion_text(payload) == "native llama.cpp /completion response"


def test_extract_completion_text_empty() -> None:
    assert _extract_completion_text({}) == ""
    assert _extract_completion_text({"choices": []}) == ""


def test_extract_chat_text() -> None:
    payload = {"choices": [{"message": {"role": "assistant", "content": "Hi"}}]}
    assert _extract_chat_text(payload) == "Hi"


def test_extract_chat_delta() -> None:
    payload = {"choices": [{"delta": {"content": "chunk"}}]}
    assert _extract_chat_delta(payload) == "chunk"


def test_extract_chat_delta_empty() -> None:
    assert _extract_chat_delta({}) == ""
    assert _extract_chat_delta({"choices": [{}]}) == ""
