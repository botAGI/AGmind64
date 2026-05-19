"""Runtime API для service registry.

Phase H'.E: load_registry() читает `templates/services/*.yaml` через
ServiceDescriptor (Pydantic v2), конвертирует в legacy Service dataclass
для backward compat с тестами/Ansible. Legacy `templates/services.yaml`
больше не используется и удалён.

Profile system:
- core           — минимум для inference (llama-* + qdrant + traefik)
- core-caddy     — alternative с Caddy вместо Traefik
- core-nginx     — alternative с Nginx (для setup без публичного домена)
- rag            — core + Dify + Postgres + Redis + Docling
- rag-weaviate   — RAG с Weaviate вместо Qdrant
- rag-milvus     — RAG с Milvus
- ragflow        — core + RAGFlow + MySQL + Elasticsearch + MinIO
- ui             — Open WebUI как chat frontend
- observability  — Prometheus + Grafana + Loki + Alloy + Alertmanager + exporters
- security       — Authelia + fail2ban (host-level)
- full           — все профили вместе
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from agmind.log import logger

log = logger(__name__)


class ServiceProfile(str, Enum):
    """Profile = логическая группа сервисов."""

    CORE = "core"
    RAG = "rag"
    RAGFLOW = "ragflow"
    UI = "ui"
    OBSERVABILITY = "observability"
    SECURITY = "security"
    FULL = "full"


@dataclass(frozen=True)
class Service:
    """Один сервис в registry."""

    name: str
    """Unique slug: "dify-api", "postgres", "llama-llm" etc."""

    image: str
    """Docker image:tag (pinned semver, no :latest)."""

    digest: str = ""
    """SHA256 digest (опционально). Используется как `image@sha256:...`."""

    profiles: tuple[str, ...] = ()
    """Names of profiles this service belongs to."""

    purpose: str = ""
    """Human-readable назначение."""

    depends_on: tuple[str, ...] = ()
    """Other service names этот сервис зависит от."""

    cpus: float = 0.0
    """Default CPU limit (cores). 0 = не ограничивать."""

    mem_limit: str = ""
    """Default memory limit (e.g. "4g", "16g"). Empty = не ограничивать."""

    ports: tuple[str, ...] = ()
    """Docker port mappings ("8080:8080", "127.0.0.1:5432:5432")."""

    env: dict[str, str] = field(default_factory=dict)
    """Env vars (sensible defaults; secrets через external file)."""

    volumes: tuple[str, ...] = ()
    """Volume mounts."""

    health: dict[str, Any] = field(default_factory=dict)
    """Healthcheck definition."""

    extra_args: tuple[str, ...] = ()
    """Дополнительные docker CLI args."""

    def fq_image(self) -> str:
        """Return fully-qualified image reference (with digest if pinned)."""
        if self.digest:
            return f"{self.image}@sha256:{self.digest}"
        return self.image


_DEFAULT_SERVICES_DIR = Path(__file__).parent.parent.parent / "templates" / "services"
# Legacy compat — старый монолитный yaml (удалён в Phase H'.E, но переменная
# держится для тестов которые до сих пор её упоминают).
_DEFAULT_REGISTRY_PATH = _DEFAULT_SERVICES_DIR.parent / "services.yaml"


def _parse_yaml(text: str) -> dict[str, Any]:
    """Минимальный YAML parser без зависимости от PyYAML.

    Поддерживает только то, что нужно для services.yaml:
    - key: value (с string values, int values)
    - list items с `-`
    - nested mappings с 2-space indent
    - quoted strings ("..." и '...')

    Если PyYAML установлен — использует его (надёжнее).
    """
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(text) or {}
    except ImportError:
        return _fallback_yaml_parse(text)


def _fallback_yaml_parse(text: str) -> dict[str, Any]:
    """Tiny YAML subset parser (без PyYAML). 2-space indent only."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-2, root)]  # (indent, container)
    pending_key: list[tuple[int, str, Any]] = []  # (indent, key, parent)

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        content = line.strip()

        # Pop stack to current indent level
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root

        if content.startswith("- "):
            value = _yaml_value(content[2:].strip())
            if not isinstance(parent, list):
                # Convert dict slot to list — unsupported in this subset
                continue
            parent.append(value)
        elif ":" in content:
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            if not val:
                # Mapping continues — empty value, expecting child block
                new: dict[str, Any] | list[Any] = {}
                if isinstance(parent, dict):
                    parent[key] = new
                stack.append((indent, new))
            elif val == "[]":
                parent[key] = []
            elif val == "{}":
                parent[key] = {}
            else:
                parent[key] = _yaml_value(val)
    return root


