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
    from agmind.core import files as files_mod

    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "x", "vendor/x", "1.0")
    original = p.read_text(encoding="utf-8")
    p.chmod(0o600)

    def flaky_fsync(fd: int) -> None:
        # write_text_atomic fsyncs the (fully-written) temp fd before the atomic
        # replace; failing here leaves a complete temp that the cleanup path must
        # unlink while the original descriptor stays intact.
        raise OSError("disk full")

    monkeypatch.setattr(files_mod.os, "fsync", flaky_fsync)

    with pytest.raises(OSError, match="disk full"):
        upgrade_cmd._bump_pin_in_yaml(p, "2.0")

    assert p.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    # mkstemp uses a random suffix, so assert no leftover temp by glob.
    assert not list(p.parent.glob(f".{p.name}.*.tmp"))


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


# ---------- tag-bump without a digest must REFUSE (silent old-image deploy) ----------
#
# A descriptor that carries a separate `digest:` line renders to
# `name:tag@sha256:<digest>` (ServiceDescriptor.image_ref). Bumping ONLY the tag
# leaves the OLD digest, so docker resolves BY DIGEST and silently deploys the
# OLD image under the new tag. The deep audit found this corrupts 34/40
# separate-digest descriptors. The bump entrypoint must FAIL FAST and demand the
# matching `--digest` rather than mutate the file.


def test_component_refuses_tag_bump_without_digest_separate_form(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A separate-digest descriptor bumped WITHOUT --digest must error, untouched."""
    services = tmp_repo / "templates" / "services"
    p = _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "1.0.0", digest="a" * 64)
    original = p.read_text(encoding="utf-8")

    rc = upgrade_cmd.cmd_component("alpha", "1.0.1")

    assert rc != 0
    # The descriptor on disk is byte-for-byte unchanged (no tag/digest mutation).
    assert p.read_text(encoding="utf-8") == original
    err = capsys.readouterr().err
    # The operator message must name the remedy (the imagetools command + --digest).
    assert "--digest" in err
    assert "imagetools inspect" in err
    # No upgrade state was written for the refused bump.
    assert not upgrade_cmd.UPGRADE_STATE_DIR.exists()


def test_component_refuses_tag_bump_in_plan_builder_separate_form(
    tmp_repo: Path,
) -> None:
    """build_component_upgrade_plan raises for a separate-digest tag-only bump."""
    services = tmp_repo / "templates" / "services"
    _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "1.0.0", digest="a" * 64)

    with pytest.raises(ValueError, match="--digest"):
        upgrade_cmd.build_component_upgrade_plan("alpha", "1.0.1")


def test_component_bumps_separate_form_with_digest(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """WITH both --version and --digest: image tag AND digest line both update."""
    services = tmp_repo / "templates" / "services"
    p = _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "1.0.0", digest="a" * 64)
    new_digest = "b" * 64

    rc = upgrade_cmd.cmd_component("alpha", "1.0.1", digest=new_digest)

    assert rc == 0
    text = p.read_text(encoding="utf-8")
    # New tag on a bare image line (no inline @sha256:) + new separate digest.
    assert "image: vendor/alpha:1.0.1" in text
    assert "@sha256:" not in text
    assert f"digest: {new_digest}" in text


def test_component_plan_only_also_refuses_separate_form(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--plan without --digest also refuses: no safe plan exists, file untouched.

    The refusal is single-sourced in the plan builder, so even a read-only
    preview surfaces the missing-digest error (and the remedy) before the
    operator wastes a deploy. The descriptor is never mutated.
    """
    services = tmp_repo / "templates" / "services"
    p = _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "1.0.0", digest="a" * 64)
    original = p.read_text(encoding="utf-8")

    rc = upgrade_cmd.cmd_component("alpha", "1.0.1", plan_only=True)

    assert rc != 0
    assert p.read_text(encoding="utf-8") == original
    assert "--digest" in capsys.readouterr().err


def test_component_digestless_descriptor_still_bumps_without_digest(
    tmp_repo: Path,
) -> None:
    """The guard must NOT over-fire: a descriptor with no digest pin bumps freely."""
    services = tmp_repo / "templates" / "services"
    p = _make_descriptor(services, "alpha", "vendor/alpha", "1.0.0")  # no digest line

    rc = upgrade_cmd.cmd_component("alpha", "1.0.1")

    assert rc == 0
    assert "vendor/alpha:1.0.1" in p.read_text(encoding="utf-8")


