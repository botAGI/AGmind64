"""Phase 18 (M11) — the RAGFlow retrieval client, the only retriever that measures the DEPLOYED
RAG rather than a reconstruction of it.

The lexical and dense retrievers chunk the corpus themselves, so they measure retrieval quality
under the harness's own assumptions. RAGFlow indexed the corpus with its own chunker, its own
hybrid scoring and its own thresholds — which is what an operator actually queries. The tests
here pin the three things that would make such a number quietly wrong: talking to an endpoint
that cannot express chunk-level ground truth, mapping a retrieved chunk to the wrong document,
and leaking the API key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.eval.clients.ragflow import (
    API_KEY_ENV,
    RagflowError,
    RagflowRetrievalClient,
    corpus_key_to_filename,
    filename_to_corpus_key,
    load_api_key,
)
from agmind.eval.endpoints import EndpointVerdict

pytestmark = pytest.mark.backend_any

_KNOWN = frozenset({"docs/QUICKSTART.md", "docs/guides/RAG.md"})


def _verdict(url: str, *, allowed: bool = True) -> EndpointVerdict:
    return EndpointVerdict(
        url=url,
        allowed=allowed,
        reason="loopback" if allowed else "public address",
        host="127.0.0.1",
        addresses=("127.0.0.1",),
    )


@pytest.mark.parametrize("doc_key", sorted(_KNOWN))
def test_filename_mapping_round_trips(doc_key: str) -> None:
    assert filename_to_corpus_key(corpus_key_to_filename(doc_key), _KNOWN) == doc_key


def test_unmappable_document_raises_instead_of_guessing() -> None:
    """A silent miss here scores every anchor in that document as absent.

    That is the same shape as the already-fixed defect where an anchor was credited in whichever
    document happened to contain the string: the run stays green and the number is about a
    different corpus than the one the report names.
    """
    with pytest.raises(RagflowError, match="not in the frozen corpus"):
        filename_to_corpus_key("docs__SOMETHING_ELSE.md", _KNOWN)


def test_dify_retrieval_endpoint_is_refused() -> None:
    """It drops chunk ids and flattens the three similarity scores, so ground truth is impossible."""
    with pytest.raises(RagflowError, match="chunk ids"):
        RagflowRetrievalClient(
            _verdict("http://127.0.0.1:9380/api/v1/dify/retrieval"), "k", dataset_ids=["d"]
        )


def test_disallowed_endpoint_is_refused() -> None:
    with pytest.raises(RagflowError, match="[Zz]ero-egress"):
        RagflowRetrievalClient(
            _verdict("http://198.51.100.7/api/v1/retrieval", allowed=False), "k", dataset_ids=["d"]
        )


def test_dataset_ids_are_required() -> None:
    """Searching "whatever is indexed" produces a number nobody can attribute to a corpus."""
    with pytest.raises(RagflowError, match="dataset id"):
        RagflowRetrievalClient(_verdict("http://127.0.0.1:9380/api/v1/retrieval"), "k", dataset_ids=[])


def test_api_key_is_read_from_a_file(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("  ragflow-key-value\n", encoding="utf-8")
    assert load_api_key(key_file=key_file) == "ragflow-key-value"


def test_missing_api_key_says_how_to_get_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installer does not provision this key, so the error has to carry the procedure."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(RagflowError) as excinfo:
        load_api_key()
    assert "--api-key-file" in str(excinfo.value)
    assert API_KEY_ENV in str(excinfo.value)


def test_empty_key_file_is_not_silently_accepted(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("\n", encoding="utf-8")
    with pytest.raises(RagflowError, match="empty"):
        load_api_key(key_file=key_file)


def test_abstention_threshold_is_ragflows_own_default() -> None:
    """0.2 comes from RAGFlow's own /api/v1/retrieval default, not from fitting this golden set."""
    from agmind.eval.runner import DEFAULT_ABSTAIN_THRESHOLD

    assert DEFAULT_ABSTAIN_THRESHOLD["ragflow"] == 0.2


def test_cli_exposes_the_ragflow_options() -> None:
    """Introspect click params, never grep --help: typer 0.26 rich-wraps option names to the
    terminal width, so a substring assertion passes locally and fails on CI."""
    import typer.main

    from agmind.cli import _make_app

    command = typer.main.get_command(_make_app())
    run_cmd = command.commands["eval"].commands["run"]  # type: ignore[attr-defined]
    options = {opt for param in run_cmd.params for opt in param.opts}
    assert {"--ragflow-url", "--ragflow-dataset", "--api-key-file"} <= options
