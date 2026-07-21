"""SPEC-17.1 guard: `agmind.install.steps` stayed a byte-stable import surface.

`agmind/install/steps.py` (2770 lines) became the package `agmind/install/steps/`.
~40 production+test import sites pull symbols — public AND private — out of that
module name, and several test modules `import agmind.install.steps as steps` and
then `monkeypatch.setattr(steps, "_helper", ...)`. A plain submodule split silently
breaks the latter: a helper moved into `configs.py`/`models.py` binds its callees
into ITS OWN globals at import time, so patching the package attribute no longer
reaches them. The submodules therefore resolve the patch-sensitive names through
the package object at call time; these tests pin both halves of that contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agmind.install.steps as steps
from agmind.install.orchestrator import InstallConfig

pytestmark = pytest.mark.backend_any


# Private names imported by production code or the existing test-suite from
# `agmind.install.steps`. Every one of them was a module global before the split.
_PRIVATE_REEXPORTS = (
    # config / secret materialization (configs.py)
    "_ALERTMANAGER_MULTICHANNEL_KEYS",
    "_ALERTMANAGER_TELEGRAM_KEYS",
    "_RUNTIME_TARGET_GUARD_SCRIPT",
    "_SMTP_PASSWORD_FILE",
    "_WEBHOOK_URL_FILE",
    "_assert_sudo_runtime_targets_safe",
    "_authelia_argon2_hash",
    "_cleanup_path",
    "_copy_file_atomic",
    "_copytree_atomic",
    "_copytree_contents",
    "_ensure_models_dir",
    "_materialize_runtime_files",
    "_redact_install_secrets",
    "_replace_authelia_password_hash",
    "_replace_path_atomic",
    "_run_sudo_runtime_command",
    "_stage_alertmanager_config",
    "_stage_authelia_config",
    "_stage_directory_contents",
    "_stage_prometheus_config",
    "_stage_runtime_payload",
    "_stage_single_file_config",
    "_stage_squid_config",
    "_sudo_runtime_target_args",
    "_write_private_text_maybe_sudo",
    "_write_runtime_payload_local",
    "_write_runtime_payload_sudo",
    "_write_secret_file",
    # cloudflare.py
    "_CLOUDFLARE_API_BASE",
    "_cloudflare_payload_errors",
    "_cloudflare_request_json",
    "_cloudflare_zone_candidates",
    # gpu_metrics.py
    "_GPU_METRICS_SERVICE_PATH",
    "_GPU_METRICS_TEXTFILE_DIR",
    "_GPU_METRICS_TIMER_PATH",
    "_GPU_METRICS_TIMER_UNIT",
    "_gpu_metrics_service_unit",
    "_host_has_amd_gpu",
    # boot_unit.py
    "_AGMIND_STACK_SERVICE_PATH",
    "_agmind_stack_unit",
    "_selected_compose_profiles",
    # kept in __init__ / _common.py
    "_RUNTIME_SECRET_KEYS",
    "_check_proxmox_config_staged",
    "_docker_compose_cmd",
    "_env_line",
    "_healthcheck_timeout_for",
    "_image_digest",
    "_image_tag",
    "_kill_on_cancel",
    "_make_event",
    "_offline_install_enabled",
    "_parse_existing_runtime_env",
    "_pull_progress_pct",
    "_runtime_env",
    "_runtime_version_env",
    "_stream_subprocess",
    "_sudo_stdin_payload",
    "_user_docker_config_dir",
    "_version_key",
    "_write_compose_env_file",
    # re-exported dependencies the suite patches on the package object
    "DEFAULT_REPO_ROOT",
    "os",
    "parse_env_file",
    "resolve_ansible_command",
    "shutil",
    "subprocess",
    "urllib",
    "write_private_text",
)


def _config(tmp_path: Path, **overrides: object) -> InstallConfig:
    base: dict[str, object] = {
        "domain": "lab.example.com",
        "cf_api_token": "",
        "services": ["llama-llm"],
        "install_dir": tmp_path / "opt",
        "models_dir": tmp_path / "var" / "lib" / "agmind" / "models",
        "config_dir": tmp_path / "etc" / "agmind",
    }
    base.update(overrides)
    return InstallConfig(**base)  # type: ignore[arg-type]


def test_every_public_name_in_all_is_importable() -> None:
    missing = [name for name in steps.__all__ if not hasattr(steps, name)]
    assert not missing, f"__all__ names missing from the package: {missing}"
    assert len(steps.__all__) == 12


def test_every_private_reexport_is_importable() -> None:
    missing = [name for name in _PRIVATE_REEXPORTS if not hasattr(steps, name)]
    assert not missing, (
        "private symbols vanished from `agmind.install.steps` — production code and "
        f"the existing test-suite import these by name: {missing}"
    )


def test_from_import_of_moved_symbols_still_resolves() -> None:
    # The `from agmind.install.steps import X` form, exactly as consumers spell it.
    from agmind.install.steps import (  # noqa: F401
        _GPU_METRICS_TIMER_UNIT,
        BootUnitStep,
        CloudflareTokenStep,
        GpuMetricsStep,
        ModelDownloadStep,
        _agmind_stack_unit,
        _gpu_metrics_service_unit,
        _materialize_runtime_files,
        _stage_alertmanager_config,
        _stage_authelia_config,
        _stage_runtime_payload,
        _stage_squid_config,
        build_alertmanager_config,
    )


def test_default_steps_order_is_unchanged() -> None:
    assert [type(step).__name__ for step in steps.default_steps()] == [
        "DoctorStep",
        "CloudflareTokenStep",
        "BootstrapStep",
        "EnvWriteStep",
        "ComposeConfigStep",
        "ModelDownloadStep",
        "DeployStep",
        "GpuMetricsStep",
        "BootUnitStep",
        "CredentialsStep",
    ]


def test_patching_stream_subprocess_reaches_the_configs_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_run_sudo_runtime_command` moved to configs.py; its `_stream_subprocess` call
    must still resolve to the package attribute the suite patches."""
    calls: list[list[str]] = []

    def fake_stream(cmd: list[str], *a: object, **k: object) -> tuple[int, list[str]]:
        calls.append(cmd)
        return 0, []

    monkeypatch.setattr(steps, "_stream_subprocess", fake_stream)
    steps._run_sudo_runtime_command(
        _config(tmp_path, sudo_password="pw"), ["true"], lambda _e: None, "step"
    )
    assert calls, "configs.py captured its own _stream_subprocess reference at import time"
    assert calls[0][-1] == "true"


