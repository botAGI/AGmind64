"""Phase M3.S.2: tests for multi-step wizard screens."""

from __future__ import annotations

import pytest

from agmind.cli.tui.setup_wizard import (
    AgmindSetupApp,
    DetectedHardware,
    SetupState,
)

pytestmark = pytest.mark.backend_any


def _detected() -> DetectedHardware:
    return DetectedHardware(
        ram_gb=128.0,
        gpu_name="x",
        is_strix_halo=True,
        vulkan_present=True,
        rocm_present=True,
        docker_present=True,
        recommended_tier="full",
    )


def test_multistep_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4.1: multi-step теперь DEFAULT."""
    for k in ("AGMIND_WIZARD_LEGACY", "AGMIND_WIZARD_MULTISTEP"):
        monkeypatch.delenv(k, raising=False)
    app = AgmindSetupApp(detected=_detected())
    assert app.multi_step is True


def test_legacy_env_forces_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGMIND_WIZARD_LEGACY=1 → single-screen (M4.1 escape hatch)."""
    monkeypatch.setenv("AGMIND_WIZARD_LEGACY", "1")
    monkeypatch.delenv("AGMIND_WIZARD_MULTISTEP", raising=False)
    app = AgmindSetupApp(detected=_detected())
    assert app.multi_step is False


def test_legacy_multistep_zero_also_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compat: старый AGMIND_WIZARD_MULTISTEP=0 продолжает работать."""
    monkeypatch.setenv("AGMIND_WIZARD_MULTISTEP", "0")
    monkeypatch.delenv("AGMIND_WIZARD_LEGACY", raising=False)
    app = AgmindSetupApp(detected=_detected())
    assert app.multi_step is False


def test_multistep_via_kwarg_true() -> None:
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    assert app.multi_step is True


def test_multistep_via_kwarg_false() -> None:
    app = AgmindSetupApp(detected=_detected(), multi_step=False)
    assert app.multi_step is False


def test_kwarg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_WIZARD_LEGACY", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    assert app.multi_step is True


def test_install_mode_requires_sudo_inside_wizard() -> None:
    app = AgmindSetupApp(detected=_detected(), install_mode=True)

    assert app.install_mode is True
    assert app.require_sudo_password is True


# ---- Pilot tests (Pilot + AGMIND_LOGO_DISABLE_ANIMATION=1) ----


@pytest.mark.asyncio
async def test_multistep_pushes_domain_screen_on_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On_mount push'ит DomainScreen в multi-step mode."""
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        # Top of stack должен быть DomainScreen
        from agmind.cli.tui.wizard_screens import DomainScreen

        assert isinstance(app.screen, DomainScreen)


@pytest.mark.asyncio
async def test_multistep_domain_screen_has_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        domain_input = app.screen.query_one("#domain-input")
        token_input = app.screen.query_one("#cf-token-input")
        assert domain_input is not None
        assert token_input is not None


@pytest.mark.asyncio
async def test_multistep_submit_uses_saved_state_without_root_form_widgets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """Confirm/Apply in multi-step mode must use app.state, not root form widgets."""
    from pathlib import Path

    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    monkeypatch.setattr(
        "agmind.cli.tui.setup_wizard.STATE_PATH",
        Path(tmp_path) / "setup-state.json",
    )
    monkeypatch.setattr(
        "agmind.cli.tui.setup_wizard.TOKEN_PATH",
        Path(tmp_path) / "cf_dns_api_token",
    )
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["dify-api", "milvus", "llama-llm", "llama-embed", "llama-rerank"],
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=False)

    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        app.action_submit()
        await pilot.pause(0.1)

    assert app.result_state is not None
    assert "dify-web" in app.result_state.services
    assert "postgres" in app.result_state.services
    assert "qdrant" not in app.result_state.services
    assert (Path(tmp_path) / "setup-state.json").exists()
    token_path = Path(tmp_path) / "cf_dns_api_token"
    assert token_path.exists()
    assert token_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_multistep_submit_removes_token_file_on_chmod_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    from pathlib import Path

    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    token_path = Path(tmp_path) / "cf_dns_api_token"
    monkeypatch.setattr(
        "agmind.cli.tui.setup_wizard.STATE_PATH",
        Path(tmp_path) / "setup-state.json",
    )
    monkeypatch.setattr(
        "agmind.cli.tui.setup_wizard.TOKEN_PATH",
        token_path,
    )
    original_chmod = Path.chmod

    def fail_token_chmod(path: Path, mode: int, *args: object, **kwargs: object) -> None:
        if "cf_dns_api_token" in path.name:
            raise PermissionError("chmod denied")
        original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "chmod", fail_token_chmod)
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=["traefik", "llama-llm"],
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=False)

    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        app.action_submit()
        await pilot.pause(0.1)

    assert app.result_state is None
    assert not token_path.exists()
    assert not any(Path(tmp_path).glob("*cf_dns_api_token*"))


