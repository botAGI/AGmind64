"""Phase 18 (M11) — the load-bearing gate: every anchor must exist VERBATIM in the corpus.

This is the single most important test in the eval subsystem. A golden set whose anchors are not
checked against the corpus silently accumulates hallucinated ground truth, and then every metric
computed from it is measuring agreement with a fiction. During the design of this milestone a
plausible-looking anchor (``PHOENIX_DEFAULT_RETENTION_POLICY_DAYS``) was proposed and grep proved
it appears nowhere in the corpus — exactly the failure this gate exists to make impossible.

It is also the anti-circularity guard: the cases were authored by reading the corpus, so the one
thing that must be mechanically true is that each claimed anchor really is in the document it
names. Wording quality is a human judgement; anchor existence is not, and is checked here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = _REPO_ROOT / "templates" / "eval" / "rag" / "golden.jsonl"

#: Composition fixed by AI-SPEC §5.4 — a set that drifts to all-factual stops measuring the
#: behaviours (abstention, lexical-vs-semantic) that actually distinguish retrievers.
_EXPECTED_COMPOSITION = {"factual": 6, "negative": 4, "distractor": 3, "multi_doc": 2}


def _load():
    from agmind.eval.cases import load_cases

    return load_cases(_GOLDEN)


def _corpus_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "docs/*.md", "docs/**/*.md"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_golden_set_loads() -> None:
    cases = _load()
    assert len(cases) == 15, f"expected 15 cases, got {len(cases)}"


def test_composition_matches_the_spec() -> None:
    from agmind.eval.cases import iter_class_counts

    assert dict(iter_class_counts(_load())) == _EXPECTED_COMPOSITION


def test_case_ids_are_unique_and_stable() -> None:
    cases = _load()
    ids = [c.case_id for c in cases]
    assert len(set(ids)) == len(ids)
    assert all(c.case_id.strip() == c.case_id and " " not in c.case_id for c in cases)


def test_every_anchor_exists_verbatim_in_its_document() -> None:
    """THE gate. Uses the same normalisation the scorer uses, so a case cannot pass here and
    fail at scoring time (or vice versa) because of a whitespace or case difference."""
    from agmind.eval.anchors import contains_anchor

    missing: list[str] = []
    for case in _load():
        for anchor in case.anchors:
            doc = _REPO_ROOT / anchor.doc_key
            if not doc.is_file():
                missing.append(f"{case.case_id}: doc_key {anchor.doc_key} does not exist")
                continue
            if not contains_anchor(doc.read_text(encoding="utf-8"), anchor.text):
                missing.append(
                    f"{case.case_id}: anchor {anchor.text!r} NOT found in {anchor.doc_key}"
                )

    assert not missing, "hallucinated or stale anchors:\n" + "\n".join(f"  - {m}" for m in missing)


def test_anchor_documents_are_inside_the_tracked_corpus() -> None:
    """An anchor pointing at an untracked file (or outside docs/) would be unreachable by the
    retriever, so the case could never pass no matter how good retrieval is."""
    corpus = set(_corpus_files())
    assert corpus, "corpus discovery returned nothing — the glob is broken"

    outside = {
        f"{c.case_id}:{a.doc_key}" for c in _load() for a in c.anchors if a.doc_key not in corpus
    }
    assert not outside, f"anchors reference documents outside the tracked corpus: {sorted(outside)}"


def test_negative_cases_carry_no_anchors_and_are_unanswerable() -> None:
    """The abstention class: anchors here would be a contradiction in terms."""
    for case in _load():
        if case.case_class == "negative":
            assert case.answerable is False, f"{case.case_id}: negative case marked answerable"
            assert case.anchors == (), f"{case.case_id}: negative case carries anchors"


def test_answerable_cases_have_at_least_one_anchor() -> None:
    for case in _load():
        if case.answerable:
            assert case.anchors, f"{case.case_id}: answerable case without anchors is unscoreable"


def test_multi_doc_cases_really_span_two_documents() -> None:
    """Otherwise the class name lies and the set measures top-1, not recall."""
    for case in _load():
        if case.case_class == "multi_doc":
            assert len(case.doc_keys) >= 2, (
                f"{case.case_id}: multi_doc case touches only {case.doc_keys}"
            )


def test_distractor_cases_name_their_misleading_document() -> None:
    """A distractor whose trap is not recorded cannot be reviewed or reproduced."""
    for case in _load():
        if case.case_class == "distractor":
            assert "misleading_doc=" in case.notes, (
                f"{case.case_id}: distractor must record misleading_doc in notes"
            )


def test_every_case_explains_why_it_exists() -> None:
    """Provenance is not paperwork: a case nobody can explain is a case nobody can maintain."""
    for case in _load():
        why = case.provenance.why
        assert len(why) > 30, f"{case.case_id}: provenance.why too thin: {why!r}"
        assert case.provenance.authored_from in {"corpus", "span"}
        assert case.provenance.origin in {"seed", "promoted"}


def test_corpus_ref_is_a_real_commit() -> None:
    """The set is pinned to a corpus snapshot; a bogus ref makes 'verbatim' unverifiable."""
    refs = {c.corpus_ref for c in _load()}
    assert len(refs) == 1, f"cases pin different corpus refs: {refs}"
    ref = refs.pop()
    proof = subprocess.run(
        ["git", "cat-file", "-t", ref], cwd=_REPO_ROOT, capture_output=True, text=True
    )
    assert proof.returncode == 0 and proof.stdout.strip() == "commit", (
        f"corpus_ref {ref} is not a commit in this repository"
    )


def test_questions_do_not_simply_quote_their_anchor() -> None:
    """Anti-circularity, mechanised: if the question already contains the anchor string, the case
    measures string matching rather than retrieval and is worthless."""
    from agmind.eval.anchors import normalize

    leaked = [
        f"{c.case_id}: question contains anchor {a.text!r}"
        for c in _load()
        for a in c.anchors
        if normalize(a.text) in normalize(c.question)
    ]
    assert not leaked, "questions leak their own answer:\n" + "\n".join(f"  - {x}" for x in leaked)


def test_language_mix_is_bilingual() -> None:
    """The stack is operated in Russian; an English-only set would not measure the real workload."""
    langs = [c.question_lang for c in _load()]
    ru = langs.count("ru")
    assert ru >= 5, f"expected a meaningful Russian share, got {ru}/{len(langs)}"
    assert len(set(langs)) >= 2, "set must contain more than one question language"
