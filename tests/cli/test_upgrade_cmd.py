"""Phase M3.R: tests for `agmind upgrade` lifecycle."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import yaml

from agmind.cli import upgrade_cmd
from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create temp template/services + holds + state dirs, patch paths."""
    services = tmp_path / "templates" / "services"
    services.mkdir(parents=True)
    state_dir = tmp_path / "state"

    monkeypatch.setattr(upgrade_cmd, "SERVICES_DIR", services)
    monkeypatch.setattr(upgrade_cmd, "UPGRADE_STATE_DIR", state_dir)
    monkeypatch.setattr(upgrade_cmd, "HOLDS_FILE", tmp_path / "holds.yaml")
    return tmp_path


def _make_descriptor(
    services_dir: Path, name: str, image: str, tag: str, digest: str | None = None
) -> Path:
    yaml_path = services_dir / f"{name}.yaml"
    lines = [
        f"name: {name}",
        f"image: {image}:{tag}" + (f"@sha256:{digest}" if digest else ""),
        "tier: storage",
        "purpose: test",
        "profiles:",
        "- test",
    ]
    if digest:
        lines.insert(2, f"digest: {digest}")
    yaml_path.write_text("\n".join(lines) + "\n")
    return yaml_path


def _make_separate_form_descriptor(
    services_dir: Path, name: str, image: str, tag: str, digest: str
) -> Path:
    """Write a descriptor in the canonical separate-field form (like weaviate.yaml).

    The `image:` line carries NO inline `@sha256:`; the digest lives on its own
    bare-hex `digest:` line. This is the form used by 34/40 real catalog
    descriptors (0 use inline today).
    """
    yaml_path = services_dir / f"{name}.yaml"
    lines = [
        f"name: {name}",
        f"image: {image}:{tag}",
        f"digest: {digest}",
        "tier: storage",
        "purpose: test",
        "profiles:",
        "- test",
    ]
    yaml_path.write_text("\n".join(lines) + "\n")
    return yaml_path


# ---------- _read_current_pin ----------


def test_read_current_pin_no_digest(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "alpha", "vendor/alpha", "1.2.3")
    result = upgrade_cmd._read_current_pin(p)
    assert result == ("vendor/alpha", "1.2.3", None)


def test_read_current_pin_with_digest(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "beta", "vendor/beta", "v0.1", digest="abc123" + "0" * 58)
    result = upgrade_cmd._read_current_pin(p)
    assert result == ("vendor/beta", "v0.1", "abc123" + "0" * 58)


# ---------- _bump_pin_in_yaml ----------


