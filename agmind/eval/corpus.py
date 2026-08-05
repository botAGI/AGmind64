"""Evaluation corpus: export and freeze (AI-SPEC §5.1, §5.3).

The corpus is the repository's own **git-tracked** markdown under ``docs/``. Three properties
make it the right choice for a first evaluation set: it is on-box (zero-egress — no HuggingFace
download, which is both a policy violation and the reason RAGFlow's own ``rag/benchmark.py`` is
unusable here), it is legally ours, and an operator can read a chunk and judge for themselves
whether the retrieval was sensible.

Two things are deliberately EXCLUDED and the exclusion is enforced by using ``git ls-files``
rather than a filesystem glob:

* ``CLAUDE.md``, ``AGENTS.md`` and ``.planning/`` are gitignored — operationally sensitive and
  not shipped. A filesystem walk would silently sweep them in on a working tree that has them,
  and produce a different corpus on a clean checkout. That divergence would be invisible and
  would make two runs incomparable while both claimed the same ``corpus_ref``.
* ``README.md`` is not package data (``package-dir`` maps only ``templates/``, ``ansible/``,
  ``scripts/``, ``docs/``), so it would not exist in a wheel install.

The manifest freezes the corpus by git ref plus a per-file sha256, so a run can prove which bytes
it measured. A metric computed over a different corpus is not a regression, it is a different
question — the gate refuses to compare across manifests rather than reporting a scary delta.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: Path globs handed to ``git ls-files``. Kept narrow and explicit.
CORPUS_GLOBS: tuple[str, ...] = ("docs/*.md", "docs/**/*.md")

#: Path prefixes excluded from the corpus AFTER the glob.
#:
#: ``docs/eval/`` documents this very subsystem and quotes golden-set anchors verbatim as
#: examples. Left in, the evaluation's own documentation is scored as maximally-relevant ground
#: truth: a retriever is rewarded for surfacing the page that merely *mentions*
#: ``mem_info_gtt_total`` as an illustration, rather than the page that answers the question.
#: Self-referential ground truth is the quietest way for a benchmark to measure itself.
CORPUS_EXCLUDE_PREFIXES: tuple[str, ...] = ("docs/eval/",)


class CorpusError(RuntimeError):
    """Raised when the corpus cannot be exported or verified (managed, never a traceback)."""


@dataclass(frozen=True)
class CorpusDoc:
    """One corpus document, frozen by content hash."""

    doc_key: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, object]:
        return {"doc_key": self.doc_key, "sha256": self.sha256, "bytes": self.bytes}


@dataclass(frozen=True)
class CorpusManifest:
    """The exact bytes an evaluation run measured."""

    corpus_ref: str
    docs: tuple[CorpusDoc, ...]

    @property
    def total_bytes(self) -> int:
        return sum(d.bytes for d in self.docs)

    @property
    def doc_keys(self) -> tuple[str, ...]:
        return tuple(d.doc_key for d in self.docs)

    def fingerprint(self) -> str:
        """Single hash over every (doc_key, sha) — the run-identity key.

        Two runs are comparable only when their fingerprints match; that check is what stops a
        quietly re-indexed or extended corpus from being reported as a retrieval regression.

        ``corpus_ref`` is deliberately NOT hashed. It is provenance — which commit was checked
        out — and it is recorded separately in the manifest and in every report's scope block.
        Hashing it made the fingerprint track the commit instead of the corpus, which defeated
        ``gate._comparable_key``'s own deliberate exclusion of ``corpus_ref`` by carrying the
        commit back in through the fingerprint field. The consequence was not subtle: after any
        commit at all — including one touching only ``docs/eval/``, a directory excluded from the
        corpus — the gate answered REFUSING TO COMPARE with every other key component identical.
        A regression gate that refuses after every commit cannot catch a regression.

        Fields are NUL-separated so no rearrangement of key and hash text can produce the same
        byte stream from a different corpus.
        """
        digest = hashlib.sha256()
        for doc in self.docs:
            digest.update(doc.doc_key.encode())
            digest.update(b"\0")
            digest.update(doc.sha256.encode())
            digest.update(b"\0")
        return digest.hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "corpus_ref": self.corpus_ref,
            "fingerprint": self.fingerprint(),
            "doc_count": len(self.docs),
            "total_bytes": self.total_bytes,
            "docs": [d.to_dict() for d in self.docs],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> CorpusManifest:
        raw_docs = payload.get("docs")
        if not isinstance(raw_docs, list):
            raise CorpusError("manifest: 'docs' must be a list")
        docs = tuple(
            CorpusDoc(
                doc_key=str(d["doc_key"]),
                sha256=str(d["sha256"]),
                bytes=int(d["bytes"]),
            )
            for d in raw_docs
            if isinstance(d, dict)
        )
        return cls(corpus_ref=str(payload.get("corpus_ref", "")), docs=docs)


def _git(args: Sequence[str], repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise CorpusError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def tracked_corpus_files(repo_root: Path) -> tuple[str, ...]:
    """The tracked corpus file list, from git — never a filesystem glob (see module docstring)."""
    out = _git(["ls-files", *CORPUS_GLOBS], repo_root)
    files = tuple(
        sorted(
            line.strip()
            for line in out.splitlines()
            if line.strip() and not line.strip().startswith(CORPUS_EXCLUDE_PREFIXES)
        )
    )
    if not files:
        raise CorpusError(
            f"no tracked corpus files matched {CORPUS_GLOBS} under {repo_root} — "
            "either the globs are wrong or this is not the repository root"
        )
    return files


def build_manifest(repo_root: Path, *, ref: str | None = None) -> CorpusManifest:
    """Hash every tracked corpus file and pin the result to a git ref (default: HEAD)."""
    corpus_ref = (ref or _git(["rev-parse", "HEAD"], repo_root).strip()).strip()
    docs: list[CorpusDoc] = []
    for doc_key in tracked_corpus_files(repo_root):
        path = repo_root / doc_key
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise CorpusError(f"cannot read corpus file {doc_key}: {exc}") from exc
        docs.append(
            CorpusDoc(
                doc_key=doc_key,
                sha256=hashlib.sha256(payload).hexdigest(),
                bytes=len(payload),
            )
        )
    return CorpusManifest(corpus_ref=corpus_ref, docs=tuple(docs))


def verify_manifest(repo_root: Path, manifest: CorpusManifest) -> list[str]:
    """Return human-readable drift between the manifest and the working tree (empty == clean).

    Reported rather than raised: the caller decides whether drift is fatal (a gated run) or
    merely worth printing (an exploratory run).
    """
    problems: list[str] = []
    current = {d.doc_key: d for d in build_manifest(repo_root, ref=manifest.corpus_ref).docs}
    recorded = {d.doc_key: d for d in manifest.docs}

    for key in sorted(set(recorded) - set(current)):
        problems.append(f"{key}: in manifest but no longer tracked")
    for key in sorted(set(current) - set(recorded)):
        problems.append(f"{key}: tracked now but absent from the manifest")
    for key in sorted(set(current) & set(recorded)):
        if current[key].sha256 != recorded[key].sha256:
            problems.append(f"{key}: content changed since the manifest was frozen")
    return problems


def export_corpus(repo_root: Path, dest: Path) -> CorpusManifest:
    """Copy the tracked corpus into ``dest`` (flattened, collision-safe) and return the manifest.

    Flattened because RAGFlow ingests a flat file list; the original path is preserved in the
    exported filename so a retrieved chunk can always be traced back to its ``doc_key``.
    """
    manifest = build_manifest(repo_root)
    dest.mkdir(parents=True, exist_ok=True)
    for doc in manifest.docs:
        flat = doc.doc_key.replace("/", "__")
        (dest / flat).write_bytes((repo_root / doc.doc_key).read_bytes())
    (dest / "corpus-manifest.json").write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def doc_key_from_export_name(name: str) -> str:
    """Inverse of the flattening applied by :func:`export_corpus`."""
    return name.replace("__", "/")


__all__ = [
    "CORPUS_GLOBS",
    "CorpusDoc",
    "CorpusError",
    "CorpusManifest",
    "build_manifest",
    "doc_key_from_export_name",
    "export_corpus",
    "tracked_corpus_files",
    "verify_manifest",
]
