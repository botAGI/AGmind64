"""Pydantic v2 ServiceDescriptor (ADR-0005).

Расширение legacy `agmind.services.registry.Service` (frozen dataclass) полями
для auto-discovery (Traefik routing, Prometheus scrape, Loki labels) и
metadata (tier, owner). Backward compat через `to_legacy_service()`.

Поля validation:
    - name: docker container name convention `^[a-z][a-z0-9-]{1,30}$`
    - image: запрет `:latest` (Invariant I.2 в `.planning/codebase/INVARIANTS.md`)
    - mem_limit: lowercase Docker format `^\\d+(k|m|g)$`
    - port: `[ip:]host:container` где host/container — port numbers
    - tier: Literal `edge | inference | storage | ops`
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from agmind.services.registry import Service


ServiceTier = Literal["edge", "inference", "app", "storage", "ops"]
"""Логическая группа сервиса.

- edge: reverse proxy, auth gateway (Traefik, Nginx, Caddy, Authelia)
- inference: compute backends — model serving only (llama-server, vLLM, Infinity)
- app: AI applications над inference (Dify, RAGFlow, Open WebUI, Docling)
- storage: state (Postgres, Redis, Qdrant, Weaviate, MinIO, Elasticsearch)
- ops: observability (Prometheus, Grafana, Loki, Alloy, exporters, Alertmanager)
"""


_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
_MEM_LIMIT_RE = re.compile(r"^\d+(k|m|g)$")
_PORT_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}:)?\d{1,5}:\d{1,5}$")
_LATEST_RE = re.compile(r":latest(?:$|@)")


class HealthCheck(BaseModel):
    """Docker healthcheck definition (compose v3 format).

    Используется тремя потребителями (deep-dive 04 §5):
    - Docker compose: для restart logic
    - Traefik: для исключения unhealthy из роутинга
    - Prometheus blackbox: для алертинга
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    test: list[str] = Field(min_length=1)
    """Compose healthcheck.test (e.g. ["CMD", "curl", "-f", "http://localhost:8080/health"])."""

    interval: str = "30s"
    """Docker duration format: "30s", "1m", "5m"."""

    timeout: str = "5s"

    retries: int = Field(default=3, ge=1, le=20)

    start_period: str = "10s"
    """Период до того как фейлы начнут считаться (для медленно стартующих сервисов)."""


class RoutingConfig(BaseModel):
    """Traefik routing метаданные.

    Если None — сервис не публикуется через Traefik (internal-only).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = Field(min_length=3)
    """Полный hostname, e.g. `qdrant.agmind.dev`. Используется в `Host(...)` rule."""

    middleware_chain: Literal["chain-llm", "chain-internal", "chain-public"] = "chain-internal"
    """Какую цепочку middleware применять (см. deep-dive 01 §4).

    - chain-llm: rate-limit + security-headers + Authelia, БЕЗ buffering (для SSE)
    - chain-internal: только Authelia (Grafana, Prometheus UI и т.д.)
    - chain-public: rate-limit + security-headers (для публичных endpoints)
    """

    sse: bool = False
    """Если True — добавит `responseforwarding.flushinterval=1ms` + `tls.options=no-http2@file`
    (см. deep-dive 01 §1 — обязательно для llama-server streaming)."""

    healthcheck_path: str = "/health"
    """Путь для Traefik active healthchecks (отдельно от Docker healthcheck)."""


class ObservabilityConfig(BaseModel):
    """Auto-discovery hints для observability stack.

    Идея: один лейбл в compose → Prometheus/Loki сам подхватывает.
    См. deep-dive 04 §3-§4 и deep-dive 03 §7.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prometheus_scrape: bool = False
    """Если True — сервис попадёт в Prometheus targets через docker_sd whitelist."""

    metrics_path: str = "/metrics"

    metrics_port: int | None = Field(default=None, ge=1, le=65535)
    """Если отличается от основного port сервиса. None = первый exposed port."""

    loki_scrape: bool = True
    """Default true — почти всегда логи нужны. Отключать только для секретных."""

    grafana_dashboard: str | None = None
    """Имя файла в `templates/grafana/dashboards/`, e.g. "qdrant.json".

    Provisioned автоматически если задано.
    """