def test_bump_pin_replaces_image_tag(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0.0")
    old, new = upgrade_cmd._bump_pin_in_yaml(p, "2.0.0")
    assert old == "1.0.0"
    assert new == "2.0.0"
    assert "image: vendor/x:2.0.0" in p.read_text()


def test_bump_pin_updates_digest(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    # Separate-field input (matches the real catalog; no inline @sha256:).
    p = _make_separate_form_descriptor(services, "x", "vendor/x", "1.0", digest="a" * 64)
    new_digest = "b" * 64
    upgrade_cmd._bump_pin_in_yaml(p, "2.0", new_digest=new_digest)
    text = p.read_text()
    # Single digest source: image line stays bare, separate digest line is bumped.
    assert "image: vendor/x:2.0" in text
    assert "@sha256:" not in text
    assert f"digest: {new_digest}" in text


def test_bump_pin_separate_form_roundtrips_through_schema(tmp_repo: Path) -> None:
    """Regression (F.1): a digest-bumped separate-form descriptor must load.

    On the pre-fix `_bump_pin_in_yaml` this FAILS with a pydantic
    ValidationError ("duplicate digest"), because the buggy bump writes BOTH an
    inline `image: ...@sha256:<d>` AND a separate `digest:` line, which
    `_check_single_digest_source` rejects.
    """
    services = tmp_repo / "templates" / "services"
    # Service name must satisfy the schema's name regex for the round-trip.
    p = _make_separate_form_descriptor(services, "xsvc", "vendor/x", "1.0", digest="a" * 64)
    new_digest = "b" * 64

    upgrade_cmd._bump_pin_in_yaml(p, "2.0", new_digest=new_digest)

    text = p.read_text()
    # The image line must NOT carry an inline digest.
    assert "image: vendor/x:2.0" in text
    assert "@sha256:" not in text
    # The separate digest line must carry the new digest.
    assert f"digest: {new_digest}" in text

    # The bumped descriptor must round-trip through the schema validator.
    descriptor = ServiceDescriptor.model_validate(yaml.safe_load(text))
    assert descriptor.image == "vendor/x:2.0"
    assert descriptor.digest == new_digest


def test_bump_pin_adds_digest_if_absent(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0")  # no digest
    new_digest = "c" * 64
    upgrade_cmd._bump_pin_in_yaml(p, "2.0", new_digest=new_digest)
    text = p.read_text()
    assert f"digest: {new_digest}" in text


def test_bump_pin_preserves_descriptor_on_write_failure(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0")
    original = p.read_text(encoding="utf-8")
    p.chmod(0o600)
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self == p or self.name == f".{p.name}.tmp":
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        upgrade_cmd._bump_pin_in_yaml(p, "2.0")

    assert p.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert not p.with_name(f".{p.name}.tmp").exists()


# ---------- cmd_component ----------


def test_component_bumps_tag(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    rc = upgrade_cmd.cmd_component("alpha", "1.0.1")
    assert rc == 0
    out = capsys.readouterr().out
    assert "1.0.0 → vendor/alpha:1.0.1" in out
    yaml = (services / "alpha.yaml").read_text()
    assert "vendor/alpha:1.0.1" in yaml


def test_component_unknown_service(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = upgrade_cmd.cmd_component("ghost", "1.0.0")
    assert rc == 1


def test_component_noop_if_already_at_target(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    rc = upgrade_cmd.cmd_component("alpha", "1.0.0")
    assert rc == 0
    assert "already at" in capsys.readouterr().out.lower()


def test_component_respects_holds_without_force(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.HOLDS_FILE.write_text(
        "vendor/alpha:\n  reason: 'pinned for compat'\n",
    )
    rc = upgrade_cmd.cmd_component("alpha", "1.0.1")
    assert rc == 1
    err = capsys.readouterr().err
    assert "HELD" in err
    assert "compat" in err
    # Tag unchanged
    yaml = (services / "alpha.yaml").read_text()
    assert "vendor/alpha:1.0.0" in yaml


def test_component_force_bypasses_holds(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.HOLDS_FILE.write_text(
        "vendor/alpha:\n  reason: 'pinned'\n",
    )
    rc = upgrade_cmd.cmd_component("alpha", "1.0.1", force=True)
    assert rc == 0
    yaml = (services / "alpha.yaml").read_text()
    assert "vendor/alpha:1.0.1" in yaml


def test_component_saves_upgrade_state(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.cmd_component("alpha", "1.0.1")

    state_files = list(upgrade_cmd.UPGRADE_STATE_DIR.glob("*.json"))
    assert len(state_files) == 1
    state = json.loads(state_files[0].read_text())
    assert state["service"] == "alpha"
    assert state["old_tag"] == "1.0.0"
    assert state["new_tag"] == "1.0.1"


def test_save_upgrade_state_removes_partial_file_on_write_failure(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self.parent == upgrade_cmd.UPGRADE_STATE_DIR:
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        upgrade_cmd._save_upgrade_state(
            "alpha",
            tmp_repo / "templates" / "services" / "alpha.yaml",
            "1.0.0",
            "1.0.1",
            None,
        )

    assert list(upgrade_cmd.UPGRADE_STATE_DIR.glob("*.json")) == []
    assert list(upgrade_cmd.UPGRADE_STATE_DIR.glob(".*.tmp")) == []


def _make_component_contract(
    components_dir: Path,
    component_id: str,
    service_names: list[str],
    policy: str = "strict-pin",
) -> Path:
    components_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        f"id: {component_id}",
        "kind: app",
        "core:",
        f"  upstream: example/{component_id}",
        "  recommended_version: '1.14.2'",
        f"  update_policy: {policy}",
        "runtime:",
        "  service_descriptors:",
    ]
    lines.extend(f"    - {service_name}" for service_name in service_names)
    path = components_dir / f"{component_id}.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_component_plan_for_grouped_stack_lists_all_descriptors(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "dify-api", "langgenius/dify-api", "1.14.2")
    _make_descriptor(services, "dify-web", "langgenius/dify-web", "1.14.2")
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-web"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    plan = upgrade_cmd.build_component_upgrade_plan("dify", "1.14.3")

    assert [item.service for item in plan.items] == ["dify-api", "dify-web"]
    assert all(item.new_tag == "1.14.3" for item in plan.items)
    assert plan.policy == "strict-pin"


def test_component_apply_saves_grouped_state(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "dify-api", "langgenius/dify-api", "1.14.2")
    _make_descriptor(services, "dify-web", "langgenius/dify-web", "1.14.2")
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-web"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    rc = upgrade_cmd.cmd_component("dify", "1.14.3", plan_only=False)

    assert rc == 0
    assert "1.14.3" in (services / "dify-api.yaml").read_text()
    assert "1.14.3" in (services / "dify-web.yaml").read_text()
    state_files = list(upgrade_cmd.UPGRADE_STATE_DIR.glob("*.json"))
    state = json.loads(state_files[0].read_text())
    assert state["component"] == "dify"
    assert len(state["items"]) == 2


def test_save_grouped_upgrade_state_removes_partial_file_on_write_failure(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self.parent == upgrade_cmd.UPGRADE_STATE_DIR:
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    plan = upgrade_cmd.UpgradePlan(
        component="dify",
        policy="strict-pin",
        is_component=True,
        items=(
            upgrade_cmd.UpgradePlanItem(
                service="dify-api",
                yaml_path=str(tmp_repo / "templates" / "services" / "dify-api.yaml"),
                image="langgenius/dify-api",
                old_tag="1.14.2",
                new_tag="1.14.3",
                old_digest=None,
            ),
        ),
    )
    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        upgrade_cmd._save_upgrade_plan_state(plan)

    assert list(upgrade_cmd.UPGRADE_STATE_DIR.glob("*.json")) == []
    assert list(upgrade_cmd.UPGRADE_STATE_DIR.glob(".*.tmp")) == []


def test_component_plan_only_does_not_edit_files(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "dify-api", "langgenius/dify-api", "1.14.2")
    _make_descriptor(services, "dify-web", "langgenius/dify-web", "1.14.2")
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-web"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    rc = upgrade_cmd.cmd_component("dify", "1.14.3", plan_only=True)

    assert rc == 0
    assert "1.14.2" in (services / "dify-api.yaml").read_text()
    assert "1.14.2" in (services / "dify-web.yaml").read_text()
    assert not upgrade_cmd.UPGRADE_STATE_DIR.exists()


def test_grouped_rollback_restores_all_files(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "dify-api", "langgenius/dify-api", "1.14.2")
    _make_descriptor(services, "dify-web", "langgenius/dify-web", "1.14.2")
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-web"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    upgrade_cmd.cmd_component("dify", "1.14.3", plan_only=False)
    rc = upgrade_cmd.cmd_rollback()

    assert rc == 0
    assert "1.14.2" in (services / "dify-api.yaml").read_text()
    assert "1.14.2" in (services / "dify-web.yaml").read_text()


# ---------- cmd_apply ----------


def test_apply_redeploys_supported_compose_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.deploy.runner import DeployResult

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)

    rc = upgrade_cmd.cmd_apply(install_dir=tmp_path, healthcheck_timeout=7)

    assert rc == 0
    assert calls["profiles"] == ["core", "rag", "observability"]
    assert calls["install_dir"] == tmp_path
    assert calls["healthcheck_timeout"] == 7


# ---------- cmd_rollback ----------


def test_rollback_restores_old_tag(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.cmd_component("alpha", "2.0.0")
    # Verify bumped
    assert "vendor/alpha:2.0.0" in (services / "alpha.yaml").read_text()

    rc = upgrade_cmd.cmd_rollback()
    assert rc == 0
    # Restored к 1.0.0
    assert "vendor/alpha:1.0.0" in (services / "alpha.yaml").read_text()


def test_rollback_no_state_returns_error(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = upgrade_cmd.cmd_rollback()
    assert rc == 1


def test_rollback_archives_state_file(tmp_repo: Path) -> None:
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")
    upgrade_cmd.cmd_component("alpha", "2.0.0")
    upgrade_cmd.cmd_rollback()
    # Original state file moved to rolled_back/
    archived = list((upgrade_cmd.UPGRADE_STATE_DIR / "rolled_back").glob("*.json"))
    assert len(archived) == 1


# ---------- cmd_check ----------


def test_check_invokes_version_check_script(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cmd_check вызывает scripts/checks/version_check.py через subprocess."""
    called: list[list[str]] = []

    class P:
        returncode = 0

    def fake_run(cmd, check):  # noqa: ARG001
        called.append(cmd)
        return P()

    monkeypatch.setattr("subprocess.run", fake_run)
    # Point script at real one (existing in repo)
    monkeypatch.setattr(upgrade_cmd, "REPO_ROOT", Path(__file__).resolve().parents[2])

    rc = upgrade_cmd.cmd_check()
    assert rc == 0
    assert any("version_check.py" in s for s in called[0])
