"""Pydantic v2 ServiceDescriptor (ADR-0005).

Расширение legacy `agmind.services.registry.Service` (frozen dataclass) полями
для auto-discovery (Traefik routing, Prometheus scrape, Loki labels) и
metadata (tier, owner). Backward compat через `to_legacy_service()`.

Поля validation:
    - name: docker container name convention `^[a-z][a-z0-9-]{1,30}$`
    - image: запрет `:latest` (pinned image invariant)
    - mem_limit: lowercase Docker format `^\\d+(k|m|g)$`
    - port: `[ip:]host:container` где host/container — port numbers
    - tier: Literal `edge | inference | storage | ops`
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from agmind.services.registry import Service


ServiceTier = Literal["edge", "inference", "app", "storage", "ops"]
"""Логическая группа сервиса.

- edge: reverse proxy, auth gateway (Traefik, Authelia)
- inference: compute backends — model serving only (llama-server, vLLM, Infinity)
- app: AI applications над inference (Dify, RAGFlow, Open WebUI, Docling)
- storage: state (Postgres, Redis, Qdrant, Weaviate, MinIO, Elasticsearch)
- ops: observability (Prometheus, Grafana, Loki, Alloy, exporters, Alertmanager)
"""


# All field anchors use \Z (end-of-string), NOT $ (which matches before a trailing \n) —
# else `name='foo\n'` becomes a compose service key with an embedded newline (review LOW
# schema-name-network-trailing-newline). Digest is lowercase-hex only: Docker rejects an
# uppercase digest with "invalid reference format" (review LOW schema-digest-uppercase-hex).
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}\Z")
_NETWORK_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}\Z")
_MEM_LIMIT_RE = re.compile(r"^\d+(k|m|g)\Z")
_PORT_RE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}:)?\d{1,5}:\d{1,5}\Z")
_LATEST_RE = re.compile(r":latest(?:$|@)")
_SHA256_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}\Z")


def _is_sha256_digest(value: str) -> bool:
    return bool(_SHA256_DIGEST_RE.match(value))


def _image_has_tag(image: str) -> bool:
    image_without_digest = image.split("@", 1)[0]
    last_slash = image_without_digest.rfind("/")
    last_colon = image_without_digest.rfind(":")
    return last_colon > last_slash and last_colon < len(image_without_digest) - 1


# Docker reference tag grammar: [A-Za-z0-9_][A-Za-z0-9_.-]{0,127} — notably NO '+'.
_VALID_DOCKER_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")


def _is_valid_docker_tag(tag: str) -> bool:
    return bool(_VALID_DOCKER_TAG_RE.match(tag))


def _split_repo_tag(image: str) -> tuple[str, str | None]:
    """Split ``repo[:tag]`` (no digest) into ``(repo, tag|None)``.

    A registry-port colon (``host:5000/img``) is not treated as a tag.
    """
    last_slash = image.rfind("/")
    last_colon = image.rfind(":")
    if last_colon > last_slash and last_colon < len(image) - 1:
        return image[:last_colon], image[last_colon + 1 :]
    return image, None


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

    port: int | None = Field(default=None, ge=1, le=65535)
    """Traefik upstream (loadbalancer.server.port) override — the CONTAINER-side port the
    edge routes to, decoupled from the published host port in ``ports``.

    Default None → renderer falls back to the first ``ports`` mapping's container port. Set
    this when the service's edge/UI port differs from what it publishes (RAGFlow publishes the
    API 9380 but serves its web UI on nginx :80 — the router must target 80). live-audit
    2026-06-05 HIGH ragflow-edge-route-wrong-port."""

    path_prefixes: tuple[str, ...] = ()
    """Если non-empty → router rule = ``Host(`host`) && (PathPrefix(`p0`) || PathPrefix(`p1`) …)``.
    Пустой кортеж = Host-only rule (текущее поведение). tuple (НЕ list) — модель frozen=True.

    Для multi-component приложения на ОДНОМ хосте (Dify: /console/api,/api,/v1,/files,/mcp,
    /triggers → dify-api; /, /explore → dify-web). Парный host-only router с низким priority
    ловит остальное."""

    priority: int = 0
    """Traefik router priority. 0 = Traefik default (сортировка по длине rule). Higher wins.
    HIGH на prefix-scoped router, LOW на catch-all router того же хоста — иначе catch-all
    (Host-only) может перехватить path-scoped запросы."""


class AccessConfig(BaseModel):
    """Operator access metadata for a web-UI / model-endpoint service.

    Drives the post-install summary, ``credentials.txt``, and the ``agmind endpoints`` /
    ``agmind creds show`` commands. The URL comes from :class:`RoutingConfig`; secret *values*
    come from the rendered ``.env`` (resolved via ``password_env``) and are never stored here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    login: str | None = None
    """Default username (e.g. ``admin``). ``None`` = no login / account created on first visit."""

    password_env: str | None = None
    """Name of the ``.env`` var holding the admin password (e.g. ``GRAFANA_PASSWORD``).
    ``None`` = no managed password (register-on-first-login or unauthenticated)."""

    first_login_register: bool = False
    """``True`` → operator creates the admin / registers an account on first login."""

    lan_only: bool = False
    """``True`` → not reachable through the public reverse proxy; emit an ``ssh -L`` hint."""

    api_kind: Literal["openai"] | None = None
    """If set, the service exposes a machine API (OpenAI-compatible) → render a copy-paste
    "Add Model" block in credentials.txt instead of a human login."""

    note: str | None = None
    """Optional operator hint shown under the entry in credentials.txt / `creds show` (e.g. a
    first-login caveat or a recovery command). Plain text, never a secret."""


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

    mem_reservation: str | None = None
    """Docker SOFT memory reservation (compose `mem_reservation:`), same `\\d+[kmg]` lowercase
    format as ``mem_limit``. None = unset. A reservation is the scheduling floor the kernel tries
    to keep available under memory pressure (NOT a hard cap — that is ``mem_limit``). Set it to the
    measured working set (~25-50% of ``mem_limit``) so Docker has a real placement signal instead of
    only an OOM ceiling (OPT-1, optimization audit 2026-06-08)."""

    @field_validator("mem_limit", "mem_reservation")
    @classmethod
    def _check_mem_limit(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _MEM_LIMIT_RE.match(v):
            raise ValueError(
                f"mem value '{v}' invalid: expected lowercase '\\d+[kmg]' (e.g. '4g', '512m')"
            )
        return v


class BuildConfig(BaseModel):
    """Local image build for an AGmind-authored app service (compose-native ``build:``).

    A descriptor with a ``build:`` block is built on the operator's host from shipped source
    at deploy time — NOT pulled from a registry. This is how AGmind ships its OWN images (the
    agent cores) without a registry/publish step: air-gap-friendly, nothing published. Such a
    service carries no registry digest, so it is exempt from the digest-pin gate; its ``image``
    is the resulting local tag (still ``:latest``-forbidden, still pinned by tag).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context: str = "."
    """Docker build context (repo-relative). Default repo root."""

    dockerfile: str
    """Dockerfile path relative to the build context (e.g. docker/Dockerfile.agent-pydanticai)."""


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

    build: BuildConfig | None = None
    """Local build for an AGmind-authored image (compose `build:`). A build-service is built
    on-host from shipped source, not pulled — so it carries no `digest` and is exempt from the
    digest-pin gate. `image` is the resulting local tag."""

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

    networks: list[str] = Field(default_factory=list)
    """Extra named networks to join (compose `networks:`). Empty (default) = join
    the shared `default` network only — byte-identical to the legacy single-net
    output. A non-empty list joins ONLY those networks (e.g. `["ssrf-net"]` to cage
    a service on an internal-only net with no host route)."""

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

    cap_drop: list[str] = Field(default_factory=list)
    """Linux capabilities to DROP, e.g. ['ALL']. Compose `cap_drop:`. Hardening: drop ALL,
    then cap_add only what's needed (live-audit 2026-06-05 container-hardening)."""

    read_only: bool = False
    """Read-only root filesystem (compose `read_only: true`). The app must write only to
    declared volumes/tmpfs. Hardening — esp. for app-as-root containers."""

    pids_limit: int | None = Field(default=None, ge=1)
    """Max process IDs (compose `pids_limit:`) — fork-bomb / PID-exhaustion guard. None = unset
    (live-audit 2026-06-05 no-pids-limit — matters most for untrusted code, e.g. dify-sandbox)."""

    no_new_privileges: bool = False
    """If True the renderer adds ``no-new-privileges:true`` to ``security_opt`` (a process can no
    longer gain privileges via setuid/setgid). Convenience over hand-writing the opt string."""

    cgroupns_mode: str | None = None
    """Cgroup namespace mode (compose top-level ``cgroup:``). 'host' shares the host's cgroup
    namespace so the container can walk EVERY cgroup, not just its own. Required for cAdvisor
    under cgroup v2 + private cgroupns — otherwise it only sees ``id="/"`` (its own cgroup) and
    emits no per-container series (grafana-dashboards.md CRITICAL-1). Only 'host'/'private' are
    valid compose values."""

    privileged: bool = False
    """Run the container with full privileges (compose ``privileged: true``). Last-resort for
    tools that need unrestricted host introspection (cAdvisor on some cgroup-v2 hosts). Prefer
    the narrower cgroupns_mode='host' + devices=['/dev/kmsg'] + /sys/fs/cgroup mount first."""

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

    access: AccessConfig | None = None
    """Operator access metadata (login / password_env / register / lan_only / api_kind).
    None = no operator-facing login (internal/infra service)."""

    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    # ---- Ownership / data-dir hints (H.5) ----
    run_as_uid: int | None = None
    """Numeric UID the container image runs as (e.g. 65534 for prometheus, 472 for grafana).

    OPTIONAL hint used by the derived bootstrap ownership-coverage test
    (tests/ansible/test_data_dir_ownership_coverage.py) to assert the ansible
    bootstrap pre-create loop owns every writable host-bind dir with the correct uid.

    Root-running images (USER 0 / empty) leave this None — no bootstrap entry needed.
    Known values: prometheus 65534, grafana 472, loki 10001, n8n 1000,
    elasticsearch 1000, dify-api/docling 1001.
    """

    run_as_gid: int | None = None
    """Numeric GID the container image runs as.  Defaults to run_as_uid when unset.

    Set explicitly only when GID differs from UID (e.g. elasticsearch uid=1000, gid=0).
    """

    writable_mounts: list[str] = Field(default_factory=list)
    """Host paths the container writes to as run_as_uid:run_as_gid.

    OPTIONAL hint listing the SOURCE (host) side of volume bind-mounts that the
    container user writes to.  The derived ownership-coverage test asserts the
    bootstrap loop pre-creates each path with the correct owner.

    Use the real absolute host path, e.g. '/var/lib/agmind/prometheus'.
    Only include writable bind-mounts; :ro config mounts are not data dirs.
    """

    # ---- Validators ----
    @field_validator("extra_args")
    @classmethod
    def _reject_extra_args(cls, v: list[str]) -> list[str]:
        if v:
            raise ValueError(
                "extra_args is unsupported by the renderer and silently dropped. "
                "Use command/devices/group_add/security_opt/cap_add instead."
            )
        return v

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
            raise ValueError(f"image '{v}' uses :latest — pin to semver (pinned image invariant)")
        if "@" in v:
            image_name, digest = v.rsplit("@", 1)
            if any(char.isspace() for char in image_name):
                raise ValueError(f"image '{v}' contains whitespace — pin to exact image reference")
            if image_name.endswith(":"):
                raise ValueError(f"image '{v}' has no tag — pin to semver")
            # Inline digest in the image ref MUST carry the `sha256:` prefix — a bare 64-hex
            # `@<hex>` passes _is_sha256_digest (prefix optional) but `docker compose up` rejects
            # it as "invalid reference format" (review MEDIUM schema-inline-digest-no-prefix).
            if not image_name or not digest.startswith("sha256:") or not _is_sha256_digest(digest):
                raise ValueError(
                    f"image '{v}' has invalid sha256 digest — expected sha256:<64 lowercase hex>"
                )
            return v
        if any(char.isspace() for char in v):
            raise ValueError(f"image '{v}' contains whitespace — pin to exact image reference")
        if not _image_has_tag(v):
            raise ValueError(f"image '{v}' has no tag — pin to semver")
        return v

    @field_validator("digest")
    @classmethod
    def _check_digest(cls, v: str | None) -> str | None:
        if not v:
            return v
        if not _is_sha256_digest(v):
            raise ValueError(f"digest '{v}' invalid: expected sha256 digest with 64 hex chars")
        return v

    @model_validator(mode="after")
    def _check_single_digest_source(self) -> ServiceDescriptor:
        if "@" in self.image and self.digest:
            raise ValueError(
                "duplicate digest: use either image@sha256:<digest> or digest field, not both"
            )
        return self

    @field_validator("ports")
    @classmethod
    def _check_ports(cls, v: list[str]) -> list[str]:
        for p in v:
            if not _PORT_RE.match(p):
                raise ValueError(
                    f"port '{p}' invalid: expected '[ip:]host:container' format "
                    "(e.g. '127.0.0.1:8080:8080' or '8080:8080')"
                )
            parts = p.split(":")
            if len(parts) == 3:
                bind_ip, host_port, container_port = parts
                try:
                    ip_address(bind_ip)
                except ValueError as exc:
                    raise ValueError(f"port '{p}' invalid: bind IP is not valid") from exc
            else:
                host_port, container_port = parts
            for label, raw_port in (("host", host_port), ("container", container_port)):
                port = int(raw_port)
                if port < 1 or port > 65535:
                    raise ValueError(f"port '{p}' invalid: {label} port must be 1..65535")
        return v

    @field_validator("depends_on")
    @classmethod
    def _check_depends_on(cls, v: list[str]) -> list[str]:
        for dep in v:
            if not _NAME_RE.match(dep):
                raise ValueError(f"depends_on '{dep}' invalid: must match service name pattern")
        return v

    @field_validator("networks")
    @classmethod
    def _check_networks(cls, v: list[str]) -> list[str]:
        for net in v:
            if not _NETWORK_RE.match(net):
                raise ValueError(
                    f"network '{net}' invalid: must match ^[a-z][a-z0-9_-]{{0,62}}$ "
                    "(docker network name; e.g. 'ssrf-net', 'default')"
                )
        return v

    @field_validator("volumes")
    @classmethod
    def _check_volumes(cls, v: list[str]) -> list[str]:
        # Catch malformed mounts at schema time (they'd otherwise only fail at
        # `docker compose up`). Lenient on the mode token to avoid churn, strict on shape.
        for spec in v:
            parts = spec.split(":")
            # Reject an empty mode token too ('/a:/b:' → ['/a','/b','']) — docker rejects it,
            # but the old check only validated src/dst (review LOW schema-volume-empty-mode).
            if (
                len(parts) not in (2, 3)
                or not parts[0]
                or not parts[1]
                or (len(parts) == 3 and not parts[2])
            ):
                raise ValueError(
                    f"volume '{spec}' invalid: expected 'src:dst[:mode]' with non-empty src and dst"
                )
        return v

    @field_validator("cgroupns_mode")
    @classmethod
    def _check_cgroupns_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("host", "private"):
            raise ValueError(
                f"cgroupns_mode '{v}' invalid: only 'host' or 'private' are valid compose values"
            )
        return v

    def fq_image(self) -> str:
        """Fully-qualified image reference (image + digest if present)."""
        if self.digest:
            digest = self.digest if self.digest.startswith("sha256:") else f"sha256:{self.digest}"
            repo, tag = _split_repo_tag(self.image)
            if tag is not None and not _is_valid_docker_tag(tag):
                # Tag is not a legal docker reference tag (e.g. grafana's
                # '13.0.1+security-01' — '+' is illegal). The digest is authoritative,
                # so pin by digest only (repo@sha256:<d>); keeping the bad tag yields
                # 'invalid reference format' on `docker compose up`.
                return f"{repo}@{digest}"
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