class ResourceLimits(BaseModel):
    """CPU/memory limits для Docker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cpus: float | None = Field(default=None, gt=0, le=64)
    """Docker `cpus:` limit (cores, float). None = unlimited."""

    mem_limit: str | None = None
    """Docker memory limit, format `\\d+[kmg]` lowercase. None = unlimited."""

    @field_validator("mem_limit")
    @classmethod
    def _check_mem_limit(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _MEM_LIMIT_RE.match(v):
            raise ValueError(
                f"mem_limit '{v}' invalid: expected lowercase '\\d+[kmg]' (e.g. '4g', '512m')"
            )
        return v


class ServiceDescriptor(BaseModel):
    """Single source of truth для одного сервиса (Phase H' artifact).

    Парсится из `templates/services/<name>.yaml`, рендерится в Docker Compose
    + Traefik labels + Prometheus scrape + Loki labels автоматически.

    Пример минимального файла:

        # yaml-language-server: $schema=../schemas/service.json
        name: qdrant
        image: qdrant/qdrant:v1.18.0
        tier: storage
        purpose: Vector store
        ports:
          - "127.0.0.1:6333:6333"
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ---- Identity ----
    name: str
    """Container slug. Pattern `^[a-z][a-z0-9-]{1,30}$`."""

    image: str
    """Pinned image. `:latest` запрещён (Invariant I.2)."""

    digest: str | None = None
    """Optional SHA256 digest (sha256:abc... — без префикса `sha256:`)."""

    tier: ServiceTier
    """Группа сервиса. Используется для middleware chains, ordering."""

    purpose: str = ""
    """Human-readable назначение."""

    owner: str = "agmind-core"
    """Кто отвечает за сервис (Backstage-style metadata)."""

    profiles: list[str] = Field(default_factory=list)
    """Docker compose profiles (core, rag, ragflow, ui, observability, proxmox, security, full)."""

    # ---- Networking ----
    ports: list[str] = Field(default_factory=list)
    """Port mappings, format `[ip:]host:container` (e.g. "127.0.0.1:8080:8080")."""

    # ---- Storage ----
    volumes: list[str] = Field(default_factory=list)
    """Volume mounts (e.g. "/var/lib/agmind/qdrant:/qdrant/storage")."""

    # ---- Runtime ----
    env: dict[str, str] = Field(default_factory=dict)
    """Environment variables. Secrets через mounted files, НЕ здесь."""

    extra_args: list[str] = Field(default_factory=list)
    """[LEGACY] Сырые docker CLI args. Phase H'.C парсит → devices/group_add/etc.

    Будет удалено в Phase H'.E после полной миграции — используйте явные поля ниже.
    """

    command: list[str] | None = None
    """Container command (override image's CMD/ENTRYPOINT). Compose `command:`."""

    devices: list[str] = Field(default_factory=list)
    """Device mappings, e.g. ['/dev/kfd', '/dev/dri']. Compose `devices:`.

    Эквивалент docker run `--device=PATH`. Для AMD GPU passthrough.
    """

    group_add: list[str] = Field(default_factory=list)
    """Additional Unix groups, e.g. ['video', 'render']. Compose `group_add:`.

    Нужно для доступа к GPU (`render` group) и видео (`video` group).
    """

    security_opt: list[str] = Field(default_factory=list)
    """Security options, e.g. ['seccomp=unconfined']. Compose `security_opt:`."""

    cap_add: list[str] = Field(default_factory=list)
    """Linux capabilities to add, e.g. ['SYS_PTRACE']. Compose `cap_add:`."""

    depends_on: list[str] = Field(default_factory=list)
    """Имена других сервисов (для `depends_on:` в compose)."""

    # ---- Phase O: capability graph ----
    provides: list[str] = Field(default_factory=list)
    """Capability tags которые сервис предоставляет, e.g. ['vector_db'],
    ['reverse_proxy'], ['rag_stack']. Используется для conflict detection
    (несколько сервисов одной capability = коллизия) и capability injection
    (consumer получает env vars указывающие на выбранного provider'а).

    Convention: snake_case, single noun. Не путать с tier (логическая группа)
    — capability это behavioural contract."""

    conflicts_with: list[str] = Field(default_factory=list)
    """Имена сервисов которые не могут жить с этим в одном compose.
    Hard error если оба выбраны (см. check_service_compatibility).
    Example: ragflow conflicts_with [dify-api, dify-web, ...]."""

    consumes: list[str] = Field(default_factory=list)
    """Capability tags которые сервис consumes (использует чужие).
    Renderer injects env vars из capability_bindings table:
      e.g. dify-api consumes=['vector_db'] + выбран milvus
        →  inject VECTOR_STORE=milvus, MILVUS_URI=http://milvus:19530."""

    # ---- Limits ----
    resources: ResourceLimits = Field(default_factory=ResourceLimits)

    # ---- Health ----
    health: HealthCheck | None = None

    # ---- Auto-discovery hints ----
    routing: RoutingConfig | None = None
    """Если задано — сервис публикуется через Traefik. None = internal-only."""

    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    # ---- Validators ----
    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                f"name '{v}' invalid: must match ^[a-z][a-z0-9-]{{1,30}}$ "
                "(lowercase, digits, hyphens; start with letter; 2-31 chars)"
            )
        return v

    @field_validator("image")
    @classmethod
    def _check_image(cls, v: str) -> str:
        if _LATEST_RE.search(v):
            raise ValueError(
                f"image '{v}' uses :latest — pin to semver "
                "(Invariant I.2 .planning/codebase/INVARIANTS.md)"
            )
        if "@sha256:" not in v and ":" not in v:
            raise ValueError(f"image '{v}' has no tag — pin to semver")
        return v

    @field_validator("ports")
    @classmethod
    def _check_ports(cls, v: list[str]) -> list[str]:
        for p in v:
            if not _PORT_RE.match(p):
                raise ValueError(
                    f"port '{p}' invalid: expected '[ip:]host:container' format "
                    "(e.g. '127.0.0.1:8080:8080' or '8080:8080')"
                )
        return v

    @field_validator("depends_on")
    @classmethod
    def _check_depends_on(cls, v: list[str]) -> list[str]:
        for dep in v:
            if not _NAME_RE.match(dep):
                raise ValueError(f"depends_on '{dep}' invalid: must match service name pattern")
        return v

    def fq_image(self) -> str:
        """Fully-qualified image reference (image + digest if present)."""
        if self.digest:
            digest = self.digest if self.digest.startswith("sha256:") else f"sha256:{self.digest}"
            return f"{self.image}@{digest}"
        return self.image

    def to_legacy_service(self) -> Service:
        """Convert to `agmind.services.registry.Service` for backward compat.

        Используется текущим Ansible Jinja2 рендерером до Phase H'.C.
        Новые поля (tier, routing, observability) теряются — рендерер их пока
        не понимает, это OK.
        """
        # Lazy import чтобы избежать циклической зависимости registry → schemas → registry
        from agmind.services.registry import Service

        digest_stripped = (self.digest or "").removeprefix("sha256:")

        health_dict: dict[str, object] = {}
        if self.health is not None:
            health_dict = {
                "test": list(self.health.test),
                "interval": self.health.interval,
                "timeout": self.health.timeout,
                "retries": self.health.retries,
                "start_period": self.health.start_period,
            }

        return Service(
            name=self.name,
            image=self.image,
            digest=digest_stripped,
            profiles=tuple(self.profiles),
            purpose=self.purpose,
            depends_on=tuple(self.depends_on),
            cpus=self.resources.cpus or 0.0,
            mem_limit=self.resources.mem_limit or "",
            ports=tuple(self.ports),
            env=dict(self.env),
            volumes=tuple(self.volumes),
            health=health_dict,
            extra_args=tuple(self.extra_args),
        )
