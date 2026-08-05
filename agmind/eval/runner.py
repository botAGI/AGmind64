"""Evaluation run orchestration: corpus → chunks → retrieval → anchors → metrics → report.

Two retrievers ship in-box and both are read-only against the live stack:

``lexical``
    Okapi BM25 over the frozen corpus. No network, no key, always available. Its job is to be the
    floor — the score a bag-of-words baseline achieves on the same questions. In modern IR
    practice a strong lexical baseline is exactly what a dense system is expected to beat, so a
    dense score is only interpretable next to it.

``dense``
    Cosine similarity over embeddings from the stack's OWN embedding server (``bge-m3`` on the
    in-stack ``llama-embed``). Measuring the deployed model rather than a stand-in is the
    difference between a number about this installation and a number about nothing.

The corpus-wide ideal ranking is computed exactly (the corpus is small and pinned by manifest),
which is what lets nDCG measure *finding and ordering* rather than ordering alone.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agmind.eval.anchors import contains_anchor, coverage_map
from agmind.eval.cases import EvalCase
from agmind.eval.chunking import Chunk
from agmind.eval.corpus import CorpusManifest
from agmind.eval.ir import AggregateScore, CaseRetrieval, CaseScore, aggregate, score_case

#: Retriever-specific score scales are not comparable, so the abstention threshold is per
#: retriever. These are starting points, printed in the report scope so they are never implicit.
DEFAULT_ABSTAIN_THRESHOLD = {"lexical": 3.0, "dense": 0.55}


class EvalRunError(RuntimeError):
    """Raised when a run cannot be performed as specified (managed)."""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    text: str
    score: float


@dataclass(frozen=True)
class RunOutcome:
    scores: tuple[CaseScore, ...]
    aggregate: AggregateScore
    latency_ms: tuple[float, ...]

    @property
    def latency_p50(self) -> float | None:
        if not self.latency_ms:
            return None
        ordered = sorted(self.latency_ms)
        return ordered[len(ordered) // 2]


def corpus_ideal_gains(case: EvalCase, chunks: Sequence[Chunk]) -> list[int]:
    """Per-chunk anchor coverage over the WHOLE corpus, descending — the exact ideal ranking.

    Possible here only because the corpus is 30 files pinned by manifest; a TREC-scale pool would
    have to approximate. Using it means a retriever that misses an anchor entirely is penalised,
    which the retrieved-only fallback cannot express.
    """
    anchors = case.anchor_texts
    if not anchors:
        return []
    gains = [sum(1 for a in anchors if contains_anchor(chunk.text, a)) for chunk in chunks]
    return sorted(gains, reverse=True)


def run_cases(
    cases: Sequence[EvalCase],
    chunks: Sequence[Chunk],
    search: Callable[[EvalCase], Sequence[RetrievedChunk]],
    *,
    k: int = 5,
    abstain_threshold: float | None = None,
) -> RunOutcome:
    """Score every case with ``search(case) -> list[RetrievedChunk]``.

    ``search`` is injected rather than a retriever object so the harness is target-agnostic: the
    built-in retrievers, a future RAGFlow client, or a fake in a unit test all satisfy it.
    A retrieval that raises is recorded as ``errored`` rather than aborting the run — but any
    errored case makes the aggregate a biased subsample, which the report states loudly.
    """
    if k < 1:
        raise EvalRunError(f"k must be >= 1, got {k}")

    scores: list[CaseScore] = []
    latencies: list[float] = []

    for case in cases:
        started = time.monotonic()
        try:
            hits = list(search(case))
        except Exception:  # noqa: BLE001 - any client failure is a case-level error, not a crash
            scores.append(
                score_case(
                    CaseRetrieval(
                        case_id=case.case_id,
                        anchors=case.anchor_texts,
                        ranked_chunk_ids=(),
                        errored=True,
                    ),
                    k=k,
                )
            )
            continue
        latencies.append((time.monotonic() - started) * 1000.0)

        coverage = coverage_map({h.chunk_id: h.text for h in hits}, case.anchor_texts)
        retrieval = CaseRetrieval(
            case_id=case.case_id,
            anchors=case.anchor_texts,
            ranked_chunk_ids=tuple(h.chunk_id for h in hits),
            anchors_by_chunk=coverage,
            scores=tuple(h.score for h in hits),
            negative=not case.answerable,
        )
        scores.append(
            score_case(
                retrieval,
                k=k,
                ideal_gains=corpus_ideal_gains(case, chunks) or None,
                abstain_threshold=abstain_threshold,
            )
        )

    return RunOutcome(
        scores=tuple(scores), aggregate=aggregate(scores), latency_ms=tuple(latencies)
    )


def load_corpus_chunks(
    repo_root: Path, *, target_chars: int = 600, min_chars: int = 150
) -> tuple[CorpusManifest, list[Chunk]]:
    """Frozen manifest + chunked corpus, in one call (the shape every run needs)."""
    from agmind.eval.chunking import chunk_corpus, documents_from_manifest
    from agmind.eval.corpus import build_manifest

    manifest = build_manifest(repo_root)
    documents = documents_from_manifest(repo_root, manifest.doc_keys)
    chunks = chunk_corpus(documents, target_chars=target_chars, min_chars=min_chars)
    return manifest, chunks


__all__ = [
    "DEFAULT_ABSTAIN_THRESHOLD",
    "EvalRunError",
    "RetrievedChunk",
    "RunOutcome",
    "corpus_ideal_gains",
    "load_corpus_chunks",
    "run_cases",
]
