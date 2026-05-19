"""HTTP client для llama-server (OpenAI-compatible API).

llama-server (часть llama.cpp) предоставляет REST API совместимый с
OpenAI:
- POST /v1/completions    — legacy completion
- POST /v1/chat/completions — chat с messages structure
- POST /v1/embeddings     — embeddings
- POST /v1/rerank          — reranking
- GET  /health            — healthcheck
- GET  /props             — server metadata + model info

Stdlib only — без httpx/aiohttp. urllib.request + json + ssl.
Streaming через SSE парсится вручную (минимальная зависимость).

См. AGMIND_MIGRATION_SPEC.md §1.2.5 (llama_cpp engine реализация).
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence

from agmind.log import logger

log = logger(__name__)


class LlamaServerError(Exception):
    """Raised on llama-server HTTP errors (4xx/5xx, network)."""


@dataclass(frozen=True)
class SamplingParams:
    """Sampling параметры для generation.

    Default values совпадают с llama-server defaults. Override через
    explicit kwargs в `LLMHandle.generate()`.
    """

    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0
    repeat_penalty: float = 1.1
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    seed: int = -1
    """`-1` = случайный seed; иначе детерминированный."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "seed": self.seed,
        }


@dataclass
class LlamaServerClient:
    """REST client для llama-server (OpenAI-compatible).

    Use:
        client = LlamaServerClient("http://localhost:8080")
        text = client.complete("hello")
        for chunk in client.complete_stream("hello"): ...
        emb = client.embed(["text"])
        scores = client.rerank("query", ["doc1", "doc2"])
    """

    base_url: str
    """Base URL llama-server, e.g. 'http://llama-llm:8080'."""

    timeout: float = 120.0
    """HTTP timeout в секундах. Long для big completions."""

    api_key: str = ""
    """Optional bearer token (llama-server поддерживает `--api-key`)."""

    verify_ssl: bool = True
    """SSL verification (False для self-signed на LAN)."""

    extra_headers: dict[str, str] = field(default_factory=dict)

    # ---- Health / introspection ----

    def health(self) -> dict[str, Any]:
        """GET /health. Returns dict из JSON response."""
        return self._get("/health")

    def props(self) -> dict[str, Any]:
        """GET /props. Returns server props + loaded model metadata."""
        return self._get("/props")

    def is_alive(self) -> bool:
        """True если /health возвращает status: ok (или 200)."""
        try:
            data = self.health()
        except LlamaServerError:
            return False
        status = data.get("status", "")
        return bool(status == "ok" or not status)

    # ---- Completion (legacy) ----

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        stop: Sequence[str] | None = None,
        sampling: SamplingParams | None = None,
    ) -> str:
        """POST /v1/completions с stream=False. Returns full text."""
        body = self._completion_body(
            prompt, max_tokens=max_tokens, stop=stop, sampling=sampling,
            stream=False,
        )
        resp = self._post("/v1/completions", body)
        return _extract_completion_text(resp)

    def complete_stream(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        stop: Sequence[str] | None = None,
        sampling: SamplingParams | None = None,
    ) -> Iterator[str]:
        """POST /v1/completions с stream=True. Yields text chunks (deltas)."""
        body = self._completion_body(
            prompt, max_tokens=max_tokens, stop=stop, sampling=sampling,
            stream=True,
        )
        for event in self._post_sse("/v1/completions", body):
            text = _extract_completion_text(event)
            if text:
                yield text

    # ---- Chat ----

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int = 512,
        stop: Sequence[str] | None = None,
        sampling: SamplingParams | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> str:
        """POST /v1/chat/completions с stream=False.

        Args:
            messages: список dict'ов {"role": "user|assistant|system", "content": "..."}
            tools: optional OpenAI tool definitions (function calling).
        """
        body = self._chat_body(
            messages, max_tokens=max_tokens, stop=stop,
            sampling=sampling, tools=tools, stream=False,
        )
        resp = self._post("/v1/chat/completions", body)
        return _extract_chat_text(resp)

    def chat_stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int = 512,
        stop: Sequence[str] | None = None,
        sampling: SamplingParams | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> Iterator[str]:
        """POST /v1/chat/completions с stream=True. Yields delta chunks."""
        body = self._chat_body(
            messages, max_tokens=max_tokens, stop=stop,
            sampling=sampling, tools=tools, stream=True,
        )
        for event in self._post_sse("/v1/chat/completions", body):
            text = _extract_chat_delta(event)
            if text:
                yield text

    # ---- Embeddings ----

    def embed(
        self,
        texts: Sequence[str],
        *,
        model: str = "",
        encoding_format: str = "float",
    ) -> list[list[float]]:
        """POST /v1/embeddings. Returns list of float vectors."""
        body: dict[str, Any] = {
            "input": list(texts),
            "encoding_format": encoding_format,
        }
        if model:
            body["model"] = model
        resp = self._post("/v1/embeddings", body)
        data = resp.get("data") or []
        out: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            emb = item.get("embedding")
            if isinstance(emb, list):
                out.append([float(x) for x in emb])
            else:
                out.append([])
        return out

    # ---- Reranking ----

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
        model: str = "",
    ) -> list[float]:
        """POST /v1/rerank. Returns relevance scores in input order.

        llama-server's /rerank endpoint requires server started with
        `--reranking` (pooling=rank). Returns scores в том же порядке
        что documents (не sorted).
        """
        body: dict[str, Any] = {
            "query": query,
            "documents": list(documents),
        }
        if top_n is not None:
            body["top_n"] = top_n
        if model:
            body["model"] = model

        # llama-server endpoints: /reranking, /rerank, /v1/rerank.
        # Try /v1/rerank first (OpenAI-compatible), fallback /rerank.
        try:
            resp = self._post("/v1/rerank", body)
        except LlamaServerError as exc:
            log.debug("/v1/rerank failed (%s), trying /rerank", exc)
            resp = self._post("/rerank", body)

        results = resp.get("results") or []
        # Build score array indexed by original document position.
        scores = [0.0] * len(documents)
        for r in results:
            if not isinstance(r, dict):
                continue
            idx = r.get("index")
            score = r.get("relevance_score") or r.get("score") or 0.0
            if isinstance(idx, int) and 0 <= idx < len(scores):
                scores[idx] = float(score)
        return scores

    # ---- HTTP plumbing ----

    def _completion_body(
        self,
        prompt: str,
        *,
        max_tokens: int,
        stop: Sequence[str] | None,
        sampling: SamplingParams | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "stream": stream,
            "n_predict": max_tokens,  # legacy llama.cpp param
        }
        if stop:
            body["stop"] = list(stop)
        body.update((sampling or SamplingParams()).to_dict())
        return body

    def _chat_body(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int,
        stop: Sequence[str] | None,
        sampling: SamplingParams | None,
        tools: Sequence[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": list(messages),
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stop:
            body["stop"] = list(stop)
        if tools:
            body["tools"] = list(tools)
        body.update((sampling or SamplingParams()).to_dict())
        return body

    def _get(self, path: str) -> dict[str, Any]:
        return self._request("GET", path, body=None)

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, body=body)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        for k, v in self.extra_headers.items():
            req.add_header(k, v)

        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout,
                context=ctx if url.startswith("https") else None,
            ) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            raise LlamaServerError(
                f"HTTP {exc.code} {method} {path}: {body_text}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LlamaServerError(
                f"Network error {method} {url}: {exc.reason}"
            ) from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LlamaServerError(
                f"Non-JSON response from {url}: {raw[:200]}"
            ) from exc

    def _post_sse(
        self,
        path: str,
        body: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        """POST with stream=true, parse SSE response. Yields parsed JSON events."""
        url = self.base_url.rstrip("/") + path
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")

        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        try:
            resp = urllib.request.urlopen(
                req, timeout=self.timeout,
                context=ctx if url.startswith("https") else None,
            )
        except urllib.error.HTTPError as exc:
            raise LlamaServerError(
                f"HTTP {exc.code} streaming POST {path}"
            ) from exc
        except urllib.error.URLError as exc:
            raise LlamaServerError(
                f"Network error streaming POST {url}: {exc.reason}"
            ) from exc

        try:
            for line in _iter_sse_events(resp):
                if not line:
                    continue
                if line.strip() == "[DONE]":
                    return
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.debug("SSE non-JSON event: %.80s", line)
                    continue
        finally:
            resp.close()


# ---- helpers ----


def _iter_sse_events(resp: Any) -> Iterable[str]:
    """Parse Server-Sent Events stream. Yields raw data: payloads."""
    for raw_line in resp:
        if not raw_line:
            continue
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("data:"):
            yield line[5:].strip()


def _extract_completion_text(payload: dict[str, Any]) -> str:
    """Extract text from /v1/completions response (full или streaming chunk)."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        # llama.cpp's native /completion uses {"content": "..."}
        return str(payload.get("content", ""))
    return str(choices[0].get("text", ""))


def _extract_chat_text(payload: dict[str, Any]) -> str:
    """Extract assistant message content from full chat response."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content", ""))


def _extract_chat_delta(payload: dict[str, Any]) -> str:
    """Extract delta content from streaming chat chunk."""
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content", ""))
