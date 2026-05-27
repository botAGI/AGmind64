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

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from agmind.schemas import ServiceDescriptor

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SERVICES_DIR = REPO_ROOT / "templates" / "services"

# Compose-spec без `version:` field — современный формат (2026).
# Docker Compose v2.0+ deprecated top-level `version:` (https://docs.docker.com/compose/compose-file/04-version-and-name/).
# Старый v3.9 был оставлен изначально, но docker compose выдаёт WARN.
# Set None чтобы НЕ писать `version:` в output (compose сам подхватит latest spec).
COMPOSE_VERSION: str | None = None

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
    return {name: d for name, d in descriptors.items() if set(d.profiles) & wanted}


def available_profiles(descriptors: dict[str, ServiceDescriptor]) -> set[str]:
    """Return profile names understood by the descriptor catalog."""
    profiles = {profile for descriptor in descriptors.values() for profile in descriptor.profiles}
    profiles.add("full")
    return profiles


def unknown_profiles(
    descriptors: dict[str, ServiceDescriptor],
    profiles: list[str] | None,
) -> list[str]:
    """Return requested profile names that are absent from the descriptor catalog."""
    if not profiles:
        return []
    return sorted(set(profiles).difference(available_profiles(descriptors)))


def filter_by_services(
    descriptors: dict[str, ServiceDescriptor],
    services: list[str],
) -> dict[str, ServiceDescriptor]:
    """Keep only explicit named services (Phase J.1.8: per-service selection)."""
    wanted = set(services)
    missing = sorted(wanted.difference(descriptors))
    if missing:
        raise ValueError(f"Unknown services requested: {', '.join(missing)}")
    return {name: d for name, d in descriptors.items() if name in wanted}


def select_services(
    descriptors: dict[str, ServiceDescriptor],
    profiles: list[str] | None = None,
    services: list[str] | None = None,
) -> dict[str, ServiceDescriptor]:
    """Combined selection: explicit `services` list takes precedence over `profiles`.

    Usage:
        select_services(d, services=["traefik", "llama-llm", "qdrant"])
        select_services(d, profiles=["core", "observability"])
    """
    if services is not None:
        return filter_by_services(descriptors, services)
    return filter_by_profile(descriptors, profiles or [])


def check_missing_dependencies(
    selected: dict[str, ServiceDescriptor],
    all_descriptors: dict[str, ServiceDescriptor],
) -> dict[str, list[str]]:
    """Return {service_name: [missing_dep_names]} — depends_on которые не выбраны.

    Используется TUI чтобы warn user'а если он выбрал dify-api без postgres.
    """
    selected_names = set(selected.keys())
    missing: dict[str, list[str]] = {}
    for name, d in selected.items():
        gaps = [dep for dep in d.depends_on if dep not in selected_names]
        if gaps:
            missing[name] = gaps
    return missing


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
        labels[f"traefik.http.services.{name}.loadbalancer.responseforwarding.flushinterval"] = (
            "1ms"
        )
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
    selected_descriptors: Mapping[str, ServiceDescriptor] | None = None,
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
        svc["depends_on"] = render_depends_on(d, selected_descriptors or {})
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


def render_depends_on(
    d: ServiceDescriptor,
    selected_descriptors: Mapping[str, ServiceDescriptor],
) -> dict[str, dict[str, bool | str]]:
    """Render Compose long-syntax dependency gates for a descriptor."""
    depends_on: dict[str, dict[str, bool | str]] = {}
    for dependency in d.depends_on:
        dependency_descriptor = selected_descriptors.get(dependency)
        condition = (
            "service_healthy"
            if dependency_descriptor and dependency_descriptor.health
            else "service_started"
        )
        depends_on[dependency] = {
            "condition": condition,
            "restart": True,
        }
    return depends_on


