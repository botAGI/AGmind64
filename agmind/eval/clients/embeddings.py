"""OpenAI-compatible embeddings client (llama.cpp ``/v1/embeddings``).

Used by the dense reference retriever to measure retrieval with the SAME embedding model the
stack serves in production (``bge-m3`` on the in-stack ``llama-embed``), rather than with a
stand-in. Measuring a different embedder than the one deployed would produce a number that is
true about nothing.

Stdlib ``urllib`` only, matching the repo's client convention (``agmind/compute/clients/*.py``)
and avoiding a dependency plane change for a single POST.

Zero-egress: the constructor REQUIRES an :class:`~agmind.eval.endpoints.EndpointVerdict` and
refuses a verdict that is not allowed. The check is re-asserted here rather than trusted from a
caller-side pre-flight, because a pre-flight that happens earlier than the request is a TOCTOU
gap, and because the whole point is that no code path can reach the network without a decision
having been made about the destination.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from agmind.eval.endpoints import EndpointVerdict


class EmbeddingError(RuntimeError):
    """Raised on transport, HTTP or payload-shape failures (managed, never a traceback)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every 3xx.

    Classifying the endpoint address is worthless if the server can then redirect the request
    anywhere: a loopback service answering ``302 Location: https://evil.example/collect`` would
    ship the query and the chunk text off-box with the allow-list none the wiser. There is no
    legitimate reason for an OpenAI-compatible embeddings endpoint to redirect.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            f"refusing redirect to {newurl!r}: the endpoint was allow-listed, the redirect target "
            "was not",
            headers,
            fp,
        )


def _direct_opener() -> urllib.request.OpenerDirector:
    """An opener that ignores ``http_proxy``/``https_proxy`` and refuses redirects.

    urllib honours the proxy environment by default, so an exported ``http_proxy`` silently sends
    every request to a third party while the endpoint check still says "loopback". Both holes are
    closed by constructing the opener explicitly rather than using the module-level ``urlopen``.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


@dataclass(frozen=True)
class EmbeddingBatch:
    """Vectors for one request, plus what it cost."""

    vectors: tuple[tuple[float, ...], ...]
    model: str
    prompt_tokens: int

    @property
    def dimension(self) -> int:
        return len(self.vectors[0]) if self.vectors else 0


class EmbeddingClient:
    """Minimal client for an OpenAI-compatible ``/v1/embeddings`` endpoint."""

    def __init__(
        self,
        verdict: EndpointVerdict,
        *,
        model: str = "bge-m3",
        timeout: float = 120.0,
    ) -> None:
        if not verdict.allowed:
            raise EmbeddingError(
                f"refusing to use embedding endpoint {verdict.url!r}: {verdict.reason}. "
                "Zero-egress requires a loopback endpoint (or an explicit LAN opt-in)."
            )
        self._verdict = verdict
        self._model = model
        self._timeout = timeout
        self._opener = _direct_opener()

    @property
    def endpoint(self) -> str:
        return self._verdict.url

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        """Embed a batch of strings, preserving input order."""
        if not texts:
            raise EmbeddingError("embed() called with an empty batch")

        payload = json.dumps({"input": texts, "model": self._model}).encode("utf-8")
        request = urllib.request.Request(
            self._verdict.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise EmbeddingError(f"embeddings HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise EmbeddingError(f"embeddings transport failure: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise EmbeddingError(f"embeddings returned non-JSON: {exc}") from exc

        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise EmbeddingError(
                f"embeddings returned {len(rows) if isinstance(rows, list) else 'no'} vectors "
                f"for {len(texts)} inputs — refusing to guess the alignment"
            )
        # Order is contractual in the OpenAI shape but cheap to enforce; a silently permuted
        # batch would mis-attribute every vector and corrupt the whole measurement.
        ordered = sorted(rows, key=lambda r: int(r.get("index", 0)))
        vectors = tuple(tuple(float(x) for x in row["embedding"]) for row in ordered)

        usage = body.get("usage") or {}
        return EmbeddingBatch(
            vectors=vectors,
            model=str(body.get("model", self._model)),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
        )

    def embed_all(self, texts: list[str], *, batch_size: int = 32) -> list[tuple[float, ...]]:
        """Embed a long list in batches, preserving order across batches."""
        if batch_size < 1:
            raise EmbeddingError(f"batch_size must be >= 1, got {batch_size}")
        out: list[tuple[float, ...]] = []
        for start in range(0, len(texts), batch_size):
            out.extend(self.embed(texts[start : start + batch_size]).vectors)
        return out


__all__ = ["EmbeddingBatch", "EmbeddingClient", "EmbeddingError"]