@pytest.mark.asyncio
async def test_multistep_navigate_domain_to_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        from agmind.cli.tui.wizard_screens import DomainScreen, ModelScreen

        assert isinstance(app.screen, DomainScreen)
        # Press Next button через keypress alt+n
        await pilot.press("alt+n")
        await pilot.pause(0.1)
        # Top should now be ModelScreen
        assert isinstance(app.screen, ModelScreen)


@pytest.mark.asyncio
async def test_multistep_back_button_returns_to_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(domain="lab.example.com", cf_api_token="X" * 40)
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Domain → Model
        await pilot.pause(0.1)
        await pilot.press("alt+b")  # Model → Domain
        await pilot.pause(0.1)
        from agmind.cli.tui.wizard_screens import DomainScreen

        assert isinstance(app.screen, DomainScreen)


# ---- M5.3 polish + M5.4 cluster integration ----


def test_setup_state_has_embed_rerank_defaults() -> None:
    """M5.1: SetupState dataclass exposes embed/rerank fields с разумными defaults."""
    state = SetupState()
    assert state.embed_model_id == "bge-m3-q8"
    assert state.embed_ctx_size == 8192
    assert state.embed_kv_cache == "f16"
    assert state.embed_parallel == 4
    assert state.rerank_model_id == "bge-reranker-v2-m3-q8"
    assert state.rerank_ctx_size == 2048
    assert state.cluster_replicate is False


def test_setup_state_resolve_embed_repo_file_from_catalog() -> None:
    """resolve_embed_repo_file() resolves curated id → (repo, file) pair."""
    state = SetupState(embed_model_id="bge-m3-q8")
    repo, file_ = state.resolve_embed_repo_file()
    assert repo == "lm-kit/bge-m3-gguf"
    assert file_ == "bge-m3-Q8_0.gguf"


def test_setup_state_resolve_rerank_returns_custom_raw() -> None:
    state = SetupState(rerank_model_id="custom", rerank_repo="r", rerank_file="f.gguf")
    assert state.resolve_rerank_repo_file() == ("r", "f.gguf")


def test_setup_state_resolve_rerank_repo_file_from_catalog() -> None:
    state = SetupState(rerank_model_id="bge-reranker-v2-m3-q8")
    repo, file_ = state.resolve_rerank_repo_file()
    assert repo == "gpustack/bge-reranker-v2-m3-GGUF"
    assert file_ == "bge-reranker-v2-m3-Q8_0.gguf"


def test_cluster_peers_cached_empty_when_zeroconf_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M5.4: cluster_peers property gracefully handles import errors / no peers."""
    monkeypatch.setattr(
        "agmind.cluster.detect.discover",
        lambda timeout=0.5, exclude_self=True: [],
    )
    app = AgmindSetupApp(detected=_detected())
    peers = app.cluster_peers
    assert peers == []
    # Second read should reuse cache (mock не вызовется снова — но empty list тот же)
    assert app.cluster_peers is peers


def test_hardware_panel_formats_strix_halo_fields() -> None:
    """M5.3.2: _format_hardware_panel renders detected GPU + tier."""
    from agmind.cli.tui.wizard_screens import _format_hardware_panel

    panel = _format_hardware_panel(_detected())
    assert "DETECTED HARDWARE" in panel
    assert "Strix Halo" in panel
    assert "full" in panel  # recommended_tier


def test_cluster_peers_banner_formats_n_peers() -> None:
    from agmind.cli.tui.wizard_screens import _format_cluster_peers_banner

    banner = _format_cluster_peers_banner([("host1", "10.0.0.2"), ("host2", "10.0.0.3")])
    assert "CLUSTER PEERS DETECTED" in banner
    assert "host1" in banner and "host2" in banner
    # 0 peers — пустая строка
    assert _format_cluster_peers_banner([]) == ""


@pytest.mark.asyncio
async def test_services_empty_banner_renders_when_no_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M5.3.4: empty-state banner появляется когда services=[]."""
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=[],
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Domain → Model
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Model → Services
        await pilot.pause(0.1)
        from textual.widgets import Static

        from agmind.cli.tui.wizard_screens import ServicesScreen

        assert isinstance(app.screen, ServicesScreen)
        banner = app.screen.query_one("#services-empty-banner", Static)
        # Banner shown — text contains "NO SERVICES SELECTED"
        rendered = str(banner.render() if callable(banner.render) else banner.renderable)
        assert "NO SERVICES SELECTED" in rendered or "no services" in rendered.lower()


