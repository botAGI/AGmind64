"""Deterministic markdown chunker for the evaluation corpus.

Used by the built-in reference retriever (:mod:`agmind.eval.reference`). It is NOT an attempt to
replicate RAGFlow's chunker — that would be a fiction, since RAGFlow's chunking depends on its
parser configuration and changes between versions. The point of this chunker is different: it
gives the harness a **stable, inspectable, zero-dependency** view of the corpus so the whole
measurement pipeline can be proven end-to-end, and so a lexical floor score can be computed for
the golden set.

Splitting is heading-aware: markdown documents carry their structure in headings, and a chunk
that spans two unrelated sections is noise for both. Within a section, long runs are split on a
size budget at paragraph boundaries so a chunk never ends mid-sentence unless a single paragraph
exceeds the budget on its own.

Chunk ids are ``<doc_key>#<ordinal>`` — deterministic, human-readable, and traceable back to the
document without a lookup table.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

#: Target chunk size in characters. Chosen to sit in the same order of magnitude as a typical
#: production chunker (a few hundred tokens) without pretending to match any specific one.
DEFAULT_TARGET_CHARS = 1200
#: Never emit a chunk smaller than this unless it is the last of its section (avoids a tail of
#: one-line fragments that inflate the chunk count and distort precision denominators).
DEFAULT_MIN_CHARS = 200

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit of the corpus."""

    chunk_id: str
    doc_key: str
    heading_path: tuple[str, ...]
    text: str

    @property
    def searchable_text(self) -> str:
        """Heading path prepended: a section title is real retrieval signal, and a chunk taken
        out of its document loses that context otherwise."""
        return " ".join([*self.heading_path, self.text])


def _hard_split(text: str, limit: int) -> list[str]:
    """Split a single oversized paragraph on line boundaries.

    A paragraph-boundary chunker cannot bound its output: one fenced code block or one long
    table is a single paragraph and can be many kilobytes. That is not theoretical — the deployed
    ``llama-embed`` server rejects any input above its 512-token physical batch (HTTP 500,
    observed at 680 tokens), so an unbounded chunk makes the corpus un-embeddable. Splitting on
    lines keeps code blocks readable rather than cutting mid-token.
    """
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buffer = ""
    for line in text.splitlines():
        candidate = f"{buffer}\n{line}" if buffer else line
        if len(candidate) > limit and buffer:
            out.append(buffer)
            buffer = line
        else:
            buffer = candidate
    if buffer:
        out.append(buffer)
    return out


def _split_paragraphs(block: str, limit: int) -> list[str]:
    paragraphs = [p for p in re.split(r"\n\s*\n", block) if p.strip()]
    out: list[str] = []
    for paragraph in paragraphs:
        out.extend(_hard_split(paragraph, limit))
    return out


def chunk_document(
    doc_key: str,
    text: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[Chunk]:
    """Split one markdown document into heading-aware chunks."""
    sections: list[tuple[tuple[str, ...], list[str]]] = []
    heading_stack: list[str] = []
    current: list[str] = []

    def _flush() -> None:
        if current:
            sections.append((tuple(heading_stack), list(current)))
            current.clear()

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            _flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            del heading_stack[level - 1 :]
            heading_stack.append(title)
            continue
        current.append(line)
    _flush()

    chunks: list[Chunk] = []
    ordinal = 0
    for heading_path, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        buffer = ""
        for paragraph in _split_paragraphs(body, target_chars):
            candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
            if len(candidate) <= target_chars or not buffer:
                buffer = candidate
                continue
            chunks.append(Chunk(f"{doc_key}#{ordinal}", doc_key, heading_path, buffer.strip()))
            ordinal += 1
            buffer = paragraph
        if buffer.strip():
            # Merge a runt tail into the previous chunk of the SAME section rather than emitting
            # a fragment that would distort the precision denominator.
            if len(buffer.strip()) < min_chars and chunks and chunks[-1].doc_key == doc_key:
                previous = chunks[-1]
                if previous.heading_path == heading_path:
                    chunks[-1] = Chunk(
                        previous.chunk_id,
                        doc_key,
                        heading_path,
                        f"{previous.text}\n\n{buffer.strip()}",
                    )
                    continue
            chunks.append(Chunk(f"{doc_key}#{ordinal}", doc_key, heading_path, buffer.strip()))
            ordinal += 1

    return chunks


def chunk_corpus(
    documents: Mapping[str, str],
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[Chunk]:
    """Chunk every document, in a deterministic order (sorted by ``doc_key``)."""
    out: list[Chunk] = []
    for doc_key in sorted(documents):
        out.extend(
            chunk_document(
                doc_key, documents[doc_key], target_chars=target_chars, min_chars=min_chars
            )
        )
    return out


def chunk_texts(chunks: Iterable[Chunk]) -> dict[str, str]:
    """``chunk_id -> text`` map, the shape :mod:`agmind.eval.anchors` consumes."""
    return {c.chunk_id: c.text for c in chunks}


def documents_from_manifest(repo_root: Path, doc_keys: Sequence[str]) -> dict[str, str]:
    """Read the corpus documents named by a manifest into memory."""
    root = Path(repo_root)
    return {key: (root / key).read_text(encoding="utf-8") for key in doc_keys}


__all__ = [
    "DEFAULT_MIN_CHARS",
    "DEFAULT_TARGET_CHARS",
    "Chunk",
    "chunk_corpus",
    "chunk_document",
    "chunk_texts",
    "documents_from_manifest",
]