def test_component_separate_form_noop_does_not_refuse(
    tmp_repo: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Already at target → no bump → no digest needed (guard only fires on a real bump)."""
    services = tmp_repo / "templates" / "services"
    _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "1.0.0", digest="a" * 64)

    rc = upgrade_cmd.cmd_component("alpha", "1.0.0")  # same tag → no-op

    assert rc == 0
    assert "already at" in capsys.readouterr().out.lower()


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


def test_load_holds_absent_returns_empty(tmp_repo: Path) -> None:
    # HOLDS_FILE points at a not-yet-created tmp path → absent → {} (no error).
    assert upgrade_cmd._load_holds() == {}


def test_load_holds_corrupt_aborts(tmp_repo: Path) -> None:
    """Review MEDIUM upgrade-corrupt-holds-dropped: a corrupt holds file must abort, not be
    silently treated as empty (which would let upgrade bump every frozen image)."""
    import typer

    upgrade_cmd.HOLDS_FILE.write_text(
        "vendor/alpha:\n  reason: 'x'\n  : : :\n broken", encoding="utf-8"
    )
    with pytest.raises(typer.Exit):
        upgrade_cmd._load_holds()


def test_load_holds_non_mapping_aborts(tmp_repo: Path) -> None:
    import typer

    upgrade_cmd.HOLDS_FILE.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(typer.Exit):
        upgrade_cmd._load_holds()


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
    from agmind.core import files as files_mod

    def flaky_fsync(fd: int) -> None:
        # write_text_atomic fsyncs the temp fd before the atomic replace; failing
        # there must unlink the partial state temp and leave no .json behind.
        raise OSError("disk full")

    monkeypatch.setattr(files_mod.os, "fsync", flaky_fsync)

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


def test_component_upgrade_skips_divergent_scheme_members(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """G.3: a component-wide upgrade bumps ONLY same-scheme members.

    The dify-like fixture is heterogeneous: dify-api shares the component
    reference line (1.14.2) while dify-sandbox (0.2.15) and dify-plugin-daemon
    (0.6.1-local) are on divergent version schemes. `agmind upgrade dify 1.15.0`
    must rewrite ONLY dify-api to 1.15.0; the divergent members are marked
    unchanged (new_tag == old_tag, so cmd_component's changed_items filter drops
    them) and each is named in a stderr WARNING.
    """
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "dify-api", "langgenius/dify-api", "1.14.2")
    _make_descriptor(services, "dify-sandbox", "langgenius/dify-sandbox", "0.2.15")
    _make_descriptor(services, "dify-plugin-daemon", "langgenius/dify-plugin-daemon", "0.6.1-local")
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-sandbox", "dify-plugin-daemon"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    plan = upgrade_cmd.build_component_upgrade_plan("dify", "1.15.0")

    by_service = {item.service: item for item in plan.items}

    # Same-scheme member (on the 1.14.2 reference line) is bumped.
    assert by_service["dify-api"].new_tag == "1.15.0"

    # Divergent members are NOT rewritten to the target tag — marked unchanged
    # so cmd_component's changed_items filter (old_tag != new_tag) drops them.
    assert by_service["dify-sandbox"].new_tag == "0.2.15"
    assert by_service["dify-sandbox"].new_tag == by_service["dify-sandbox"].old_tag
    assert by_service["dify-plugin-daemon"].new_tag == "0.6.1-local"
    assert by_service["dify-plugin-daemon"].new_tag == by_service["dify-plugin-daemon"].old_tag

    # Each divergent member is named in a stderr WARNING.
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "dify-sandbox" in err
    assert "0.2.15" in err
    assert "dify-plugin-daemon" in err
    assert "0.6.1-local" in err


def test_component_upgrade_does_not_write_divergent_members(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G.3 end-to-end: `agmind upgrade dify <v>` leaves divergent pins intact.

    sandbox/plugin-daemon descriptors keep their own tags; only the same-scheme
    member is rewritten to the target version on disk.
    """
    services = tmp_repo / "templates" / "services"
    _make_descriptor(services, "dify-api", "langgenius/dify-api", "1.14.2")
    _make_descriptor(services, "dify-sandbox", "langgenius/dify-sandbox", "0.2.15")
    _make_descriptor(services, "dify-plugin-daemon", "langgenius/dify-plugin-daemon", "0.6.1-local")
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-sandbox", "dify-plugin-daemon"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    rc = upgrade_cmd.cmd_component("dify", "1.15.0", plan_only=False)

    assert rc == 0
    assert "langgenius/dify-api:1.15.0" in (services / "dify-api.yaml").read_text()
    # Divergent members keep their original tags — never rewritten to 1.15.0.
    sandbox_yaml = (services / "dify-sandbox.yaml").read_text()
    assert "langgenius/dify-sandbox:0.2.15" in sandbox_yaml
    assert "1.15.0" not in sandbox_yaml
    daemon_yaml = (services / "dify-plugin-daemon.yaml").read_text()
    assert "langgenius/dify-plugin-daemon:0.6.1-local" in daemon_yaml
    assert "1.15.0" not in daemon_yaml


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
    from agmind.core import files as files_mod

    def flaky_fsync(fd: int) -> None:
        # write_text_atomic fsyncs the temp fd before the atomic replace; failing
        # there must unlink the partial state temp and leave no .json behind.
        raise OSError("disk full")

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
    monkeypatch.setattr(files_mod.os, "fsync", flaky_fsync)

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


def test_rollback_restores_old_digest_on_separate_form_descriptor(tmp_repo: Path) -> None:
    """D-01 (P0.2) regression: rollback of a separate-form descriptor must restore
    the ORIGINAL digest, not leave the bumped NEW digest under the restored old tag.

    `_read_current_pin` only ever sees an inline `@sha256:` digest; 34/40 catalog
    descriptors carry the digest on a SEPARATE `digest:` line instead. Pre-fix,
    `old_digest` persisted as None in upgrade-state, so rollback wrote
    `image: OLD_TAG` while leaving `digest:` at the NEW value — docker then
    resolves by digest and silently deploys the NEW image under the OLD tag.
    """
    services = tmp_repo / "templates" / "services"
    old_digest = "a" * 64
    new_digest = "b" * 64
    _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "1.0.0", digest=old_digest)

    rc = upgrade_cmd.cmd_component("alpha", "2.0.0", digest=new_digest)
    assert rc == 0
    bumped = (services / "alpha.yaml").read_text()
    assert "image: vendor/alpha:2.0.0" in bumped
    assert f"digest: {new_digest}" in bumped

    rc = upgrade_cmd.cmd_rollback()
    assert rc == 0

    restored = (services / "alpha.yaml").read_text()
    assert "image: vendor/alpha:1.0.0" in restored
    assert f"digest: {old_digest}" in restored
    assert new_digest not in restored