@pytest.mark.asyncio
async def test_services_screen_walks_service_departments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=[],
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Domain -> Model
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Model -> Services / core
        await pilot.pause(0.1)

        from textual.widgets import Checkbox

        from agmind.cli.tui.wizard_screens import ServicesScreen

        assert isinstance(app.screen, ServicesScreen)
        assert app.screen.current_department_key == "core"
        assert app.screen.query_one("#svc-traefik", Checkbox) is not None

        await pilot.press("alt+n")  # core -> RAG/agents
        await pilot.pause(0.1)

        assert isinstance(app.screen, ServicesScreen)
        assert app.screen.current_department_key == "rag_agents"
        assert app.screen.query_one("#svc-dify_api", Checkbox) is not None


@pytest.mark.asyncio
async def test_services_screen_checking_dify_api_marks_component_closure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=[],
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Domain -> Model
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Model -> Services
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # core -> RAG/agents
        await pilot.pause(0.1)

        from textual.widgets import Checkbox

        app.screen.query_one("#svc-dify_api", Checkbox).value = True
        await pilot.pause(0.1)

        for service in (
            "dify_api",
            "dify_web",
            "dify_worker",
            "dify_plugin_daemon",
            "dify_sandbox",
        ):
            assert app.screen.query_one(f"#svc-{service}", Checkbox).value is True
        assert {
            "postgres",
            "redis",
            "qdrant",
            "llama-llm",
            "llama-embed",
        } <= set(app.state.services)
        assert app.screen.query_one("#svc-ragflow", Checkbox).value is False


@pytest.mark.asyncio
async def test_services_screen_milvus_replaces_qdrant_for_dify_but_not_ragflow_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=[],
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("alt+n")
        await pilot.pause(0.1)
        await pilot.press("alt+n")
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # core -> RAG/agents
        await pilot.pause(0.1)

        from textual.widgets import Checkbox

        app.screen.query_one("#svc-dify_api", Checkbox).value = True
        await pilot.pause(0.1)
        assert "qdrant" in app.state.services

        app.screen.query_one("#svc-ragflow", Checkbox).value = True
        await pilot.pause(0.1)
        assert "elasticsearch" in app.state.services

        await pilot.press("alt+n")  # RAG/agents -> Data
        await pilot.pause(0.1)
        assert app.screen.current_department_key == "data"

        app.screen.query_one("#svc-milvus", Checkbox).value = True
        await pilot.pause(0.1)

        assert app.screen.query_one("#svc-milvus", Checkbox).value is True
        assert app.screen.query_one("#svc-qdrant", Checkbox).value is False
        assert app.screen.query_one("#svc-elasticsearch", Checkbox).value is True
        assert "milvus" in app.state.services
        assert "qdrant" not in app.state.services
        assert "elasticsearch" in app.state.services


def test_confirm_summary_shows_retrieval_topology() -> None:
    from agmind.cli.tui.wizard_screens import ConfirmScreen

    state = SetupState(
        services=["dify-api", "ragflow", "milvus", "elasticsearch"],
        rerank_file="rr.gguf",
    )

    summary = ConfirmScreen()._summary(state)

    assert "RAG STORAGE PLAN" in summary
    assert "DIFY VECTOR DB ..... milvus" in summary
    assert "RAGFLOW DOC ENGINE . elasticsearch" in summary
    assert "Milvus applies to Dify only" in summary


def test_confirm_summary_shows_topology_warnings() -> None:
    from agmind.cli.tui.wizard_screens import ConfirmScreen

    state = SetupState(
        services=["dify-api", "milvus", "qdrant", "postgres", "redis"],
        rerank_file="rr.gguf",
    )

    summary = ConfirmScreen()._summary(state)

    assert "TOPOLOGY WARNINGS" in summary
    assert "Dify has multiple vector_db providers selected" in summary


@pytest.mark.asyncio
async def test_help_screen_open_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """M5.3.7: F1 на любом step показывает HelpScreen, Esc закрывает."""
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        from agmind.cli.tui.wizard_screens import DomainScreen, HelpScreen

        assert isinstance(app.screen, DomainScreen)
        await pilot.press("f1")
        await pilot.pause(0.1)
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert isinstance(app.screen, DomainScreen)
