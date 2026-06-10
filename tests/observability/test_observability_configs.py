"""Phase H'.D: tests that observability configs are syntactically valid.

Не запускаем сами сервисы (нет docker в test env), но валидируем YAML structure
и semantic invariants (есть нужные scrape jobs, alert rules, datasources).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]
OBS_DIR = REPO_ROOT / "templates" / "observability"


# ---- existence / structure ----


def test_observability_dir_exists() -> None:
    assert OBS_DIR.exists()


# ---- Wave 5 correctness fixes (review observability findings) ----


def test_netdata_does_not_advertise_a_broken_prometheus_scrape() -> None:
    """Review MEDIUM netdata-scrape-not-prometheus: netdata's allmetrics defaults to
    format=shell, so prometheus_scrape would scrape a non-Prometheus body — it is dropped."""
    netdata = load_descriptors()["netdata"]
    assert netdata.observability.prometheus_scrape is False


def test_alertmanager_has_no_dead_hostdown_inhibit_rule() -> None:
    """Review LOW alertmanager-inhibit-hostdown-missing: no alert emits HostDown and
    equal:[instance] can't correlate it — the dead inhibit rule is removed."""
    text = (OBS_DIR / "alertmanager.yml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    assert not cfg.get("inhibit_rules"), "dead HostDown inhibit rule must be gone"
    # No active matcher referencing the non-existent HostDown alert (a comment may mention it).
    assert 'alertname="HostDown"' not in text


def test_containers_dashboard_uses_real_restart_metric() -> None:
    """Review LOW grafana-container-restart-count: cAdvisor exports no container_restart_count;
    restarts are derived from changes(container_start_time_seconds[...])."""
    text = (
        OBS_DIR / "grafana" / "provisioning" / "dashboards" / "json" / "containers.json"
    ).read_text(encoding="utf-8")
    assert "container_restart_count" not in text
    assert "changes(container_start_time_seconds" in text


def test_inference_dashboard_exposes_decode_speed_tokens_per_second() -> None:
    """Operator-visible tok/s: the chat UI reports per-request decode speed (e.g. 215 tok/s) =
    rate(tokens_predicted_total) / rate(tokens_predicted_seconds_total) — NOT the wall-time
    rate(tokens_predicted_total) the 'Throughput' panel shows. The Inference board must surface it."""
    dash = json.loads(
        (OBS_DIR / "grafana" / "provisioning" / "dashboards" / "json" / "inference.json").read_text(
            encoding="utf-8"
        )
    )
    exprs = [t.get("expr", "") for p in dash["panels"] for t in p.get("targets", [])]
    assert any(
        "tokens_predicted_total" in e and "tokens_predicted_seconds_total" in e for e in exprs
    ), (
        "inference dashboard must expose decode speed (tokens_predicted_total / tokens_predicted_seconds_total)"
    )
    titles = {p.get("title", "").lower() for p in dash["panels"]}
    assert any("tok/s" in t and "decode" in t for t in titles), (
        "needs a labelled decode tok/s panel"
    )


def test_overview_dashboard_surfaces_llm_inference_tokens_per_second() -> None:
    """The main monitoring board (overview) must show LLM inference tok/s, so the operator sees
    generation activity and speed without opening the dedicated Inference dashboard."""
    dash = json.loads(
        (OBS_DIR / "grafana" / "provisioning" / "dashboards" / "json" / "overview.json").read_text(
            encoding="utf-8"
        )
    )
    exprs = [t.get("expr", "") for p in dash["panels"] for t in p.get("targets", [])]
    assert any(
        "tokens_predicted_total" in e and "tokens_predicted_seconds_total" in e for e in exprs
    ), "overview dashboard must surface LLM decode tok/s"


def test_expected_files_present() -> None:
    expected = [
        "prometheus.yml",
        "prometheus/rules/llama.yml",
        "prometheus/rules/system.yml",
        "prometheus/rules/services.yml",
        "alertmanager.yml",
        "alloy/config.alloy",
        "loki/loki.yml",
        "grafana/provisioning/datasources/agmind.yml",
        "grafana/provisioning/dashboards/dashboards.yml",
    ]
    for rel in expected:
        assert (OBS_DIR / rel).exists(), f"missing {rel}"


def test_observability_service_mounts_match_template_layout() -> None:
    descriptors = load_descriptors()

    grafana = descriptors["grafana"]
    assert "/etc/agmind/grafana/provisioning:/etc/grafana/provisioning:ro" in grafana.volumes

    loki = descriptors["loki"]
    assert loki.command == ["-config.file=/etc/loki/loki.yml"]

    alloy = descriptors["alloy"]
    assert "/var/lib/agmind/alloy:/var/lib/alloy/data" in alloy.volumes
    assert alloy.command == [
        "run",
        "--server.http.listen-addr=0.0.0.0:12345",
        "--storage.path=/var/lib/alloy/data",
        "/etc/alloy/config.alloy",
    ]


# ---- prometheus.yml ----


@pytest.fixture(scope="module")
def prometheus_config() -> dict[str, object]:
    return yaml.safe_load((OBS_DIR / "prometheus.yml").read_text(encoding="utf-8"))


def test_prometheus_has_docker_sd_job(prometheus_config: dict[str, object]) -> None:
    """docker_sd_configs job существует — это краеугольный камень auto-discovery."""
    jobs = prometheus_config["scrape_configs"]  # type: ignore[index]
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


def test_service_exporter_alerts_present() -> None:
    """redis + postgres exporter/backend alerts using OUR docker_sd label model."""
    path = OBS_DIR / "prometheus/rules/services.yml"
    rules = yaml.safe_load(path.read_text(encoding="utf-8"))
    alerts = {r["alert"]: r for g in rules["groups"] for r in g["rules"] if "alert" in r}
    for name in ("RedisExporterDown", "RedisDown", "PostgresExporterDown", "PostgresDown"):
        assert name in alerts, f"missing {name}"
        assert alerts[name]["expr"], f"{name} has empty expr"
        assert alerts[name]["labels"]["severity"] in ("critical", "warning")
    # backend-down alerts are critical; exporter-unreachable is warning.
    assert alerts["RedisDown"]["labels"]["severity"] == "critical"
    assert alerts["PostgresDown"]["labels"]["severity"] == "critical"
    # Single docker-auto scrape job → key on {service=...}, NEVER {job=...}.
    text = path.read_text(encoding="utf-8")
    assert 'service="redis-exporter"' in text
    assert 'service="postgres-exporter"' in text
    assert "job=" not in text, "our topology has one docker-auto job; select on {service=...}"


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
        r
        for r in subroutes
        if any("severity" in m and "critical" in m for m in r.get("matchers", []))
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
    provider = cfg["providers"][0]
    assert provider["folder"] == "AGmind"
    # Provider path must live INSIDE the RO provisioning mount that the install materializes,
    # not /var/lib/grafana/dashboards (a separate writable mount nothing populated → empty).
    assert provider["options"]["path"] == "/etc/grafana/provisioning/dashboards/json"


_DASHBOARD_DIR = OBS_DIR / "grafana/provisioning/dashboards/json"


def test_dashboards_shipped_and_parse() -> None:
    files = sorted(_DASHBOARD_DIR.glob("*.json"))
    assert files, "no dashboard JSON shipped under provisioning/dashboards/json"
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))  # raises on invalid JSON
        assert data.get("uid"), f"{f.name} missing uid"
        assert data.get("panels"), f"{f.name} has no panels"


