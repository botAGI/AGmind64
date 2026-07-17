"""Tests for `agmind.deploy.state` — deploy-state.json паспорт установки (D-01, Phase 13.B)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agmind.deploy.state import DeployState

pytestmark = pytest.mark.backend_any


def _make_state(**overrides: object) -> DeployState:
    fields: dict[str, object] = dict(
        agmind_version="1.2.3",
        profiles=["core", "rag"],
        requested_services=["qdrant"],
        resolved_services=["postgres", "qdrant"],
        domain="lab.example.com",
        edge_mode="lan",
        written_at=datetime.now(UTC).isoformat(),
    )
    fields.update(overrides)
    return DeployState(**fields)


def test_round_trips_through_json() -> None:
    state = _make_state()
    restored = DeployState.model_validate_json(state.model_dump_json())
    assert restored == state


def test_forward_compat_ignores_unknown_keys() -> None:
    state = _make_state()
    data = state.model_dump()
    data["future_field"] = 1
    restored = DeployState.model_validate(data)
    assert not hasattr(restored, "future_field")
    assert restored == state


def test_edge_mode_rejects_invalid_value() -> None:
    data = _make_state().model_dump()
    data["edge_mode"] = "bogus"
    with pytest.raises(ValidationError):
        DeployState.model_validate(data)


def test_new_stamps_utc_iso_written_at() -> None:
    state = DeployState.new(
        agmind_version="1.2.3",
        profiles=["core"],
        requested_services=["qdrant"],
        resolved_services=["qdrant"],
        domain=None,
        edge_mode="local",
    )
    assert "+00:00" in state.written_at or state.written_at.endswith("Z")


def test_domain_none_and_config_hash_default() -> None:
    state = DeployState(
        agmind_version="1.2.3",
        profiles=[],
        requested_services=[],
        resolved_services=[],
        domain=None,
        edge_mode="local",
        written_at=datetime.now(UTC).isoformat(),
    )
    assert state.domain is None
    assert state.config_hash == ""
