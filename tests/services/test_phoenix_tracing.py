"""Arize Phoenix — self-hosted LLM-tracing backend for Dify (2026-06-07 DIFY-TRACING-RESEARCH).

Phoenix receives Dify's native OpenInference/OTLP trace export on :6006 so the operator can debug
LLM runs (prompts/completions/tokens/latency/RAG spans). Deployed like our other internal
observability UIs: SQLite on a managed data dir, no own-auth (Authelia at the edge via
chain-internal), hardened (nonroot uid + no-new-privileges + cap_drop).
"""

from __future__ import annotations

import pytest

from agmind.services.profile_sets import all_profile_names
from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_phoenix_descriptor_shape() -> None:
    d = load_descriptors()
    assert "phoenix" in d, "phoenix descriptor must exist"
    p = d["phoenix"]
    assert p.image.startswith("arizephoenix/phoenix:")
    assert p.digest, "phoenix must pin a digest"
    assert p.profiles == ["tracing"], "phoenix lives in its own opt-in tracing profile"
    # default-only (no networks block) → shared default bridge; Dify reaches phoenix:6006 there
    assert p.networks == []
    assert any("6006" in str(port) for port in p.ports), "phoenix UI/OTLP-HTTP port 6006"
    # The Phoenix SPA streams live traces over SSE/GraphQL-subscriptions; without sse the edge proxy
    # buffers the stream and the UI loads but never populates ("dead"). live 2026-06-08.
    assert p.routing is not None and p.routing.sse is True, (
        "phoenix UI needs sse (no proxy buffering)"
    )


def test_phoenix_is_hardened_nonroot() -> None:
    p = load_descriptors()["phoenix"]
    assert p.run_as_uid == 65532, "distroless nonroot uid baked in the image"
    assert p.no_new_privileges is True
    assert "ALL" in (p.cap_drop or []), "Phoenix needs no Linux caps — drop ALL"


def test_phoenix_persists_to_managed_data_dir() -> None:
    p = load_descriptors()["phoenix"]
    assert any("/var/lib/agmind/phoenix" in v for v in p.volumes), (
        "SQLite must persist (no anon vol)"
    )
    assert "/var/lib/agmind/phoenix" in (p.writable_mounts or [])


def test_phoenix_ui_is_authelia_gated() -> None:
    p = load_descriptors()["phoenix"]
    assert p.routing is not None
    assert p.routing.middleware_chain == "chain-internal", "UI gated by Authelia at the edge"


def test_phoenix_provides_llm_tracing_and_consumes_nothing() -> None:
    p = load_descriptors()["phoenix"]
    assert "llm_tracing" in p.provides
    # SQLite + self-contained: no backend consume (so no missing-capability gate ripple)
    assert list(p.consumes) == []


def test_tracing_profile_registered() -> None:
    assert "tracing" in all_profile_names(), "new profile must be in profile_sets.ALL_PROFILE_SETS"