def test_grouped_rollback_restores_old_digest_on_separate_form_descriptors(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-01 (P0.2) regression: grouped/component rollback restores each member's
    OWN original digest on separate-form descriptors, not the bumped NEW digest."""
    services = tmp_repo / "templates" / "services"
    old_digest_api = "a" * 64
    old_digest_web = "c" * 64
    new_digest = "b" * 64
    _make_separate_form_descriptor(
        services, "dify-api", "langgenius/dify-api", "1.14.2", digest=old_digest_api
    )
    _make_separate_form_descriptor(
        services, "dify-web", "langgenius/dify-web", "1.14.2", digest=old_digest_web
    )
    components = tmp_repo / "templates" / "components"
    _make_component_contract(components, "dify", ["dify-api", "dify-web"])
    monkeypatch.setattr(upgrade_cmd, "COMPONENTS_DIR", components)

    upgrade_cmd.cmd_component("dify", "1.14.3", digest=new_digest, plan_only=False)
    rc = upgrade_cmd.cmd_rollback()

    assert rc == 0
    api_text = (services / "dify-api.yaml").read_text()
    web_text = (services / "dify-web.yaml").read_text()
    assert "image: langgenius/dify-api:1.14.2" in api_text
    assert f"digest: {old_digest_api}" in api_text
    assert "image: langgenius/dify-web:1.14.2" in web_text
    assert f"digest: {old_digest_web}" in web_text


def test_rollback_refuses_legacy_state_against_digest_pinned_descriptor(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-01 (P0.2) guard: a legacy state file (old_digest absent/None) rolling
    back against a descriptor that is CURRENTLY digest-pinned must refuse rather
    than silently write old_tag while leaving the NEW digest in place."""
    services = tmp_repo / "templates" / "services"
    _make_separate_form_descriptor(services, "alpha", "vendor/alpha", "2.0.0", digest="b" * 64)

    state_dir = upgrade_cmd.UPGRADE_STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "2020-01-01T00-00-00Z_alpha.json"
    state_file.write_text(
        json.dumps(
            {
                "service": "alpha",
                "yaml_path": str(services / "alpha.yaml"),
                "old_tag": "1.0.0",
                "new_tag": "2.0.0",
                "old_digest": None,
                "timestamp": "2020-01-01T00-00-00Z",
            }
        )
    )

    def must_not_mutate(*_a: object, **_kw: object) -> object:
        raise AssertionError("_bump_pin_in_yaml must not be called on a legacy-state refuse")

    monkeypatch.setattr(upgrade_cmd, "_bump_pin_in_yaml", must_not_mutate)

    rc = upgrade_cmd.cmd_rollback()

    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "digest" in err
    assert "--digest" in err or "re-pin" in err
    # Confirm nothing was mutated or archived.
    assert "vendor/alpha:2.0.0" in (services / "alpha.yaml").read_text()
    assert list(state_dir.glob("*.json")) == [state_file]


def test_grouped_rollback_refuses_legacy_state_against_digest_pinned_descriptor(
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """D-01 (P0.2) guard, grouped/component branch: same refuse behavior as the
    single-service case, for a legacy component-plan state file."""
    services = tmp_repo / "templates" / "services"
    _make_separate_form_descriptor(
        services, "dify-api", "langgenius/dify-api", "1.14.3", digest="b" * 64
    )

    state_dir = upgrade_cmd.UPGRADE_STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "2020-01-01T00-00-00Z_dify.json"
    state_file.write_text(
        json.dumps(
            {
                "component": "dify",
                "policy": "strict-pin",
                "timestamp": "2020-01-01T00-00-00Z",
                "items": [
                    {
                        "service": "dify-api",
                        "yaml_path": str(services / "dify-api.yaml"),
                        "image": "langgenius/dify-api",
                        "old_tag": "1.14.2",
                        "new_tag": "1.14.3",
                        "old_digest": None,
                        "new_digest": "b" * 64,
                    }
                ],
            }
        )
    )

    def must_not_mutate(*_a: object, **_kw: object) -> object:
        raise AssertionError("_bump_pin_in_yaml must not be called on a legacy-state refuse")

    monkeypatch.setattr(upgrade_cmd, "_bump_pin_in_yaml", must_not_mutate)

    rc = upgrade_cmd.cmd_rollback()

    assert rc == 1
    err = capsys.readouterr().err.lower()
    assert "digest" in err
    assert "--digest" in err or "re-pin" in err
    assert "langgenius/dify-api:1.14.3" in (services / "dify-api.yaml").read_text()
    assert list(state_dir.glob("*.json")) == [state_file]


# ---------- cmd_apply ----------


def test_apply_reads_profiles_services_domain_from_deploy_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0.1/D-03: `cmd_apply` no longer hardcodes `profiles=["core","rag",
    "observability"]`/`domain=None` — it reads the previously-applied selection
    from `deploy-state.json` (flips the historical RED assertion)."""
    from agmind.deploy.runner import DeployResult
    from agmind.deploy.state import DeployState, write_deploy_state

    write_deploy_state(
        tmp_path,
        DeployState.new(
            agmind_version="9.9.9",
            profiles=["core", "ui"],
            requested_services=[],
            resolved_services=["postgres", "traefik", "openwebui"],
            domain="lab.example.com",
            edge_mode="lan",
        ),
    )

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)

    rc = upgrade_cmd.cmd_apply(install_dir=tmp_path, healthcheck_timeout=7)

    assert rc == 0
    assert calls["profiles"] == ["core", "ui"]
    assert calls["services"] == ["postgres", "traefik", "openwebui"]
    assert calls["domain"] == "lab.example.com"
    assert calls["install_dir"] == tmp_path
    assert calls["healthcheck_timeout"] == 7
    assert calls["allow_removal"] is False


def test_apply_legacy_state_fallback_warns_and_deploys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No deploy-state.json yet, but a legacy setup-state.json exists → cmd_apply
    derives the selection from it and prints a loud WARNING (D-03 legacy fallback,
    never silent)."""
    from agmind.cli.tui.setup_wizard import SetupState
    from agmind.deploy.runner import DeployResult

    legacy_path = tmp_path / "legacy-setup-state.json"
    SetupState(domain="legacy.example.com", services=["postgres", "qdrant"]).to_json(legacy_path)
    monkeypatch.setattr("agmind.cli.tui.setup_wizard.STATE_PATH", legacy_path)

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)

    rc = upgrade_cmd.cmd_apply(install_dir=tmp_path)

    assert rc == 0
    assert calls["services"] == ["postgres", "qdrant"]
    assert calls["domain"] == "legacy.example.com"
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "legacy" in err.lower()


def test_apply_hard_refuses_with_no_deploy_state_and_no_legacy_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Neither deploy-state.json nor a legacy setup-state.json exists → refuse
    rather than fall back to the old hardcoded profile list (D-03 closes P0.1 with
    no silent fallback)."""
    monkeypatch.setattr(
        "agmind.cli.tui.setup_wizard.STATE_PATH", tmp_path / "nonexistent-setup-state.json"
    )

    def must_not_deploy(**_kwargs: object) -> object:
        raise AssertionError("runner.deploy must not be called when no state is known")

    monkeypatch.setattr("agmind.deploy.runner.deploy", must_not_deploy)

    rc = upgrade_cmd.cmd_apply(install_dir=tmp_path)

    assert rc == 2
    err = capsys.readouterr().err
    assert "--profile" in err or "agmind install" in err


def test_apply_threads_skip_data_backup_to_deploy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stateful upgrade can pass `--skip-data-backup` through to `deploy(...)`
    (P1-3 satisfiability for the no_prompt upgrade path)."""
    from agmind.deploy.runner import DeployResult
    from agmind.deploy.state import DeployState, write_deploy_state

    write_deploy_state(
        tmp_path,
        DeployState.new(
            agmind_version="9.9.9",
            profiles=["core"],
            requested_services=[],
            resolved_services=["postgres"],
            domain=None,
            edge_mode="local",
        ),
    )

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)

    rc = upgrade_cmd.cmd_apply(install_dir=tmp_path, skip_data_backup=True)

    assert rc == 0
    assert calls["skip_data_backup"] is True
    assert calls["allow_removal"] is False


