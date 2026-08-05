"""Dense reference retriever over the frozen corpus, using the stack's own embedding model.

Pairs with :mod:`agmind.eval.reference` (BM25). Together they give the operator the only
comparison that makes a retrieval number meaningful on a small corpus:

    lexical floor  →  what a bag-of-words baseline achieves
    dense          →  what the embedding model actually deployed here achieves

A dense score that fails to clear the lexical floor is a finding, not a rounding error: it is the
documented BEIR result that neural retrievers can underperform BM25 out of domain, reproduced on
your own corpus with your own model.

Embedding vectors are cached on disk keyed by (corpus fingerprint, model, chunk id), because
embedding 500+ chunks takes real time and the corpus only changes when the manifest does. The
cache key includes the fingerprint precisely so a corpus edit cannot silently reuse stale vectors.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agmind.eval.chunking import Chunk
from agmind.eval.reference import ScoredChunk


class DenseRetrievalError(RuntimeError):
    """Raised when dense retrieval cannot be performed (managed)."""


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 for a zero vector rather than raising."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class EmbeddingCache:
    """On-disk vector cache, invalidated by corpus fingerprint and model name."""

    path: Path
    fingerprint: str
    model: str

    def _key(self) -> str:
        return hashlib.sha256(f"{self.fingerprint}|{self.model}".encode()).hexdigest()[:16]

    def _file(self) -> Path:
        return self.path / f"chunks-{self._key()}.json"

    def load(self) -> dict[str, list[float]] | None:
        file = self._file()
        if not file.is_file():
            return None
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        vectors = payload.get("vectors")
        return vectors if isinstance(vectors, dict) else None

    def store(self, vectors: dict[str, list[float]]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._file().write_text(
            json.dumps(
                {"fingerprint": self.fingerprint, "model": self.model, "vectors": vectors},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


class DenseRetriever:
    """Cosine-similarity retrieval over embedded chunks."""

    def __init__(self, chunks: Sequence[Chunk], vectors: dict[str, Sequence[float]]) -> None:
        missing = [c.chunk_id for c in chunks if c.chunk_id not in vectors]
        if missing:
            raise DenseRetrievalError(
                f"{len(missing)} chunk(s) have no embedding (first: {missing[0]}) — "
                "refusing to rank on a partial index, which would silently drop documents"
            )
        self._chunks = list(chunks)
        self._vectors = vectors

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def search(self, query_vector: Sequence[float], *, top_k: int = 5) -> list[ScoredChunk]:
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")
        scored = [
            (cosine(query_vector, self._vectors[c.chunk_id]), c.chunk_id, c) for c in self._chunks
        ]
        # Score desc, chunk_id asc — deterministic ties, so a rerun reproduces the ranking.
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [
            ScoredChunk(chunk_id=c.chunk_id, doc_key=c.doc_key, score=score, text=c.text)
            for score, _cid, c in scored[:top_k]
        ]


__all__ = ["DenseRetrievalError", "DenseRetriever", "EmbeddingCache", "cosine"]
