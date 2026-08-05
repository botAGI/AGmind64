"""The corpus fingerprint must identify the CORPUS, not the commit that happened to be checked out.

It is the run-identity key: the regression gate refuses to compare two runs whose fingerprints
differ, on the correct reasoning that a changed corpus makes a score difference something other
than a regression. ``_comparable_key`` therefore excludes ``corpus_ref`` deliberately — a commit
is provenance, not experimental identity.

That exclusion was defeated by hashing ``corpus_ref`` INTO the fingerprint, which carried the
commit back in through the fingerprint field. Demonstrated end to end: a baseline recorded, one
commit touching only ``docs/eval/`` (a directory excluded from the corpus), and the gate answered
REFUSING TO COMPARE with every other component of the key identical. A gate that refuses after
any commit cannot catch a regression, which is the only thing it exists to do.
"""

from __future__ import annotations

import pytest

from agmind.eval.corpus import CorpusDoc, CorpusManifest

pytestmark = pytest.mark.backend_any


def _manifest(ref: str, docs: tuple[tuple[str, str], ...]) -> CorpusManifest:
    return CorpusManifest(
        corpus_ref=ref,
        docs=tuple(CorpusDoc(doc_key=key, sha256=sha, bytes=len(sha)) for key, sha in docs),
    )


_DOCS = (("docs/A.md", "a" * 64), ("docs/B.md", "b" * 64))


def test_same_content_at_a_different_commit_is_the_same_corpus() -> None:
    """The scenario that made the gate inert: identical documents, one commit apart."""
    assert _manifest("c" * 40, _DOCS).fingerprint() == _manifest("d" * 40, _DOCS).fingerprint()


def test_changed_document_content_changes_the_fingerprint() -> None:
    edited = (("docs/A.md", "a" * 64), ("docs/B.md", "e" * 64))
    assert _manifest("c" * 40, _DOCS).fingerprint() != _manifest("c" * 40, edited).fingerprint()


def test_added_document_changes_the_fingerprint() -> None:
    extended = (*_DOCS, ("docs/C.md", "f" * 64))
    assert _manifest("c" * 40, _DOCS).fingerprint() != _manifest("c" * 40, extended).fingerprint()


def test_renamed_document_changes_the_fingerprint() -> None:
    """Same bytes under a different key is a different corpus: anchors are scoped by document."""
    renamed = (("docs/RENAMED.md", "a" * 64), ("docs/B.md", "b" * 64))
    assert _manifest("c" * 40, _DOCS).fingerprint() != _manifest("c" * 40, renamed).fingerprint()


def test_gate_compares_two_runs_one_commit_apart() -> None:
    """The end-to-end consequence, at the layer that actually refused."""
    from agmind.eval.gate import _comparable_key

    scope = {
        "retriever": "lexical",
        "corpus_fingerprint": _manifest("c" * 40, _DOCS).fingerprint(),
        "corpus_ref": "c" * 40,
        "k": 5,
        "golden_set_cases": 15,
        "corpus_chunks": 643,
    }
    later = dict(scope, corpus_ref="d" * 40)
    later["corpus_fingerprint"] = _manifest("d" * 40, _DOCS).fingerprint()

    assert _comparable_key(scope) == _comparable_key(later)
