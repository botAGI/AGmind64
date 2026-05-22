"""Phase H'.B: миграция templates/services.yaml → templates/services/<name>.yaml.

Читает существующий монолитный `services.yaml`, конвертирует каждый сервис в
Pydantic `ServiceDescriptor`, разносит по отдельным файлам с правильным
$schema header для VSCode autocomplete.

Идемпотентно: повторный запуск перезаписывает файлы (можно безопасно после
правок tier-mapping). Старый `services.yaml` НЕ удаляется — legacy рендерер
(Ansible Jinja2) пока его использует. Удаление в Phase H'.C.

Запуск:
    python -m scripts.migrate_services_to_descriptors [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from agmind.schemas import (
    HealthCheck,
    ObservabilityConfig,
    ResourceLimits,
    RoutingConfig,
    ServiceDescriptor,
)
from agmind.services.registry import Service, load_registry

if TYPE_CHECKING:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO_ROOT / "templates" / "services"


# Tier mapping. Источник: heuristic + roles из spec.
# - inference: model-serving only (llama-server, vLLM, Infinity)
# - app: AI applications over inference (Dify, RAGFlow, Open WebUI, Docling)
# - storage: databases, vector stores, blob stores
# - edge: reverse proxy, auth
# - ops: observability stack + exporters
TIER_MAP: dict[str, str] = {
    # inference
    "llama-llm": "inference",
    "llama-embed": "inference",
    "llama-rerank": "inference",
    # app
    "dify-api": "app",
    "dify-worker": "app",
    "dify-web": "app",
    "dify-plugin-daemon": "app",
    "dify-sandbox": "app",
    "docling": "app",
    "openwebui": "app",
    "ragflow": "app",
    # storage
    "qdrant": "storage",
    "weaviate": "storage",
    "milvus": "storage",
    "postgres": "storage",
    "redis": "storage",
    "mysql": "storage",
    "elasticsearch": "storage",
    "minio": "storage",
    # edge
    "nginx": "edge",
    "caddy": "edge",
    "authelia": "edge",
    # ops
    "prometheus": "ops",
    "grafana": "ops",
    "loki": "ops",
    "alloy": "ops",
    "cadvisor": "ops",
    "portainer": "ops",
    "alertmanager": "ops",
    "node-exporter": "ops",
    "postgres-exporter": "ops",
    "redis-exporter": "ops",
}


# Public services: routing config (Traefik labels, Phase H'.C).
# host = subdomain.agmind.dev (см. ADR-0006 + R15 — публичный домен через CF DNS-01).
# sse=True обязательно для streaming endpoints (llama-server, chat UIs).
ROUTING_MAP: dict[str, dict[str, object]] = {
    "llama-llm": {
        "host": "llama.agmind.dev",
        "middleware_chain": "chain-llm",
        "sse": True,
    },
    "llama-embed": {
        "host": "embed.agmind.dev",
        "middleware_chain": "chain-llm",
        "sse": False,  # embeddings не стримятся
    },
    "llama-rerank": {
        "host": "rerank.agmind.dev",
        "middleware_chain": "chain-llm",
        "sse": False,
    },
    "grafana": {
        "host": "grafana.agmind.dev",
        "middleware_chain": "chain-internal",
        "sse": False,
        "healthcheck_path": "/api/health",
    },
    "openwebui": {
        "host": "chat.agmind.dev",
        "middleware_chain": "chain-llm",
        "sse": True,  # стримит chat tokens
    },
    "dify-web": {
        "host": "dify.agmind.dev",
        "middleware_chain": "chain-llm",
        "sse": True,
    },
    "ragflow": {
        "host": "rag.agmind.dev",
        "middleware_chain": "chain-llm",
        "sse": True,
    },
    "portainer": {
        "host": "portainer.agmind.dev",
        "middleware_chain": "chain-internal",
        "sse": False,
        "healthcheck_path": "/",
    },
}


# Services that emit /metrics for Prometheus scraping (Phase H'.D wiring).
# docker_sd_configs whitelist берёт сервисы с label `prometheus.scrape=true`.
# Profile overrides: mutually-exclusive alternatives (как caddy vs nginx).
# Traefik становится default reverse proxy (`core`), nginx переходит в
# `core-nginx` для тех кто не хочет публичный домен / Cloudflare DNS-01.
PROFILE_OVERRIDES: dict[str, list[str]] = {
    "nginx": ["core-nginx"],
}


PROMETHEUS_SCRAPE: set[str] = {
    "llama-llm",
    "llama-embed",
    "llama-rerank",
    "cadvisor",
    "node-exporter",
    "postgres-exporter",
    "redis-exporter",
    "prometheus",
    "alertmanager",
    "grafana",
}


def _legacy_health_to_model(health: dict[str, Any]) -> HealthCheck | None:
    """Convert legacy health dict (compose format) → HealthCheck model."""
    if not health or "test" not in health:
        return None
    test = health["test"]
    if isinstance(test, str):
        # Compose CMD-SHELL string syntax → wrap to list
        test = ["CMD-SHELL", test]
    return HealthCheck(
        test=list(test),
        interval=health.get("interval", "30s"),
        timeout=health.get("timeout", "5s"),
        retries=int(health.get("retries", 3)),
        start_period=health.get("start_period", "10s"),
    )


def _split_extra_args(extra_args: list[str]) -> dict[str, list[str]]:
    """Parse legacy extra_args → compose-native fields.

    Examples:
        --device=/dev/dri          -> devices
        --group-add=render         -> group_add
        --security-opt=seccomp=... -> security_opt
        --cap-add=SYS_PTRACE       -> cap_add
        (anything else)            -> remainder (warn — нет прямого mapping)
    """
    out: dict[str, list[str]] = {
        "devices": [],
        "group_add": [],
        "security_opt": [],
        "cap_add": [],
        "remainder": [],
    }
    for arg in extra_args:
        if arg.startswith("--device="):
            out["devices"].append(arg.removeprefix("--device="))
        elif arg.startswith("--group-add="):
            out["group_add"].append(arg.removeprefix("--group-add="))
        elif arg.startswith("--security-opt="):
            out["security_opt"].append(arg.removeprefix("--security-opt="))
        elif arg.startswith("--cap-add="):
            out["cap_add"].append(arg.removeprefix("--cap-add="))
        else:
            out["remainder"].append(arg)
    return out


def _legacy_to_descriptor(svc: Service, tier: str) -> ServiceDescriptor:
    """Build ServiceDescriptor from legacy Service dataclass + tier hint."""
    resources = ResourceLimits(
        cpus=svc.cpus if svc.cpus > 0 else None,
        mem_limit=svc.mem_limit or None,
    )
    parsed = _split_extra_args(list(svc.extra_args))
    if parsed["remainder"]:
        print(
            f"  ! {svc.name}: unmapped extra_args (no compose equivalent): {parsed['remainder']}",
            file=sys.stderr,
        )
    routing = None
    if svc.name in ROUTING_MAP:
        routing = RoutingConfig.model_validate(ROUTING_MAP[svc.name])

    observability = ObservabilityConfig(
        prometheus_scrape=(svc.name in PROMETHEUS_SCRAPE),
    )

    profiles = PROFILE_OVERRIDES.get(svc.name, list(svc.profiles))

    return ServiceDescriptor(
        name=svc.name,
        image=svc.image,
        digest=svc.digest or None,
        tier=tier,  # type: ignore[arg-type]
        purpose=svc.purpose,
        profiles=profiles,
        ports=list(svc.ports),
        volumes=list(svc.volumes),
        env=dict(svc.env),
        # extra_args НЕ копируем — раскладываем по compose-native полям
        devices=parsed["devices"],
        group_add=parsed["group_add"],
        security_opt=parsed["security_opt"],
        cap_add=parsed["cap_add"],
        depends_on=list(svc.depends_on),
        resources=resources,
        health=_legacy_health_to_model(svc.health),
        routing=routing,
        observability=observability,
    )


def _descriptor_to_yaml(d: ServiceDescriptor) -> str:
    """Pydantic model → YAML string with schema header."""
    data = d.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    # mode=json чтобы Literal/enum сериализовались как strings
    body = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )
    header = "# yaml-language-server: $schema=../schemas/service.json\n"
    return header + body


def migrate(dry_run: bool = False) -> int:
    """Run migration. Returns exit code (0 success, 1 error)."""
    registry = load_registry()
    if not registry:
        print(
            "ERROR: load_registry() returned empty — check templates/services.yaml", file=sys.stderr
        )
        return 1

    # Validate tier mapping covers all services
    missing = [name for name in registry if name not in TIER_MAP]
    if missing:
        print(f"ERROR: TIER_MAP missing entries for: {missing}", file=sys.stderr)
        return 1

    if not dry_run:
        SERVICES_DIR.mkdir(parents=True, exist_ok=True)

    converted = 0
    for name, svc in registry.items():
        tier = TIER_MAP[name]
        try:
            descriptor = _legacy_to_descriptor(svc, tier)
        except Exception as exc:
            print(f"✗ {name}: build failed — {exc}", file=sys.stderr)
            return 1

        yaml_text = _descriptor_to_yaml(descriptor)
        out_path = SERVICES_DIR / f"{name}.yaml"

        if dry_run:
            print(
                f"  [dry-run] would write {out_path.relative_to(REPO_ROOT)} ({len(yaml_text)} bytes, tier={tier})"
            )
        else:
            out_path.write_text(yaml_text, encoding="utf-8")
            print(f"✓ {out_path.relative_to(REPO_ROOT)} (tier={tier})")
        converted += 1

    print()
    print(f"Migrated {converted}/{len(registry)} services" + (" (dry-run)" if dry_run else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Не записывать файлы")
    args = parser.parse_args(argv)
    return migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
