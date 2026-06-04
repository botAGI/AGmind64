"""Tests for agmind.cli.install_state.load_setup_state_from_file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agmind.cli.install_state import (
    StateResolveError,
    load_prior_setup_state,
    load_setup_state_from_file,
)

pytestmark = pytest.mark.backend_any


def _write_state(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------- load_prior_setup_state (interactive re-run pre-selection) ----------


def test_prior_state_missing_returns_none(tmp_path: Path) -> None:
    assert load_prior_setup_state(tmp_path / "absent.json") is None


def test_prior_state_corrupt_returns_none_not_raises(tmp_path: Path) -> None:
    bad = tmp_path / "setup-state.json"
    bad.write_text("{broken json", encoding="utf-8")
    # Best-effort: a corrupt prior state must NOT crash a fresh install — fall to defaults.
    assert load_prior_setup_state(bad) is None


def test_prior_state_corrupt_warns_but_absent_is_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review MEDIUM install-state-corrupt-orphan-removal: a PRESENT-but-corrupt state must warn
    (the fallback can --remove-orphans the running stack), while a truly absent file stays
    silent (a normal fresh install)."""
    assert load_prior_setup_state(tmp_path / "absent.json") is None
    assert capsys.readouterr().err == "", "absent state must not warn"

    bad = tmp_path / "setup-state.json"
    bad.write_text("{broken json", encoding="utf-8")
    assert load_prior_setup_state(bad) is None
    err = capsys.readouterr().err
    assert "unreadable" in err and "remove-orphans" in err


def test_prior_state_loads_previously_selected_services(tmp_path: Path) -> None:
    """Re-run must default to the previously-deployed selection so an incremental
    re-run adds/replaces a component instead of dropping the running stack."""
    state = _write_state(
        tmp_path / "setup-state.json",
        {"domain": "lab.example.com", "services": ["traefik", "postgres", "grafana"]},
    )
    prior = load_prior_setup_state(state)
    assert prior is not None
    assert prior.services == ["traefik", "postgres", "grafana"]
    assert prior.domain == "lab.example.com"


def test_missing_file_raises_read_error(tmp_path: Path) -> None:
    with pytest.raises(StateResolveError, match="cannot read --from-state"):
        load_setup_state_from_file(tmp_path / "nope.json")


def test_unparseable_file_raises_load_error(tmp_path: Path) -> None:
    bad = tmp_path / "state.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateResolveError, match="cannot load --from-state"):
        load_setup_state_from_file(bad)


def test_unknown_service_raises(tmp_path: Path) -> None:
    state = _write_state(
        tmp_path / "s.json", {"domain": "lab.example.com", "services": ["traefik", "nope-svc"]}
    )
    with pytest.raises(
        StateResolveError, match="unknown selected services in --from-state: nope-svc"
    ):
        load_setup_state_from_file(state)


def test_unknown_profile_raises(tmp_path: Path) -> None:
    state = _write_state(
        tmp_path / "s.json",
        {"domain": "lab.example.com", "services": [], "profiles": ["does-not-exist"]},
    )
    with pytest.raises(StateResolveError, match="unknown selected profiles in --from-state"):
        load_setup_state_from_file(state)


def test_empty_selection_raises(tmp_path: Path) -> None:
    state = _write_state(
        tmp_path / "s.json", {"domain": "lab.example.com", "services": [], "profiles": []}
    )
    with pytest.raises(StateResolveError, match="no selected services in --from-state"):
        load_setup_state_from_file(state)


def test_profiles_expand_to_services(tmp_path: Path) -> None:
    state = _write_state(
        tmp_path / "s.json", {"domain": "lab.example.com", "services": [], "profiles": ["core"]}
    )
    resolved = load_setup_state_from_file(state)
    assert resolved.services, "core profile should expand to a non-empty service set"
    assert resolved.profiles == []