def inject_capability_env(
    selected: dict[str, ServiceDescriptor],
) -> dict[str, dict[str, str]]:
    """Phase O.B: compute env vars to add to each consumer based on capability bindings.

    Walks consumers (services with .consumes non-empty), resolves who provides
    each capability among selected services, and looks up BINDINGS table.

    Returns: {consumer_name: {ENV_VAR: value}} — to merge into descriptor env.
    Consumers without resolvable provider или no binding entry → empty dict.
    """
    from agmind.services.capability_bindings import env_for_consumer
    from agmind.services.compatibility import resolve_capability_provider_for_consumer

    extra_env: dict[str, dict[str, str]] = {}
    for name, d in selected.items():
        if not d.consumes:
            continue
        consumer_env: dict[str, str] = {}
        for cap in d.consumes:
            provider = resolve_capability_provider_for_consumer(selected, cap, name)
            if provider is None:
                continue
            consumer_env.update(env_for_consumer(cap, provider, name))
        if consumer_env:
            extra_env[name] = consumer_env
    return extra_env


def descriptors_with_capability_env(
    descriptors: list[ServiceDescriptor],
) -> list[ServiceDescriptor]:
    """Return descriptor copies with capability env merged into consumers.

    Descriptor-authored env wins over injected defaults, matching Docker Compose
    renderer behavior and keeping manual overrides possible.
    """
    selected_map = {d.name: d for d in descriptors}
    capability_env = inject_capability_env(selected_map)
    resolved: list[ServiceDescriptor] = []
    for descriptor in descriptors:
        extra = capability_env.get(descriptor.name)
        if not extra:
            resolved.append(descriptor)
            continue
        env = {**extra, **descriptor.env}
        resolved.append(descriptor.model_copy(update={"env": env}))
    return resolved


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
    resolved_descriptors = descriptors_with_capability_env(descriptors)

    services_block_local: dict[str, Any] = {}
    selected_by_name = {descriptor.name: descriptor for descriptor in resolved_descriptors}
    for d in sorted(resolved_descriptors, key=lambda x: x.name):
        svc = descriptor_to_compose_service(d, traefik_enabled, selected_by_name)
        services_block_local[d.name] = svc
    services_block = services_block_local
    compose: dict[str, Any] = {
        "services": services_block,
        "networks": {
            "default": {
                "name": network_name,
                "driver": "bridge",
            }
        },
    }
    if COMPOSE_VERSION is not None:
        # Legacy compatibility — современный compose не требует version
        compose = {"version": COMPOSE_VERSION, **compose}
    return compose


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
    profiles: list[str] | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    traefik_enabled: bool = True,
    domain: str | None = None,
    services: list[str] | None = None,
) -> str:
    """End-to-end: load + filter + render + serialize. Возвращает финальный YAML.

    Args:
        profiles: list profile names to include (high-level filter)
        services: explicit service names (per-service selection, takes precedence)
        services_dir: где искать service descriptors
        traefik_enabled: добавлять Traefik routing labels из routing config
        domain: если задан — sed-replace `agmind.dev` placeholder на этот домен
    """
    descriptors = load_descriptors(services_dir)
    if services is not None:
        missing = sorted(set(services).difference(descriptors))
        if missing:
            raise ValueError(f"Unknown services requested: {', '.join(missing)}")
    if services is None:
        missing_profiles = unknown_profiles(descriptors, profiles)
        if missing_profiles:
            raise ValueError(f"Unknown profiles requested: {', '.join(missing_profiles)}")
    selected = select_services(descriptors, profiles=profiles, services=services)
    if not selected:
        raise ValueError(f"No services match: profiles={profiles}, services={services}")
    missing_dependencies = check_missing_dependencies(selected, descriptors)
    if missing_dependencies:
        details = "; ".join(
            f"{name} requires {', '.join(deps)}"
            for name, deps in sorted(missing_dependencies.items())
        )
        raise ValueError(f"Missing dependencies for selected services: {details}")
    compose = render_compose(list(selected.values()), traefik_enabled=traefik_enabled)
    rendered = to_yaml(compose)
    if domain and domain != "agmind.dev":
        rendered = rendered.replace("agmind.dev", domain)
    return rendered