def _yaml_value(s: str) -> Any:
    if not s:
        return ""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    if s.lower() in ("true", "yes"):
        return True
    if s.lower() in ("false", "no"):
        return False
    if s.lower() in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _build_service(name: str, data: dict[str, Any]) -> Service:
    """Construct Service from dict (from YAML parse)."""
    return Service(
        name=name,
        image=str(data.get("image", "")),
        digest=str(data.get("digest", "")),
        profiles=tuple(data.get("profiles") or ()),
        purpose=str(data.get("purpose", "")),
        depends_on=tuple(data.get("depends_on") or ()),
        cpus=float(data.get("cpus") or 0.0),
        mem_limit=str(data.get("mem_limit", "")),
        ports=tuple(str(p) for p in (data.get("ports") or ())),
        env=dict(data.get("env") or {}),
        volumes=tuple(str(v) for v in (data.get("volumes") or ())),
        health=dict(data.get("health") or {}),
        extra_args=tuple(str(a) for a in (data.get("extra_args") or ())),
    )


def _load_legacy_single_yaml(p: Path) -> dict[str, Service]:
    """Legacy: один монолитный services.yaml (до Phase H'.B).

    Сохранено для backward compat в тестах, которые pass свой fixture yaml.
    Возвращает {name: Service} построенных через `_build_service`.
    """
    if not p.exists():
        return {}
    parsed = _parse_yaml(p.read_text(encoding="utf-8"))
    services_data = parsed.get("services") or {}
    if not isinstance(services_data, dict):
        return {}
    out: dict[str, Service] = {}
    for name, data in services_data.items():
        if isinstance(data, dict):
            out[name] = _build_service(name, data)
    return out


def load_registry(path: Path | str | None = None) -> dict[str, Service]:
    """Load all services. Returns {name: Service}.

    Phase H'.E: читает `templates/services/*.yaml` (split-файлы из Phase H'.B)
    через `agmind.schemas.ServiceDescriptor` → `.to_legacy_service()`. Если
    указан явный `path` (legacy single yaml) — возвращает empty dict с warning.
    """
    # If explicit path passed:
    #   - file → legacy single-yaml parsing (backward compat для тестов)
    #   - dir → split-files via ServiceDescriptor
    #   - non-existent path → empty (тесты ожидают такое поведение)
    # If path is None → default split dir `templates/services/`.
    if path is not None:
        target = Path(path)
        if target.is_file():
            return _load_legacy_single_yaml(target)
        if target.is_dir():
            services_dir = target
        else:
            # Path указан, но не существует — empty result (legacy contract)
            log.warning("services source not found at %s", target)
            return {}
    else:
        services_dir = _DEFAULT_SERVICES_DIR

    if not services_dir.exists():
        log.warning("services directory not found at %s", services_dir)
        return {}

    # Lazy import чтобы избежать circular dep (schemas → services.registry)
    try:
        from agmind.schemas import ServiceDescriptor
    except ImportError:
        log.error("agmind.schemas unavailable — cannot load registry")
        return {}

    out: dict[str, Service] = {}
    for yaml_path in sorted(services_dir.glob("*.yaml")):
        try:
            data = _parse_yaml(yaml_path.read_text(encoding="utf-8"))
            descriptor = ServiceDescriptor.model_validate(data)
            out[descriptor.name] = descriptor.to_legacy_service()
        except Exception as exc:  # noqa: BLE001
            log.error("failed to load %s: %s", yaml_path.name, exc)
            continue
    return out


def list_services(path: Path | str | None = None) -> list[Service]:
    """List all services in priority order (file order)."""
    return list(load_registry(path).values())


def services_for_profile(
    profile: ServiceProfile | str,
    path: Path | str | None = None,
) -> list[Service]:
    """Return services что входят в указанный профиль.

    Profile "full" = объединение всех именованных профилей.
    """
    if isinstance(profile, str):
        profile = ServiceProfile(profile)

    all_services = list_services(path)
    if profile == ServiceProfile.FULL:
        wanted_profiles = {p.value for p in ServiceProfile if p != ServiceProfile.FULL}
    else:
        wanted_profiles = {profile.value}

    return [s for s in all_services if set(s.profiles) & wanted_profiles]


_IMAGE_LATEST_RE = re.compile(r":latest(?:$|@)")


def validate_no_latest(services: dict[str, Service]) -> list[str]:
    """Return list of violations: services using :latest tag.

    AGMIND_MIGRATION_SPEC.md §1.3: «Всегда пинить по digest, не по тегу».
    """
    issues: list[str] = []
    for svc in services.values():
        if _IMAGE_LATEST_RE.search(svc.image):
            issues.append(f"{svc.name}: image '{svc.image}' uses :latest — pin to semver")
    return issues