def test_apply_bare_forwards_none_healthcheck_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `cmd_apply()` (no explicit timeout) forwards None so the runner sizes
    the wait budget from the actual selection instead of a flat 300s (BREA02)."""
    from agmind.deploy.runner import DeployResult
    from agmind.deploy.state import DeployState, write_deploy_state

    write_deploy_state(
        tmp_path,
        DeployState.new(
            agmind_version="9.9.9",
            profiles=["core"],
            requested_services=[],
            resolved_services=["postgres"],
            domain=None,
            edge_mode="local",
        ),
    )

    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr("agmind.deploy.runner.deploy", fake_deploy)

    rc = upgrade_cmd.cmd_apply(install_dir=tmp_path)

    assert rc == 0
    assert calls["healthcheck_timeout"] is None


def test_upgrade_apply_cli_healthcheck_timeout_flag_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`agmind upgrade --apply --healthcheck-timeout N` overrides the None default."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_apply(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd_apply", fake_cmd_apply)

    result = CliRunner().invoke(
        _make_app(),
        ["upgrade", "--apply", "--healthcheck-timeout", "42"],
    )

    assert result.exit_code == 0
    assert captured["healthcheck_timeout"] == 42


def test_upgrade_apply_cli_omitting_healthcheck_timeout_forwards_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting --healthcheck-timeout on `agmind upgrade --apply` forwards None."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_apply(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd_apply", fake_cmd_apply)

    result = CliRunner().invoke(_make_app(), ["upgrade", "--apply"])

    assert result.exit_code == 0
    assert captured["healthcheck_timeout"] is None


def test_upgrade_apply_cli_skip_data_backup_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`agmind upgrade --apply --skip-data-backup` reaches cmd_apply as True."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_apply(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd_apply", fake_cmd_apply)

    result = CliRunner().invoke(
        _make_app(),
        ["upgrade", "--apply", "--skip-data-backup"],
    )

    assert result.exit_code == 0
    assert captured["skip_data_backup"] is True


def test_upgrade_apply_cli_omitting_skip_data_backup_defaults_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting --skip-data-backup on `agmind upgrade --apply` defaults to False,
    keeping the D-06 stateful-backup guard armed."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_apply(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(upgrade_cmd, "cmd_apply", fake_cmd_apply)

    result = CliRunner().invoke(_make_app(), ["upgrade", "--apply"])

    assert result.exit_code == 0
    assert captured["skip_data_backup"] is False


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
