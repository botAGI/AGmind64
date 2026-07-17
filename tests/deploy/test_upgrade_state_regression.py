"""SPEC-13.6 regression: `agmind upgrade --apply` preserves the applied service
set + domain across a bump — exercising the REAL writer (D-02) and the REAL
state-driven resolution (D-03) end to end, hermetically.

Flow: seed `deploy-state.json` directly (the "install fixture" — what a real
successful apply would have written), bump ONE service's image digest on a TMP
COPY of two real, self-contained descriptors (never mutate the actual
`templates/services/` tree — Правила Карпатого), then run `cmd_apply` with NO
explicit profiles. Only the leaf docker/subprocess side-effects are
monkeypatched (the exact seam list established by
`tests/deploy/test_deploy.py::test_deploy_apply_writes_compose_via_sudo_helper`)
— the real render/select pipeline and the real `write_deploy_state` call both
run for real, so a regression in D-02 or D-03 fails this test, not a mock.
"""

from __future__ import annotations

import shutil
from functools import partial
from pathlib import Path

import pytest

from agmind.cli import upgrade_cmd
from agmind.deploy import runner
from agmind.deploy.state import DeployState, load_deploy_state, write_deploy_state
from agmind.services.renderer import load_descriptors, render_to_string

pytestmark = pytest.mark.backend_any

_REAL_SERVICES_DIR = Path(__file__).resolve().parents[2] / "templates" / "services"
_OLD_POSTGRES_DIGEST = "b02d9b5bcf608c2719da32cdabee274a33841202487fd5dc9b065b63f886753f"
_NEW_POSTGRES_DIGEST = "f" * 64


def _seed_tmp_catalog(tmp_services_dir: Path) -> None:
    """Copy two REAL, self-contained descriptors (no host ports, no cross-service
    deps) into a tmp catalog dir — proves the real render/select pipeline without
    ever touching the actual `templates/services/` tree."""
    tmp_services_dir.mkdir(parents=True, exist_ok=True)
    for name in ("postgres.yaml", "qdrant.yaml"):
        shutil.copy(_REAL_SERVICES_DIR / name, tmp_services_dir / name)


def test_upgrade_apply_preserves_resolved_set_and_domain_except_bumped_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install(profiles=[core,rag,ui,security], resolved=[postgres,qdrant]) → bump
    postgres's digest → `upgrade --apply` (state-driven, no explicit --profile) →
    the reloaded deploy-state's resolved_services + domain are byte/list-identical
    to the original; only the bumped image digest differs."""
    install_dir = tmp_path
    tmp_services_dir = tmp_path / "catalog"
    _seed_tmp_catalog(tmp_services_dir)

    # Redirect the REAL render/select pipeline at the tmp catalog copy (never the
    # actual repo templates/services/ tree) — both are bare module-level names on
    # `runner`, the established monkeypatch seam (PATTERNS.md "Module-level
    # monkeypatch seams").
    monkeypatch.setattr(
        runner, "load_descriptors", partial(load_descriptors, services_dir=tmp_services_dir)
    )
    monkeypatch.setattr(
        runner,
        "render_to_string",
        lambda **kwargs: render_to_string(services_dir=tmp_services_dir, **kwargs),
    )

    # Leaf docker/subprocess side-effects only — exactly the seam list from
    # tests/deploy/test_deploy.py::test_deploy_apply_writes_compose_via_sudo_helper
    # (lines 934-964). No real docker/subprocess call anywhere in this test.
    monkeypatch.setattr(runner, "_validate_compose_config", lambda *_a, **_k: (0, ""))
    monkeypatch.setattr(runner, "_wait_healthy", lambda *_a, **_k: (True, []))
    monkeypatch.setattr(runner, "_run_compose", lambda *_a, **_k: (0, "", ""))
    monkeypatch.setattr(runner, "_stream_compose", lambda *_a, **_k: (0, ""))

    writes: list[dict[str, object]] = []

    def fake_write_text_maybe_sudo(
        path: Path,
        text: str,
        sudo_password: str | None = None,
        mode: str = "0644",
    ) -> None:
        writes.append({"path": path, "text": text})

    monkeypatch.setattr(runner, "_write_text_maybe_sudo", fake_write_text_maybe_sudo, raising=False)

    original = DeployState.new(
        agmind_version="9.9.9",
        profiles=["core", "rag", "ui", "security"],
        requested_services=[],
        resolved_services=["postgres", "qdrant"],
        domain="lab.example.com",
        edge_mode="lan",
    )
    write_deploy_state(install_dir, original)

    # Bump ONE service's digest on the tmp copy only — real templates/services/
    # tree is never touched.
    upgrade_cmd._bump_pin_in_yaml(
        tmp_services_dir / "postgres.yaml", "17.10-alpine3.22", _NEW_POSTGRES_DIGEST
    )

    rc = upgrade_cmd.cmd_apply(install_dir=install_dir)

    assert rc == 0
    # The writer actually ran (real _write_text_maybe_sudo call captured) — the
    # assertion below is meaningful, not trivially passing on an unwritten file.
    assert writes, "the real _deploy_impl compose write must have run"
    rendered_text = str(writes[0]["text"])
    assert _NEW_POSTGRES_DIGEST in rendered_text
    assert _OLD_POSTGRES_DIGEST not in rendered_text

    reloaded = load_deploy_state(install_dir)
    assert reloaded is not None
    # The writer actually re-ran (fresh timestamp), not a stale unwritten file.
    assert reloaded.written_at != original.written_at
    assert reloaded.resolved_services == original.resolved_services
    assert reloaded.domain == original.domain
