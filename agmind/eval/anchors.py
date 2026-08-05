"""Anchor matching — the relevance decision, made without an LLM (AI-SPEC §5.2, §6.1).

An *anchor* is a short verbatim string from the corpus that a correct retrieval must surface.
A retrieved chunk is judged relevant for a case if it **contains** one of that case's anchors
after normalisation. That is the whole relevance model: no embeddings, no judge, no labels that
rot — which is what makes layer 1 deterministic, reproducible and free.

Why content identity rather than chunk ids: RAGFlow's own evaluation schema keys on
``relevant_chunk_ids``, but those UUIDs are destroyed by reindexing, re-chunking, a backend swap
or a wipe. Binding the scarcest resource in the system — human judgement — to identifiers that
die on the first reindex is self-defeating. An anchor survives all of it.

**Known limitation, stated rather than hidden.** Answer-string containment is a classic
open-domain-QA proxy and it has documented failure modes in both directions: a chunk can contain
the string incidentally (false positive) or express the same fact in other words (false
negative). Mitigations actually implemented here and in the golden-set discipline:
  * anchors are chosen to be *distinctive* identifiers, not common words — an incidental
    occurrence of ``mem_info_gtt_total`` is not plausible the way one of "memory" would be;
  * the integrity gate proves every anchor exists verbatim in its named document, so the set
    cannot accumulate hallucinated ground truth;
  * a case may carry several anchors, and recall is measured over anchors covered;
  * the false-negative direction is accepted deliberately: this layer measures whether the
    retriever surfaced *the passage the operator would need*, which is a narrower and more
    honest question than "is the answer semantically present somewhere".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC → casefold → collapse whitespace.

    NFKC folds the compatibility forms that survive a markdown→chunker round trip (non-breaking
    spaces, full-width punctuation, ligatures). ``casefold`` rather than ``lower`` because the
    corpus is bilingual RU/EN and casefold is the Unicode-correct caseless-matching operation.
    Whitespace is collapsed because chunkers reflow line breaks, so a two-word anchor must match
    across a wrap.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", folded).strip()


def contains_anchor(chunk_text: str, anchor: str) -> bool:
    """Whether ``chunk_text`` contains ``anchor`` under :func:`normalize`.

    An empty anchor never matches: it would make every chunk relevant and silently turn the
    metric into a constant 1.0.
    """
    needle = normalize(anchor)
    if not needle:
        return False
    return needle in normalize(chunk_text)


def anchors_covered(chunk_text: str, anchors: Iterable[str]) -> frozenset[str]:
    """The subset of ``anchors`` present in ``chunk_text``.

    Returned keyed by the ORIGINAL anchor text, so the caller's ids stay stable in reports even
    though matching happened on the normalised form.
    """
    return frozenset(a for a in anchors if contains_anchor(chunk_text, a))


def coverage_map(
    chunks: Mapping[str, str],
    anchors: Iterable[str],
) -> dict[str, frozenset[str]]:
    """Map ``chunk_id -> anchors it covers`` for every chunk in ``chunks``.

    This is the bridge into :mod:`agmind.eval.ir`: the metrics module takes exactly this shape
    and never sees raw text, which keeps it pure arithmetic.
    """
    anchor_list = list(anchors)
    return {cid: anchors_covered(text, anchor_list) for cid, text in chunks.items()}


__all__ = ["anchors_covered", "contains_anchor", "coverage_map", "normalize"]
