"""Golden-set schema and JSONL loader (AI-SPEC §5.2).

A case is keyed on **content identity**, never on backend ids. RAGFlow's own evaluation schema
keys on ``relevant_chunk_ids``; copying that would bind human judgement to UUIDs that a reindex
destroys. Here the ground truth is a question plus verbatim anchors from a frozen corpus — chunker-
agnostic, backend-agnostic, human-checkable, and machine-checkable against the corpus.

The frozen dataclass below IS the schema. There is deliberately no separate ``schema.json``: a
second declaration of the same truth drifts from the first, and the integrity test already proves
conformance far more strongly than a JSON Schema could (it checks the anchors actually exist).

``reference_answer`` is unused in the MVP (there is no judge yet) and exists so that adding the
judge layer does not require a schema migration of hand-authored data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = 1

#: Case classes carry different evidentiary weight; the report breaks metrics down by class so a
#: set that is strong on ``factual`` and blind on ``negative`` cannot hide behind one average.
CASE_CLASSES = frozenset({"factual", "multi_doc", "cross_lingual", "negative", "distractor"})

#: Only one matching mode exists today. The field is explicit so a future exact/regex mode is a
#: data change rather than a silent reinterpretation of every existing anchor.
MATCH_MODES = frozenset({"normalized_substring"})


class EvalCaseError(ValueError):
    """Raised on a malformed or internally inconsistent case (managed, never a traceback)."""


@dataclass(frozen=True)
class Anchor:
    """A verbatim string that a correct retrieval must surface, and the doc it lives in."""

    text: str
    doc_key: str
    match: str = "normalized_substring"
    required: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "doc_key": self.doc_key,
            "match": self.match,
            "required": self.required,
        }


@dataclass(frozen=True)
class CaseProvenance:
    """Where a case came from — the reviewer's whole point is that cases come from real failures.

    ``origin='seed'`` cases are authored against the corpus; ``origin='promoted'`` cases come from
    an observed retrieval failure. ``why`` is mandatory prose: a case nobody can explain is a case
    nobody can maintain.
    """

    origin: str
    authored_from: str
    author: str
    created_at: str
    why: str
    span_id: str | None = None
    observed_at: str | None = None
    failure_class: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "authored_from": self.authored_from,
            "author": self.author,
            "created_at": self.created_at,
            "why": self.why,
            "span_id": self.span_id,
            "observed_at": self.observed_at,
            "failure_class": self.failure_class,
        }


@dataclass(frozen=True)
class EvalCase:
    """One evaluation case."""

    case_id: str
    question: str
    question_lang: str
    answerable: bool
    case_class: str
    anchors: tuple[Anchor, ...]
    provenance: CaseProvenance
    corpus_ref: str
    reference_answer: str | None = None
    notes: str = ""
    schema_version: int = SCHEMA_VERSION

    @property
    def anchor_texts(self) -> tuple[str, ...]:
        return tuple(a.text for a in self.anchors)

    @property
    def doc_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(a.doc_key for a in self.anchors))

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "schema_version": self.schema_version,
            "question": self.question,
            "question_lang": self.question_lang,
            "answerable": self.answerable,
            "case_class": self.case_class,
            "anchors": [a.to_dict() for a in self.anchors],
            "reference_answer": self.reference_answer,
            "provenance": self.provenance.to_dict(),
            "corpus_ref": self.corpus_ref,
            "notes": self.notes,
        }


def _require(payload: dict[str, object], key: str, where: str) -> object:
    if key not in payload:
        raise EvalCaseError(f"{where}: missing required field {key!r}")
    return payload[key]


def case_from_dict(payload: dict[str, object], *, where: str = "<case>") -> EvalCase:
    """Build a case from a decoded JSON object, validating the invariants that matter."""
    case_id = str(_require(payload, "case_id", where))
    where = f"{where}[{case_id}]"

    version = int(str(payload.get("schema_version", SCHEMA_VERSION)))
    if version != SCHEMA_VERSION:
        raise EvalCaseError(f"{where}: schema_version {version} != supported {SCHEMA_VERSION}")

    case_class = str(_require(payload, "case_class", where))
    if case_class not in CASE_CLASSES:
        raise EvalCaseError(
            f"{where}: unknown case_class {case_class!r} (want {sorted(CASE_CLASSES)})"
        )

    answerable = bool(_require(payload, "answerable", where))

    raw_anchors = payload.get("anchors") or []
    if not isinstance(raw_anchors, list):
        raise EvalCaseError(f"{where}: anchors must be a list")
    anchors: list[Anchor] = []
    for index, raw in enumerate(raw_anchors):
        if not isinstance(raw, dict):
            raise EvalCaseError(f"{where}: anchors[{index}] must be an object")
        text = str(_require(raw, "text", f"{where}.anchors[{index}]")).strip()
        if not text:
            raise EvalCaseError(f"{where}: anchors[{index}].text is empty")
        mode = str(raw.get("match", "normalized_substring"))
        if mode not in MATCH_MODES:
            raise EvalCaseError(f"{where}: anchors[{index}].match {mode!r} unsupported")
        anchors.append(
            Anchor(
                text=text,
                doc_key=str(_require(raw, "doc_key", f"{where}.anchors[{index}]")),
                match=mode,
                required=bool(raw.get("required", True)),
            )
        )

    # The load-bearing invariant: an answerable case with no anchors is unscoreable, and a
    # negative case WITH anchors contradicts its own class. Both are authoring mistakes that
    # would otherwise surface as a silently skipped case or a nonsense expectation.
    if answerable and not anchors:
        raise EvalCaseError(f"{where}: answerable case must carry at least one anchor")
    if not answerable and anchors:
        raise EvalCaseError(f"{where}: unanswerable case must not carry anchors")

    raw_prov = payload.get("provenance")
    if not isinstance(raw_prov, dict):
        raise EvalCaseError(f"{where}: provenance object is required")
    provenance = CaseProvenance(
        origin=str(_require(raw_prov, "origin", f"{where}.provenance")),
        authored_from=str(_require(raw_prov, "authored_from", f"{where}.provenance")),
        author=str(_require(raw_prov, "author", f"{where}.provenance")),
        created_at=str(_require(raw_prov, "created_at", f"{where}.provenance")),
        why=str(_require(raw_prov, "why", f"{where}.provenance")),
        span_id=(str(raw_prov["span_id"]) if raw_prov.get("span_id") else None),
        observed_at=(str(raw_prov["observed_at"]) if raw_prov.get("observed_at") else None),
        failure_class=(str(raw_prov["failure_class"]) if raw_prov.get("failure_class") else None),
    )

    return EvalCase(
        case_id=case_id,
        question=str(_require(payload, "question", where)),
        question_lang=str(payload.get("question_lang", "en")),
        answerable=answerable,
        case_class=case_class,
        anchors=tuple(anchors),
        provenance=provenance,
        corpus_ref=str(_require(payload, "corpus_ref", where)),
        reference_answer=(
            str(payload["reference_answer"]) if payload.get("reference_answer") else None
        ),
        notes=str(payload.get("notes", "")),
        schema_version=version,
    )


def load_cases(path: Path) -> tuple[EvalCase, ...]:
    """Load a golden-set JSONL file, rejecting duplicate ``case_id``s.

    Blank lines and ``#`` comment lines are skipped so the file stays hand-editable.
    """
    cases: list[EvalCase] = []
    seen: set[str] = set()

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalCaseError(f"{path}:{lineno}: invalid JSON ({exc})") from exc
        if not isinstance(payload, dict):
            raise EvalCaseError(f"{path}:{lineno}: each line must be a JSON object")

        case = case_from_dict(payload, where=f"{path.name}:{lineno}")
        if case.case_id in seen:
            raise EvalCaseError(f"{path}:{lineno}: duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
        cases.append(case)

    if not cases:
        raise EvalCaseError(f"{path}: no cases found — an empty golden set proves nothing")
    return tuple(cases)


def dump_cases(cases: Iterable[EvalCase]) -> str:
    """Serialise cases back to JSONL (stable key order, one object per line)."""
    return "".join(
        json.dumps(c.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for c in cases
    )


def iter_class_counts(cases: Iterable[EvalCase]) -> Iterator[tuple[str, int]]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.case_class] = counts.get(case.case_class, 0) + 1
    yield from sorted(counts.items())


def golden_set_path() -> Path:
    """Repo-versioned golden set (package data, resolves in both editable and wheel layouts)."""
    from agmind.core.paths import data_root

    return data_root() / "templates" / "eval" / "rag" / "golden.jsonl"


__all__ = [
    "CASE_CLASSES",
    "MATCH_MODES",
    "SCHEMA_VERSION",
    "Anchor",
    "CaseProvenance",
    "EvalCase",
    "EvalCaseError",
    "case_from_dict",
    "dump_cases",
    "golden_set_path",
    "iter_class_counts",
    "load_cases",
]