def test_patching_run_sudo_runtime_command_reaches_gpu_and_boot_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GpuMetricsStep / BootUnitStep moved out of __init__; both call the patched helper."""
    seen: list[list[str]] = []
    monkeypatch.setattr(
        steps, "_run_sudo_runtime_command", lambda _c, cmd, _cb, _s: seen.append(cmd)
    )
    monkeypatch.setattr(steps, "_host_has_amd_gpu", lambda: True)

    cfg = _config(tmp_path, services=["node-exporter"], sudo_password="pw")
    assert steps.GpuMetricsStep().run(lambda _e: None, cfg).success
    assert any("systemctl" in cmd for cmd in seen), seen

    seen.clear()
    boot_cfg = _config(tmp_path, sudo_password="pw")
    (boot_cfg.install_dir).mkdir(parents=True, exist_ok=True)
    (boot_cfg.install_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    assert steps.BootUnitStep().run(lambda _e: None, boot_cfg).success
    assert any("agmind-stack.service" in cmd for cmd in seen), seen


def test_patching_copy_file_atomic_reaches_its_configs_internal_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_copytree_contents` and `_copy_file_atomic` both live in configs.py; the suite
    patches the latter on the package and expects the former's call to observe it."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.yml").write_text("a\n", encoding="utf-8")
    copied: list[str] = []
    monkeypatch.setattr(steps, "_copy_file_atomic", lambda s, t: copied.append(s.name))
    steps._copytree_contents(source, tmp_path / "dst")
    assert copied == ["a.yml"]


def test_patching_offline_and_models_dir_reaches_the_models_submodule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ModelDownloadStep moved to models.py; both helpers it calls are patched by the
    existing model tests on the package object."""
    order: list[str] = []
    monkeypatch.setattr(steps, "_ensure_models_dir", lambda *_a: order.append("ensure"))
    monkeypatch.setattr(steps, "_offline_install_enabled", lambda: True)

    cfg = _config(tmp_path, model_repo="r/e", model_file="m.gguf")
    cfg.models_dir.mkdir(parents=True, exist_ok=True)
    result = steps.ModelDownloadStep().run(lambda _e: None, cfg)

    assert order == ["ensure"], "models.py bypassed the patched _ensure_models_dir"
    assert not result.success
    assert "AGMIND_OFFLINE" in result.message, result.message