def test_dashboards_reference_our_metrics_not_parent_stack() -> None:
    for f in _DASHBOARD_DIR.glob("*.json"):
        text = f.read_text(encoding="utf-8")
        # upstream-stack metric leftovers must not survive the re-point
        assert "agmind_gpu_" not in text, f"{f.name}: upstream GPU metric agmind_gpu_*"
        assert "vllm:" not in text, f"{f.name}: upstream inference metric vllm:*"
        assert '"uid": "${DS' not in text, f"{f.name}: unresolved datasource template var"
        assert '"uid": "Loki"' not in text, f"{f.name}: parent Loki uid (ours is lowercase 'loki')"


def test_gpu_and_inference_dashboards_use_our_metric_names() -> None:
    gpu = (_DASHBOARD_DIR / "gpu.json").read_text(encoding="utf-8")
    assert "amdgpu_gpu_busy_percent" in gpu and "amdgpu_temp_edge_celsius" in gpu
    inference = (_DASHBOARD_DIR / "inference.json").read_text(encoding="utf-8")
    assert "llamacpp:tokens_predicted_total" in inference
    assert "llamacpp:kv_cache_usage_ratio" in inference


# ---- gfx1151 textfile collector script ----


def test_amdgpu_textfile_script_exists() -> None:
    path = REPO_ROOT / "scripts" / "ops" / "amdgpu_textfile.sh"
    assert path.exists()
    # Executable
    assert path.stat().st_mode & 0o111, f"{path} не executable"


def test_amdgpu_script_uses_lc_all_c() -> None:
    """LC_ALL=C — критично, иначе awk пишет запятые вместо точек (русская локаль)."""
    text = (REPO_ROOT / "scripts" / "ops" / "amdgpu_textfile.sh").read_text(encoding="utf-8")
    assert "LC_ALL=C awk" in text, "decimal separator bug — нужен LC_ALL=C"


def test_amdgpu_script_targets_correct_metrics() -> None:
    """Script экспортит критичные для Strix Halo gfx1151 метрики."""
    text = (REPO_ROOT / "scripts" / "ops" / "amdgpu_textfile.sh").read_text(encoding="utf-8")
    for metric in (
        "amdgpu_temp_edge_celsius",
        "amdgpu_gtt_used_bytes",  # критично для unified memory
        "amdgpu_vram_used_bytes",
        "amdgpu_gpu_busy_percent",
        "amdgpu_sclk_hz",  # для clock stuck detection
    ):
        assert metric in text, f"{metric} missing в textfile collector"
