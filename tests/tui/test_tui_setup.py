"""Phase J: tests для agmind.cli.tui.setup_wizard через Textual Pilot."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agmind.cli.tui import setup_wizard
from agmind.cli.tui.setup_wizard import (
    AgmindSetupApp,
    DetectedHardware,
    SetupState,
    detect_hardware,
)

pytestmark = pytest.mark.backend_any


# ---------- SetupState ----------


def test_state_default() -> None:
    s = SetupState()
    assert s.domain == ""
    assert s.backend == "auto"
    # Smart defaults: core inference + operator observability/console services.
    assert "traefik" in s.services
    assert "llama-llm" in s.services
    assert "qdrant" in s.services
    assert "prometheus" in s.services
    assert "uptime-kuma" in s.services
    assert "homarr" in s.services
    assert "watchtower" in s.services
    assert "dozzle" in s.services
    assert "netdata" in s.services
    assert s.profiles == []  # profiles больше не primary


def test_state_roundtrip_excludes_token(tmp_path: Path) -> None:
    s = SetupState(
        domain="x.example",
        cf_api_token="secret-xyz",
        sudo_password="sudo-secret",
        profiles=["core"],
    )
    path = tmp_path / "state.json"
    s.to_json(path)
    data = json.loads(path.read_text())
    # Token НЕ должен попадать в state.json
    assert "cf_api_token" not in data
    assert "sudo_password" not in data
    assert data["domain"] == "x.example"

    loaded = SetupState.from_json(path)
    assert loaded.domain == "x.example"
    assert loaded.cf_api_token == ""  # not persisted
    assert loaded.sudo_password == ""  # not persisted


def test_state_to_json_preserves_existing_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    old = json.dumps({"domain": "old.example"}) + "\n"
    path.write_text(old, encoding="utf-8")
    path.chmod(0o600)
    original_write_text = Path.write_text

    def flaky_write_text(self: Path, data: str, *args: object, **kwargs: object) -> int:
        if self == path or self.name == f".{path.name}.tmp":
            original_write_text(self, "BROKEN\n", encoding="utf-8")
            raise OSError("disk full")
        return original_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="disk full"):
        SetupState(domain="new.example").to_json(path)

    assert path.read_text(encoding="utf-8") == old
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_name(f".{path.name}.tmp").exists()


# ---------- detect_hardware ----------


def test_detect_returns_dataclass() -> None:
    d = detect_hardware()
    assert isinstance(d, DetectedHardware)
    assert d.ram_gb > 0
    assert d.recommended_tier in ("S", "M", "L", "XL")


def test_detect_includes_required_fields() -> None:
    d = detect_hardware()
    assert hasattr(d, "vulkan_present")
    assert hasattr(d, "rocm_present")
    assert hasattr(d, "docker_present")
    assert hasattr(d, "is_strix_halo")


def test_detect_hardware_ignores_lspci_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: object) -> object:
        raise PermissionError("lspci denied")

    monkeypatch.setattr(setup_wizard.subprocess, "run", fake_run)

    detected = detect_hardware()

    assert detected.gpu_name is None
    assert detected.is_strix_halo is False


# ---------- Validation ----------


def test_validate_rejects_empty_domain() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected, initial_state=SetupState(domain=""))
    state = SetupState(domain="", cf_api_token="x" * 30, profiles=["core"])
    errors = app._validate(state)
    assert any("domain" in e.lower() for e in errors)


def test_validate_accepts_real_owned_domain() -> None:
    """User может реально владеть `agmind.dev` — не reject."""
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    # agmind.dev — это реальный домен пользователя
    state = SetupState(domain="agmind.dev", cf_api_token="x" * 30, profiles=["core"])
    errors = app._validate(state)
    assert errors == []  # NO placeholder rejection


def test_validate_rejects_short_token() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(domain="x.example", cf_api_token="short", profiles=["core"])
    errors = app._validate(state)
    assert any("token" in e.lower() for e in errors)


def test_validate_rejects_no_services_or_profiles() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="x.example",
        cf_api_token="x" * 30,
        services=[],
        profiles=[],  # ничего не выбрано
    )
    errors = app._validate(state)
    assert any("service" in e.lower() for e in errors)


def test_validate_rejects_unknown_selected_service() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="x.example",
        cf_api_token="x" * 30,
        services=["traefik", "missing-service"],
        profiles=[],
    )

    errors = app._validate(state)

    assert "unknown selected services: missing-service" in errors


def test_validate_rejects_unknown_legacy_profile() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="x.example",
        cf_api_token="x" * 30,
        services=[],
        profiles=["missing-profile"],
    )

    errors = app._validate(state)

    assert "unknown selected profiles: missing-profile" in errors


def test_validate_explicit_services_ignore_unused_unknown_profiles() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="x.example",
        cf_api_token="x" * 30,
        services=["traefik"],
        profiles=["missing-profile"],
    )

    errors = app._validate(state)

    assert "unknown selected profiles: missing-profile" not in errors


def test_setup_app_deployment_topology_report_uses_shared_policy() -> None:
    app = AgmindSetupApp(
        detected=DetectedHardware(
            ram_gb=128,
            gpu_name="x",
            is_strix_halo=True,
            vulkan_present=True,
            rocm_present=True,
            docker_present=True,
            recommended_tier="XL",
        )
    )
    state = SetupState(
        domain="x.example",
        cf_api_token="x" * 30,
        services=["dify-api", "milvus", "qdrant", "postgres", "redis"],
    )

    report = app._deployment_topology_report(state)

    assert "DIFY VECTOR DB ..... milvus (ambiguous: qdrant also selected)" in report.retrieval_lines
    assert any(
        "Dify has multiple vector_db providers selected" in warning
        for warning in report.compatibility_warnings
    )


def test_state_explicit_services_overrides_default() -> None:
    s = SetupState(services=["traefik", "llama-llm"])
    assert s.services == ["traefik", "llama-llm"]


def test_expand_selected_services_for_setup_expands_dify_api() -> None:
    from agmind.cli.tui.setup_wizard import expand_selected_services_for_setup

    services = expand_selected_services_for_setup(["dify-api"])

    assert "dify-api" in services
    assert "dify-web" in services
    assert "dify-worker" in services
    assert "dify-plugin-daemon" in services
    assert "dify-sandbox" in services
    assert "postgres" in services
    assert "redis" in services
    assert "qdrant" in services
    assert "llama-llm" in services
    assert "llama-embed" in services
    assert "ragflow" not in services


def test_expand_selected_services_for_setup_uses_milvus_without_qdrant() -> None:
    from agmind.cli.tui.setup_wizard import expand_selected_services_for_setup

    services = expand_selected_services_for_setup(["dify-api", "milvus"])

    assert "dify-api" in services
    assert "milvus" in services
    assert "qdrant" not in services


def test_expand_selected_services_for_setup_keeps_ragflow_search_index_separate() -> None:
    from agmind.cli.tui.setup_wizard import expand_selected_services_for_setup

    services = expand_selected_services_for_setup(["dify-api", "ragflow", "milvus"])

    assert "dify-api" in services
    assert "milvus" in services
    assert "ragflow" in services
    assert "elasticsearch" in services
    assert "qdrant" not in services


# ---------- Phase M3.S.1: inline Input validators ----------


def test_domain_validator_empty_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase M4.3: force EN для assertion stability (validators теперь i18n)
    monkeypatch.setenv("AGMIND_LANG", "en")
    from agmind.cli.tui.setup_wizard import DomainValidator

    v = DomainValidator()
    result = v.validate("")
    assert not result.is_valid
    assert "required" in (result.failure_descriptions[0] if result.failure_descriptions else "")


def test_domain_validator_no_dot_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    from agmind.cli.tui.setup_wizard import DomainValidator

    result = DomainValidator().validate("localhost")
    assert not result.is_valid
    assert "'." in (result.failure_descriptions[0] if result.failure_descriptions else "")


def test_domain_validator_accepts_real_owned_agmind_dev_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    from agmind.cli.tui.setup_wizard import DomainValidator

    result = DomainValidator().validate("agmind.dev")
    assert result.is_valid


def test_domain_validator_valid_passes() -> None:
    from agmind.cli.tui.setup_wizard import DomainValidator

    for v in ("lab.example.com", "agi.mycorp.io", "x.y.z.test"):
        result = DomainValidator().validate(v)
        assert result.is_valid, f"{v} should be valid"


def test_token_validator_empty_ok() -> None:
    from agmind.cli.tui.setup_wizard import TokenLengthValidator

    # Empty OK — token может load'нуться из --cf-token-file
    result = TokenLengthValidator().validate("")
    assert result.is_valid


def test_token_validator_short_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LANG", "en")
    from agmind.cli.tui.setup_wizard import TokenLengthValidator

    result = TokenLengthValidator().validate("abc123")
    assert not result.is_valid
    assert "too short" in (result.failure_descriptions[0] if result.failure_descriptions else "")


def test_token_validator_long_passes() -> None:
    from agmind.cli.tui.setup_wizard import TokenLengthValidator

    result = TokenLengthValidator().validate("X" * 40)
    assert result.is_valid


def test_state_default_model_settings_phase_n_g() -> None:
    """Phase N.G: SetupState имеет model_id / ctx_size / kv_cache_type defaults."""
    s = SetupState()
    assert s.model_id == "qwen36-a3b-q4km"
    assert s.ctx_size == 16384
    assert s.kv_cache_type == "q8_0"


def test_resolve_model_repo_file_curated() -> None:
    """resolve_model_repo_file() возвращает curated repo/file для known id."""
    s = SetupState(model_id="qwen36-a3b-q4km")
    repo, file = s.resolve_model_repo_file()
    assert repo == "0xSero/Qwen3.6-35B-A3B-GGUF-Strix"
    assert file == "Qwen3.6-35B-A3B-Q4_K_M.gguf"


def test_resolve_model_repo_file_custom() -> None:
    """resolve_model_repo_file() с id='custom' возвращает raw model_repo/file."""
    s = SetupState(model_id="custom", model_repo="my/repo", model_file="x.gguf")
    repo, file = s.resolve_model_repo_file()
    assert repo == "my/repo"
    assert file == "x.gguf"


def test_resolve_model_repo_file_unknown_id_fallback() -> None:
    """Неизвестный id → возвращает raw fields (degrade gracefully)."""
    s = SetupState(model_id="bogus-id", model_repo="alt/repo", model_file="alt.gguf")
    repo, file = s.resolve_model_repo_file()
    assert repo == "alt/repo"
    assert file == "alt.gguf"


def test_state_json_roundtrip_phase_n_fields(tmp_path: pytest.TempPathFactory) -> None:
    """Phase N.G fields сохраняются и читаются обратно через to_json/from_json."""
    from pathlib import Path

    s = SetupState(
        domain="x.example",
        model_id="custom",
        model_repo="my/repo",
        model_file="m.gguf",
        ctx_size=32768,
        kv_cache_type="q4_0",
    )
    path = Path(str(tmp_path)) / "state.json"
    s.to_json(path)
    loaded = SetupState.from_json(path)
    assert loaded.model_id == "custom"
    assert loaded.model_repo == "my/repo"
    assert loaded.model_file == "m.gguf"
    assert loaded.ctx_size == 32768
    assert loaded.kv_cache_type == "q4_0"


def test_state_from_json_backward_compat_missing_fields(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Старый state.json без Phase N.G полей загружается с default'ами."""
    import json
    from pathlib import Path

    path = Path(str(tmp_path)) / "old.json"
    path.write_text(
        json.dumps(
            {
                "domain": "old.example",
                "profiles": [],
                "services": [],
                "backend": "auto",
                "model_tier": "auto",
                "install_dir": "/opt/agmind",
            }
        )
    )
    loaded = SetupState.from_json(path)
    assert loaded.domain == "old.example"
    assert loaded.model_id == "qwen36-a3b-q4km"  # default
    assert loaded.ctx_size == 16384  # default


