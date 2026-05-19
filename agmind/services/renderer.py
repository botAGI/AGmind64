"""Phase H'.C: Python renderer для docker-compose.yml из ServiceDescriptor catalog.

Заменяет Ansible Jinja2 шаблон (`ansible/roles/services/templates/docker-compose.yml.j2`)
на типизированный Python код. Используется через `agmind render compose` CLI.

Контракт:
    descriptors = load_descriptors(SERVICES_DIR)
    selected = filter_by_profile(descriptors, ["core", "rag"])
    compose = render_compose(selected.values(), traefik_enabled=True)
    yaml_text = to_yaml(compose)

Что генерирует:
- compose v3.9 структура: services{} + networks{}
- Traefik docker provider labels из ServiceDescriptor.routing (если задано)
- Prometheus docker_sd labels из ServiceDescriptor.observability
- Loki Alloy labels (loki.scrape=true по умолчанию)
- AGmind metadata labels (agmind.service/tier/owner)
- Logging defaults: json-file 50m × 3 файла (no 100GB log files)
- Healthcheck в нативном compose формате
- Network `agmind` shared bridge

См. ADR-0006 (когда напишем).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agmind.schemas import ServiceDescriptor

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SERVICES_DIR = REPO_ROOT / "templates" / "services"

# Compose v3.9 — latest stable supported by docker compose v2.30+
COMPOSE_VERSION = "3.9"

# Logging defaults для предотвращения накопления GB логов
# (deep-dive 04 §10, deep-dive 03 §9)
DEFAULT_LOGGING = {
    "driver": "json-file",
    "options": {"max-size": "50m", "max-file": "3"},
}

# AGmind shared bridge network — все сервисы видят друг друга и Traefik.
DEFAULT_NETWORK_NAME = "agmind"


def load_descriptors(services_dir: Path = DEFAULT_SERVICES_DIR) -> dict[str, ServiceDescriptor]:
    """Load all `templates/services/*.yaml` → {name: ServiceDescriptor}.

    Сортирует по имени для детерминированного output (важно для diff/tests).
    """
    out: dict[str, ServiceDescriptor] = {}
    if not services_dir.exists():
        return out
    for path in sorted(services_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        descriptor = ServiceDescriptor.model_validate(data)
        if descriptor.name in out:
            raise ValueError(f"Duplicate service name '{descriptor.name}' in {path}")
        out[descriptor.name] = descriptor
    return out


def filter_by_profile(
    descriptors: dict[str, ServiceDescriptor],
    profiles: list[str],
) -> dict[str, ServiceDescriptor]:
    """Keep only services with at least one profile from `profiles`.

    Если profiles=["full"] — возвращает все сервисы (special-case).
    """
    if "full" in profiles:
        return dict(descriptors)
    wanted = set(profiles)
    return {
        name: d
        for name, d in descriptors.items()
        if set(d.profiles) & wanted
    }


def render_traefik_labels(d: ServiceDescriptor) -> dict[str, str]:
    """Generate Traefik docker provider labels from ServiceDescriptor.routing.

    Если routing=None — пустой dict (internal-only service).
    Если routing задан — полный набор labels для:
    - Host(...) router rule
    - websecure entrypoint + TLS via `le` cert resolver
    - middleware chain (chain-llm | chain-internal | chain-public from file provider)
    - healthcheck integration
    - SSE-safe settings если routing.sse=True (см. deep-dive 01 §1)
    """
    if d.routing is None:
        return {}

    name = d.name
    routing = d.routing
    labels: dict[str, str] = {
        "traefik.enable": "true",
        f"traefik.http.routers.{name}.rule": f"Host(`{routing.host}`)",
        f"traefik.http.routers.{name}.entrypoints": "websecure",
        f"traefik.http.routers.{name}.tls": "true",
        f"traefik.http.routers.{name}.tls.certresolver": "le",
        f"traefik.http.routers.{name}.middlewares": f"{routing.middleware_chain}@file",
    }

    # Port — берём container-side первого port mapping
    container_port = _first_container_port(d)
    if container_port:
        labels[f"traefik.http.services.{name}.loadbalancer.server.port"] = container_port

    # Healthcheck integration: Traefik исключит unhealthy из роутинга
    labels[f"traefik.http.services.{name}.loadbalancer.healthcheck.path"] = routing.healthcheck_path
    labels[f"traefik.http.services.{name}.loadbalancer.healthcheck.interval"] = "30s"
    labels[f"traefik.http.services.{name}.loadbalancer.passhostheader"] = "true"

    # SSE-safe: обязательно для llama-server streaming (deep-dive 01 §1)
    if routing.sse:
        labels[f"traefik.http.services.{name}.loadbalancer.responseforwarding.flushinterval"] = "1ms"
        labels[f"traefik.http.routers.{name}.tls.options"] = "no-http2@file"

    return labels


def render_observability_labels(d: ServiceDescriptor) -> dict[str, str]:
    """Generate Prometheus docker_sd + Alloy loki.scrape + AGmind metadata labels.

    Эти labels подхватываются:
    - prometheus.* → Prometheus docker_sd_configs (relabel_configs whitelist)
    - loki.* → Grafana Alloy discovery.docker (loki.source.docker target filter)
    - agmind.* → free-form metadata, доступны как labels в Prometheus/Loki queries
    """
    labels: dict[str, str] = {
        "agmind.service": d.name,
        "agmind.tier": d.tier,
        "agmind.owner": d.owner,
    }

    obs = d.observability
    if obs.prometheus_scrape:
        labels["prometheus.scrape"] = "true"
        labels["prometheus.path"] = obs.metrics_path
        # Port — explicit (metrics_port) or fallback to first container port
        port = obs.metrics_port
        if port is None:
            container_port = _first_container_port(d)
            if container_port:
                try:
                    port = int(container_port)
                except ValueError:
                    port = None
        if port is not None:
            labels["prometheus.port"] = str(port)

    if obs.loki_scrape:
        labels["loki.scrape"] = "true"

    return labels


def _first_container_port(d: ServiceDescriptor) -> str | None:
    """Extract container-side port из первого port mapping (для loadbalancer.server.port).

    Format: `[ip:]host:container` → возвращаем container часть.
    """
    if not d.ports:
        return None
    spec = d.ports[0]
    parts = spec.split(":")
    return parts[-1] if parts else None


def descriptor_to_compose_service(
    d: ServiceDescriptor,
    traefik_enabled: bool = True,
) -> dict[str, Any]:
    """Build single compose service definition (compose v3.9 format)."""
    svc: dict[str, Any] = {
        "image": d.fq_image(),
        "container_name": f"agmind-{d.name}",
        "restart": "unless-stopped",
    }

    if d.profiles:
        svc["profiles"] = list(d.profiles)
    if d.depends_on:
        svc["depends_on"] = list(d.depends_on)
    if d.resources.cpus is not None:
        svc["cpus"] = str(d.resources.cpus)
    if d.resources.mem_limit:
        svc["mem_limit"] = d.resources.mem_limit
    if d.ports:
        svc["ports"] = list(d.ports)
    if d.env:
        svc["environment"] = dict(d.env)
    if d.volumes:
        svc["volumes"] = list(d.volumes)

    if d.health is not None:
        hc: dict[str, Any] = {
            "test": list(d.health.test),
            "interval": d.health.interval,
            "timeout": d.health.timeout,
            "retries": d.health.retries,
        }
        if d.health.start_period:
            hc["start_period"] = d.health.start_period
        svc["healthcheck"] = hc

    # Compose-native runtime fields (compose-spec)
    if d.command:
        svc["command"] = list(d.command)
    if d.devices:
        svc["devices"] = list(d.devices)
    if d.group_add:
        svc["group_add"] = list(d.group_add)
    if d.security_opt:
        svc["security_opt"] = list(d.security_opt)
    if d.cap_add:
        svc["cap_add"] = list(d.cap_add)

    # Logging defaults для предотвращения log bloat
    svc["logging"] = DEFAULT_LOGGING

    # Combined labels: observability + traefik + agmind metadata
    labels = render_observability_labels(d)
    if traefik_enabled:
        labels.update(render_traefik_labels(d))
    if labels:
        svc["labels"] = labels

    return svc


def render_compose(
    descriptors: list[ServiceDescriptor],
    traefik_enabled: bool = True,
    network_name: str = DEFAULT_NETWORK_NAME,
) -> dict[str, Any]:
    """Build full docker-compose.yml structure as Python dict.

    Args:
        descriptors: services to include (отсортированы по имени для детерминизма)
        traefik_enabled: добавлять Traefik labels из routing config
        network_name: имя shared bridge сети
    """
    services_block = {
        d.name: descriptor_to_compose_service(d, traefik_enabled)
        for d in sorted(descriptors, key=lambda x: x.name)
    }
    return {
        "version": COMPOSE_VERSION,
        "services": services_block,
        "networks": {
            "default": {
                "name": network_name,
                "driver": "bridge",
            }
        },
    }


def to_yaml(compose: dict[str, Any]) -> str:
    """Serialize compose dict to YAML with stable ordering.

    Возвращает строку с auto-generated header.
    """
    header = (
        "# Auto-generated by `agmind render compose` from templates/services/*.yaml.\n"
        "# DO NOT EDIT BY HAND — изменения переписать в template files и регенерировать.\n"
        "# См. agmind/services/renderer.py + ADR-0006.\n"
        "\n"
    )
    body = yaml.safe_dump(
        compose,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
        indent=2,
    )
    return header + body


def render_to_string(
    profiles: list[str],
    services_dir: Path = DEFAULT_SERVICES_DIR,
    traefik_enabled: bool = True,
    domain: str | None = None,
) -> str:
    """End-to-end: load + filter + render + serialize. Возвращает финальный YAML.

    Args:
        profiles: list profile names to include
        services_dir: где искать service descriptors
        traefik_enabled: добавлять Traefik routing labels из routing config
        domain: если задан — sed-replace `agmind.dev` placeholder на этот домен
            (для multi-user setup: каждый юзер вводит свой `agmind_domain` в Ansible
            vars_prompt, без правки источника). См. ADR-0006 + SETUP_CLOUDFLARE_DOMAIN.md.
    """
    descriptors = load_descriptors(services_dir)
    selected = filter_by_profile(descriptors, profiles)
    if not selected:
        raise ValueError(f"No services match profiles {profiles}")
    compose = render_compose(list(selected.values()), traefik_enabled=traefik_enabled)
    rendered = to_yaml(compose)
    if domain and domain != "agmind.dev":
        rendered = rendered.replace("agmind.dev", domain)
    return rendered
