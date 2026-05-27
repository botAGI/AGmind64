from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


def test_retrieval_topology_summarizes_dify_milvus_and_ragflow_elasticsearch() -> None:
    from agmind.services.retrieval_policy import summarize_retrieval_topology

    lines = summarize_retrieval_topology(["dify-api", "ragflow", "milvus", "elasticsearch"])

    assert "DIFY VECTOR DB ..... milvus" in lines
    assert "RAGFLOW DOC ENGINE . elasticsearch" in lines
    assert any("Milvus applies to Dify only" in line for line in lines)


def test_retrieval_topology_reports_dify_only_when_ragflow_absent() -> None:
    from agmind.services.retrieval_policy import summarize_retrieval_topology

    lines = summarize_retrieval_topology(["dify-api", "qdrant"])

    assert "DIFY VECTOR DB ..... qdrant" in lines
    assert not any("RAGFLOW DOC ENGINE" in line for line in lines)


def test_retrieval_topology_reports_ambiguous_dify_vector_providers() -> None:
    from agmind.services.retrieval_policy import summarize_retrieval_topology

    lines = summarize_retrieval_topology(["dify-api", "milvus", "qdrant"])

    assert "DIFY VECTOR DB ..... milvus (ambiguous: qdrant also selected)" in lines
    assert any("Choose one Dify VECTOR_STORE" in line for line in lines)
