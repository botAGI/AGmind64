"""Phase H'.D: tests that observability configs are syntactically valid.

Не запускаем сами сервисы (нет docker в test env), но валидируем YAML structure
и semantic invariants (есть нужные scrape jobs, alert rules, datasources).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parent.parent
OBS_DIR = REPO_ROOT / "templates" / "observability"


# ---- existence / structure ----

def test_observability_dir_exists() -> None:
    assert OBS_DIR.exists()


def test_expected_files_present() -> None:
    expected = [
        "prometheus.yml",
        "prometheus/rules/llama.yml",
        "prometheus/rules/system.yml",
        "alertmanager.yml",
        "alloy/config.alloy",
        "loki/loki.yml",
        "grafana/provisioning/datasources/agmind.yml",
        "grafana/provisioning/dashboards/dashboards.yml",
    ]
    for rel in expected:
        assert (OBS_DIR / rel).exists(), f"missing {rel}"


# ---- prometheus.yml ----

@pytest.fixture(scope="module")
def prometheus_config() -> dict[str, object]:
    return yaml.safe_load((OBS_DIR / "prometheus.yml").read_text(encoding="utf-8"))


def test_prometheus_has_docker_sd_job(prometheus_config: dict[str, object]) -> None:
    """docker_sd_configs job существует — это краеугольный камень auto-discovery."""
    jobs = prometheus_config["scrape_configs"]  # type: ignore[index]
    docker_sd_jobs = [
        j for j in jobs if any("docker_sd_configs" in j for j in [list(j.keys())])  # noqa
    ]
    # Простая проверка: есть job с docker_sd_configs
    has_docker_sd = any("docker_sd_configs" in j for j in jobs)  # type: ignore[index, union-attr]
    assert has_docker_sd, "no docker_sd_configs job — auto-discovery broken"


def test_prometheus_whitelist_relabel(prometheus_config: dict[str, object]) -> None:
    """Whitelist через prometheus.scrape=true — критично для security."""
    jobs = prometheus_config["scrape_configs"]  # type: ignore[index]
    docker_job = next(j for j in jobs if "docker_sd_configs" in j)  # type: ignore[union-attr]
    relabel = docker_job.get("relabel_configs", [])  # type: ignore[union-attr]
    # Должен быть rule action=keep на prometheus.scrape
    keep_rules = [r for r in relabel if r.get("action") == "keep"]
    assert keep_rules, "no whitelist 'keep' rule — Prometheus заскрейпит всё"


def test_prometheus_rules_files_configured(prometheus_config: dict[str, object]) -> None:
    assert "rule_files" in prometheus_config


def test_prometheus_external_labels(prometheus_config: dict[str, object]) -> None:
    labels = prometheus_config["global"]["external_labels"]  # type: ignore[index]
    assert labels["cluster"] == "agmind"


# ---- prometheus rules ----

def test_llama_alerts_present() -> None:
    rules = yaml.safe_load((OBS_DIR / "prometheus/rules/llama.yml").read_text(encoding="utf-8"))
    alert_groups = [g for g in rules["groups"] if g.get("name") == "llama_alerts"]
    assert alert_groups, "no llama_alerts group"
    alerts = {r["alert"] for r in alert_groups[0]["rules"] if "alert" in r}
    # Must-have alerts
    assert "LlamaServerDown" in alerts
    assert "LlamaKvCacheNearFull" in alerts
    assert "LlamaQueueBuildup" in alerts


def test_system_alerts_present() -> None:
    rules = yaml.safe_load((OBS_DIR / "prometheus/rules/system.yml").read_text(encoding="utf-8"))
    alerts = {r["alert"] for g in rules["groups"] for r in g["rules"] if "alert" in r}
    assert "HostOomKilled" in alerts
    assert "ContainerRestartLoop" in alerts
    assert "AmdGpuTempHigh" in alerts
    assert "AmdGttUsageHigh" in alerts


def test_amd_gpu_alerts_use_textfile_metrics() -> None:
    """GPU alerts должны ссылаться на наши textfile-collector metrics (R13)."""
    rules_text = (OBS_DIR / "prometheus/rules/system.yml").read_text(encoding="utf-8")
    assert "amdgpu_temp_edge_celsius" in rules_text
    assert "amdgpu_gtt_used_bytes" in rules_text


# ---- alertmanager ----

@pytest.fixture(scope="module")
def alertmanager_config() -> dict[str, object]:
    return yaml.safe_load((OBS_DIR / "alertmanager.yml").read_text(encoding="utf-8"))


def test_alertmanager_telegram_configured(alertmanager_config: dict[str, object]) -> None:
    receivers = alertmanager_config["receivers"]  # type: ignore[index]
    for r in receivers:  # type: ignore[union-attr]
        if "telegram_configs" in r:
            tg = r["telegram_configs"][0]
            assert "bot_token_file" in tg, "bot_token_file required for secret mount"
            return
    pytest.fail("no telegram_configs receiver — alerts won't reach Telegram")


def test_alertmanager_critical_route(alertmanager_config: dict[str, object]) -> None:
    """Critical severity должен иметь отдельный route с быстрым repeat."""
    root_route = alertmanager_config["route"]  # type: ignore[index]
    subroutes = root_route.get("routes", [])  # type: ignore[union-attr]
    critical_routes = [
        r for r in subroutes
        if any(
            "severity" in m and "critical" in m
            for m in r.get("matchers", [])
        )
    ]
    assert critical_routes, "no critical severity route"


# ---- alloy ----

def test_alloy_config_has_docker_discovery() -> None:
    text = (OBS_DIR / "alloy/config.alloy").read_text(encoding="utf-8")
    assert 'discovery.docker "containers"' in text
    assert "loki.scrape" in text  # whitelist label


def test_alloy_propagates_agmind_labels() -> None:
    text = (OBS_DIR / "alloy/config.alloy").read_text(encoding="utf-8")
    for label in ("agmind_service", "agmind_tier", "agmind_owner"):
        assert label in text, f"alloy не пропагирует label {label}"


# ---- loki ----

def test_loki_retention_14d() -> None:
    cfg = yaml.safe_load((OBS_DIR / "loki/loki.yml").read_text(encoding="utf-8"))
    assert cfg["limits_config"]["retention_period"] == "14d"


def test_loki_structured_metadata_enabled() -> None:
    """Structured metadata (Loki v3) — критично для нашего trace_id propagation."""
    cfg = yaml.safe_load((OBS_DIR / "loki/loki.yml").read_text(encoding="utf-8"))
    assert cfg["limits_config"]["allow_structured_metadata"] is True


# ---- grafana ----

def test_grafana_datasources_minimal_set() -> None:
    cfg = yaml.safe_load(
        (OBS_DIR / "grafana/provisioning/datasources/agmind.yml").read_text(encoding="utf-8")
    )
    names = {ds["name"] for ds in cfg["datasources"]}
    assert names >= {"Prometheus", "Loki", "Alertmanager"}


def test_grafana_dashboard_provider_configured() -> None:
    cfg = yaml.safe_load(
        (OBS_DIR / "grafana/provisioning/dashboards/dashboards.yml").read_text(encoding="utf-8")
    )
    assert cfg["providers"][0]["folder"] == "AGmind"


# ---- gfx1151 textfile collector script ----

def test_amdgpu_textfile_script_exists() -> None:
    path = REPO_ROOT / "scripts" / "amdgpu_textfile.sh"
    assert path.exists()
    # Executable
    assert path.stat().st_mode & 0o111, f"{path} не executable"


def test_amdgpu_script_uses_lc_all_c() -> None:
    """LC_ALL=C — критично, иначе awk пишет запятые вместо точек (русская локаль)."""
    text = (REPO_ROOT / "scripts" / "amdgpu_textfile.sh").read_text(encoding="utf-8")
    assert "LC_ALL=C awk" in text, "decimal separator bug — нужен LC_ALL=C"


def test_amdgpu_script_targets_correct_metrics() -> None:
    """Script экспортит критичные для Strix Halo gfx1151 метрики."""
    text = (REPO_ROOT / "scripts" / "amdgpu_textfile.sh").read_text(encoding="utf-8")
    for metric in (
        "amdgpu_temp_edge_celsius",
        "amdgpu_gtt_used_bytes",  # критично для unified memory
        "amdgpu_vram_used_bytes",
        "amdgpu_gpu_busy_percent",
        "amdgpu_sclk_hz",  # для clock stuck detection
    ):
        assert metric in text, f"{metric} missing в textfile collector"
