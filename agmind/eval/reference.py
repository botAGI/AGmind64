"""Built-in reference retriever: Okapi BM25 over the frozen corpus.

**Why a lexical baseline is the right reference, not a legacy shortcut.** In modern IR practice a
strong lexical baseline is the floor every dense/hybrid retriever is expected to clear — BEIR and
the TREC deep-learning tracks report it precisely because neural systems have repeatedly been
shown to underperform it on out-of-domain corpora. So this module serves three purposes that are
all about *proving* things rather than about retrieval quality:

1. **It proves the harness.** Corpus → chunks → retrieval → anchor matching → metrics → report →
   gate can be exercised end to end with no network, no API key and no live service, which means
   the measurement machinery is testable in CI and on a laptop.
2. **It proves the golden set discriminates.** A question set on which a bag-of-words retriever
   scores 1.0 is measuring nothing. The reference run is the cheapest possible check that the
   cases have teeth, and it is what the distractor class was designed to expose.
3. **It gives the operator a floor.** "Our production retriever scores X, a 200-line lexical
   baseline over the same corpus scores Y" is a far more meaningful statement than X alone.

Implementation is textbook Okapi BM25 (k1=1.2, b=0.75) with a Unicode-aware tokeniser, because
the corpus is bilingual RU/EN. numpy is a core dependency; nothing else is needed.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from agmind.eval.chunking import Chunk

#: Standard Okapi parameters. Not tuned — a tuned baseline would defeat the purpose of a floor.
BM25_K1 = 1.2
BM25_B = 0.75

#: Unicode-aware: ``\w`` under ``re.UNICODE`` keeps Cyrillic, and the extra class preserves the
#: dotted/underscored identifiers (``ttm.pages_limit``, ``AGMIND_OFFLINE``) that anchors rely on.
_TOKEN = re.compile(r"[\w][\w.\-_/=]*", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Casefolded tokens, keeping identifier punctuation that matters in a technical corpus."""
    return [m.group(0).casefold() for m in _TOKEN.finditer(text)]


@dataclass(frozen=True)
class ScoredChunk:
    """One retrieval hit, in the shape the harness consumes."""

    chunk_id: str
    doc_key: str
    score: float
    text: str


class ReferenceRetriever:
    """BM25 over a fixed chunk set. Deterministic: same corpus + same query → same ranking."""

    def __init__(self, chunks: Sequence[Chunk], *, k1: float = BM25_K1, b: float = BM25_B) -> None:
        if not chunks:
            raise ValueError("reference retriever needs a non-empty chunk set")
        self._chunks = list(chunks)
        self._k1 = k1
        self._b = b

        self._tokens: list[Counter[str]] = [Counter(tokenize(c.searchable_text)) for c in chunks]
        self._lengths = [sum(t.values()) for t in self._tokens]
        self._avg_len = sum(self._lengths) / len(self._lengths)

        document_frequency: Counter[str] = Counter()
        for counts in self._tokens:
            document_frequency.update(counts.keys())
        total = len(self._chunks)
        # Standard BM25 idf with the +0.5 smoothing; floored at a small positive value so a term
        # present in every chunk contributes nothing rather than a negative score.
        self._idf = {
            term: max(math.log((total - freq + 0.5) / (freq + 0.5) + 1.0), 1e-9)
            for term, freq in document_frequency.items()
        }

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def search(self, query: str, *, top_k: int = 5) -> list[ScoredChunk]:
        """Return the ``top_k`` highest-scoring chunks, ties broken by chunk id for determinism."""
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")
        query_terms = tokenize(query)

        scored: list[tuple[float, str, int]] = []
        for index, counts in enumerate(self._tokens):
            length = self._lengths[index] or 1
            total = 0.0
            for term in query_terms:
                freq = counts.get(term)
                if not freq:
                    continue
                idf = self._idf.get(term, 0.0)
                denominator = freq + self._k1 * (1 - self._b + self._b * length / self._avg_len)
                total += idf * (freq * (self._k1 + 1)) / denominator
            if total > 0.0:
                scored.append((total, self._chunks[index].chunk_id, index))

        # Sort by score desc, then chunk_id asc — a stable order is required for reproducibility.
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [
            ScoredChunk(
                chunk_id=self._chunks[i].chunk_id,
                doc_key=self._chunks[i].doc_key,
                score=score,
                text=self._chunks[i].text,
            )
            for score, _cid, i in scored[:top_k]
        ]


__all__ = ["BM25_B", "BM25_K1", "ReferenceRetriever", "ScoredChunk", "tokenize"]