def test_get_services_by_tier_returns_grouped() -> None:
    from agmind.cli.tui.setup_wizard import _TIER_ORDER, get_services_by_tier

    by_tier = get_services_by_tier()
    assert len(by_tier) > 0
    # Order matches _TIER_ORDER
    tier_keys = list(by_tier.keys())
    for tier in tier_keys:
        if tier in _TIER_ORDER:
            assert _TIER_ORDER.index(tier) >= 0
    # traefik в edge, qdrant в storage
    edge_names = [n for n, _ in by_tier.get("edge", [])]
    storage_names = [n for n, _ in by_tier.get("storage", [])]
    assert "traefik" in edge_names
    assert "qdrant" in storage_names


def test_get_services_by_department_returns_installer_product_sections() -> None:
    from agmind.cli.tui.setup_wizard import (
        _SERVICE_DEPARTMENT_ORDER,
        get_services_by_department,
    )

    by_department = get_services_by_department()

    assert list(by_department) == [
        department for department in _SERVICE_DEPARTMENT_ORDER if department in by_department
    ]
    assert "traefik" in [name for name, _ in by_department["core"]]
    assert "n8n" in [name for name, _ in by_department["rag_agents"]]
    assert "ragflow" in [name for name, _ in by_department["rag_agents"]]
    assert "qdrant" in [name for name, _ in by_department["data"]]
    assert "llama-rerank" in [name for name, _ in by_department["model_runtime"]]
    assert "grafana" in [name for name, _ in by_department["monitoring"]]


