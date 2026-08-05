"""RAGFlow retrieval client — measures the deployed RAG stack, not a stand-in.

``POST /api/v1/retrieval`` is the only endpoint worth evaluating against. It returns, per chunk:
``id``, ``content``, ``document_id``, ``document_keyword`` and three separate similarity numbers
(``similarity``, ``vector_similarity``, ``term_similarity``), and it accepts
``vector_similarity_weight`` as a request parameter — so a run can sweep the dense/lexical balance
and attribute a failure to one side or the other.

``/api/v1/dify/retrieval`` is REFUSED, loudly. It drops the chunk id, flattens the three scores
into one, hard-codes the weight at 0.3 and applies no reranker — chunk-level ground truth is
impossible through it, and a number measured there is not comparable with one measured here.

Same zero-egress contract as the embeddings client: an :class:`EndpointVerdict` is required, the
opener ignores proxy environment variables and refuses redirects, and the API key is read from a
file or the environment and never printed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from agmind.eval.clients.embeddings import _direct_opener
from agmind.eval.endpoints import EndpointVerdict

#: Env var holding the RAGFlow API key. A file is preferred (``--api-key-file``, mode 0600).
API_KEY_ENV = "AGMIND_EVAL_RAGFLOW_API_KEY"

_FORBIDDEN_PATH = "/api/v1/dify/retrieval"


#: RAGFlow names a document by its upload filename and rejects ``/`` in it, so a corpus key has
#: to be flattened on the way in and restored on the way out. The two functions below are the
#: ONLY place that convention lives — a retrieved chunk whose document cannot be mapped back to a
#: corpus key would score zero anchors silently, which is the same defect (anchors credited in
#: the wrong document) that the doc-scoped coverage map already had to fix once.
_SEPARATOR = "__"


class RagflowError(RuntimeError):
    """Raised on transport, HTTP, auth or payload-shape failures (managed)."""


def corpus_key_to_filename(doc_key: str) -> str:
    """``docs/QUICKSTART.md`` -> ``docs__QUICKSTART.md`` (upload side)."""
    return doc_key.replace("/", _SEPARATOR)


def filename_to_corpus_key(name: str, known: frozenset[str]) -> str:
    """``docs__QUICKSTART.md`` -> ``docs/QUICKSTART.md``, verified against the frozen corpus.

    Raises rather than returning a best guess: an unmappable document means the dataset holds
    something the manifest does not, and every anchor in it would quietly fail to match.
    """
    restored = name.replace(_SEPARATOR, "/")
    if restored not in known:
        raise RagflowError(
            f"retrieved document {name!r} maps to {restored!r}, which is not in the frozen "
            "corpus. The dataset and the manifest have diverged — re-upload the corpus before "
            "measuring, or the score is about a different set of documents."
        )
    return restored


@dataclass(frozen=True)
class RagflowChunk:
    """One retrieved chunk, with RAGFlow's three separate similarity numbers preserved."""

    chunk_id: str
    content: str
    document_id: str
    document_name: str
    similarity: float
    vector_similarity: float
    term_similarity: float


def load_api_key(*, key_file: Path | None = None) -> str:
    """Read the API key from a 0600 file or the environment, with an actionable failure.

    RAGFlow has no key provisioning in the installer — the key is minted in its UI (or via
    ``POST /api/v1/system/tokens`` with a logged-in session) — so the error message says exactly
    that rather than leaving the operator to guess.
    """
    if key_file is not None:
        try:
            key = key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RagflowError(f"cannot read RAGFlow API key from {key_file}: {exc}") from exc
        if not key:
            raise RagflowError(f"RAGFlow API key file {key_file} is empty")
        return key

    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if not key:
        raise RagflowError(
            f"no RAGFlow API key: set {API_KEY_ENV} or pass --api-key-file <path>. "
            "Mint one in the RAGFlow UI (top-right avatar -> API -> API Key); the installer does "
            "not provision it."
        )
    return key


class RagflowRetrievalClient:
    """Client for RAGFlow's chunk-level retrieval endpoint."""

    def __init__(
        self,
        verdict: EndpointVerdict,
        api_key: str,
        *,
        dataset_ids: list[str],
        timeout: float = 60.0,
    ) -> None:
        if not verdict.allowed:
            raise RagflowError(
                f"refusing RAGFlow endpoint {verdict.url!r}: {verdict.reason}. "
                "Zero-egress requires a loopback endpoint (or an explicit LAN opt-in)."
            )
        if _FORBIDDEN_PATH in verdict.url:
            raise RagflowError(
                f"refusing {_FORBIDDEN_PATH}: it drops chunk ids, flattens the three similarity "
                "scores into one, hard-codes vector_similarity_weight=0.3 and applies no "
                "reranker. Chunk-level evaluation through it is impossible and its numbers are "
                "not comparable with /api/v1/retrieval. Point --ragflow-url at /api/v1/retrieval."
            )
        if not dataset_ids:
            raise RagflowError("at least one dataset id is required")

        self._verdict = verdict
        self._key = api_key
        self._dataset_ids = list(dataset_ids)
        self._timeout = timeout
        self._opener = _direct_opener()

    @property
    def endpoint(self) -> str:
        return self._verdict.url

    def retrieve(
        self,
        question: str,
        *,
        top_k: int = 5,
        vector_similarity_weight: float = 0.3,
        similarity_threshold: float = 0.0,
        rerank_id: str | None = None,
    ) -> list[RagflowChunk]:
        """Retrieve chunks for ``question``.

        ``similarity_threshold`` defaults to 0.0 so the harness sees the retriever's real ranking
        and applies its own abstention threshold — letting the server pre-filter would silently
        merge two different decisions (what was found, and what was confident enough to return).
        """
        payload: dict[str, object] = {
            "question": question,
            "dataset_ids": self._dataset_ids,
            "page": 1,
            "page_size": top_k,
            "similarity_threshold": similarity_threshold,
            "vector_similarity_weight": vector_similarity_weight,
        }
        if rerank_id:
            payload["rerank_id"] = rerank_id

        request = urllib.request.Request(
            self._verdict.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._key}",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RagflowError(f"RAGFlow HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RagflowError(f"RAGFlow transport failure: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RagflowError(f"RAGFlow returned non-JSON: {exc}") from exc

        # RAGFlow answers 200 with a non-zero `code` on auth/logic errors, so the status line
        # alone is not proof of success.
        if body.get("code"):
            raise RagflowError(f"RAGFlow error {body.get('code')}: {body.get('message')}")

        data = body.get("data") or {}
        rows = data.get("chunks") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RagflowError("RAGFlow response carried no 'chunks' list")

        return [
            RagflowChunk(
                chunk_id=str(row.get("id", "")),
                content=str(row.get("content") or row.get("content_with_weight") or ""),
                document_id=str(row.get("document_id", "")),
                document_name=str(row.get("document_keyword") or row.get("docnm_kwd") or ""),
                similarity=float(row.get("similarity") or 0.0),
                vector_similarity=float(row.get("vector_similarity") or 0.0),
                term_similarity=float(row.get("term_similarity") or 0.0),
            )
            for row in rows
        ]


__all__ = [
    "API_KEY_ENV",
    "RagflowChunk",
    "RagflowError",
    "RagflowRetrievalClient",
    "corpus_key_to_filename",
    "filename_to_corpus_key",
    "load_api_key",
]