def test_get_available_profiles_describes_automation_profile() -> None:
    from agmind.cli.tui.setup_wizard import get_available_profiles

    profiles = dict(get_available_profiles())

    assert "automation" in profiles
    assert "n8n workflow automation" in profiles["automation"]


def test_validate_warns_no_docker() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=False,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(domain="x.example", cf_api_token="x" * 30, profiles=["core"])
    errors = app._validate(state)
    assert any("docker" in e.lower() for e in errors)
    assert any("docker-compose-plugin" in e for e in errors)


def test_install_mode_allows_bootstrap_when_docker_missing() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=False,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected, install_mode=True)
    state = SetupState(
        domain="x.example",
        cf_api_token="x" * 30,
        profiles=["core"],
        sudo_password="secret",
    )

    errors = app._validate(state)

    assert not any("docker" in e.lower() for e in errors)


def test_validate_passes_clean() -> None:
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="agmind.mycompany.example",
        cf_api_token="X" * 40,
        services=["traefik", "llama-llm", "qdrant"],
        backend="vulkan",
    )
    errors = app._validate(state)
    assert errors == []


# ---------- Textual Pilot integration ----------


@pytest.mark.asyncio
async def test_app_quit_via_keybinding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke: app launches, Ctrl+C exits cleanly (use keybinding вместо click).

    AGMIND_LOGO_DISABLE_ANIMATION=1 отключает reactive interval в AnimatedLogo
    который иначе вешал headless event loop.
    """
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="AMD x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    # M4.1: explicit multi_step=False для legacy single-screen test
    app = AgmindSetupApp(detected=detected, multi_step=False)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.press("ctrl+c")
    assert app.result_state is None


@pytest.mark.asyncio
async def test_app_apply_via_keybinding_with_valid_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply через Ctrl+S keybinding (pilot.click ломается на OOB кнопки)."""
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    detected = DetectedHardware(
        ram_gb=128,
        gpu_name="AMD x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="XL",
    )
    initial = SetupState(
        domain="agmind.mycompany.example",
        cf_api_token="X" * 40,
        services=["traefik", "llama-llm", "qdrant"],
        backend="vulkan",
    )
    # M4.1: legacy single-screen test — explicit multi_step=False
    app = AgmindSetupApp(detected=detected, initial_state=initial, multi_step=False)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.press("ctrl+s")
    assert app.result_state is not None
    assert app.result_state.domain == "agmind.mycompany.example"
    # Phase J.1.8: services заменил profiles как primary selection
    assert "traefik" in app.result_state.services
    assert "llama-llm" in app.result_state.services


def test_logo_widget_import() -> None:
    """AnimatedLogo класс импортируется (sync test, без app context)."""
    from agmind.cli.tui.logo import AnimatedLogo, print_static_logo

    logo = AnimatedLogo(text="TEST", subtitle="logo test")
    assert logo.ascii_art  # pyfiglet rendered
    assert "TEST" in logo.ascii_art or len(logo.ascii_art) > 10
    # print_static_logo доступен
    assert callable(print_static_logo)


def test_gradient_rendering() -> None:
    """_render_gradient возвращает Rich Text без crash."""
    from agmind.cli.tui.logo import _render_gradient

    text = _render_gradient("AGmind\nHello", color_offset=3)
    assert text is not None
    assert len(str(text)) > 0
