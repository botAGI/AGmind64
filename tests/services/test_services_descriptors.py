"""Каждый файл templates/services/*.yaml валидно парсится в ServiceDescriptor.

Это smoke-тест что текущий сервисный каталог консистентен и соответствует
ServiceDescriptor schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "templates" / "services"


def _service_files() -> list[Path]:
    if not SERVICES_DIR.exists():
        return []
    return sorted(SERVICES_DIR.glob("*.yaml"))


@pytest.fixture(scope="module")
def service_files() -> list[Path]:
    files = _service_files()
    if not files:
        pytest.skip("templates/services/ empty — service descriptor catalog missing")
    return files


def test_services_directory_exists() -> None:
    assert SERVICES_DIR.exists(), f"missing {SERVICES_DIR}"


def test_services_directory_non_empty(service_files: list[Path]) -> None:
    assert len(service_files) >= 30, f"expected ~32 services, got {len(service_files)}"


@pytest.mark.parametrize(
    "path",
    _service_files() or [pytest.param(Path("/dev/null"), marks=pytest.mark.skip)],
    ids=lambda p: p.stem,
)
def test_descriptor_file_validates(path: Path) -> None:
    """Каждый templates/services/<name>.yaml парсится в ServiceDescriptor."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    descriptor = ServiceDescriptor.model_validate(data)
    # Filename ↔ service.name consistency
    assert descriptor.name == path.stem, f"name '{descriptor.name}' != filename stem '{path.stem}'"


def _duration_seconds(value: str) -> int:
    units = {"s": 1, "m": 60, "h": 3600}
    assert value and value[-1] in units, f"unexpected duration: {value!r}"
    return int(value[:-1]) * units[value[-1]]


def test_traefik_healthcheck_ping_endpoint_is_enabled() -> None:
    """traefik's healthcheck runs `traefik healthcheck --ping`, which calls the /ping
    endpoint. That endpoint is OFF by default and must be enabled with `--ping=true` in
    the static command — otherwise the healthcheck always fails → container unhealthy →
    deploy rolls back."""
    data = yaml.safe_load((SERVICES_DIR / "traefik.yaml").read_text(encoding="utf-8"))
    descriptor = ServiceDescriptor.model_validate(data)
    health = [str(x) for x in (descriptor.health.test if descriptor.health else [])]
    command = [str(c) for c in (descriptor.command or [])]
    if any("--ping" in tok for tok in health):
        assert any("--ping" in tok for tok in command), (
            "traefik healthcheck uses `--ping` but the ping endpoint is not enabled "
            "(`--ping=true` missing from command) → healthcheck never passes"
        )


def test_llama_llm_flash_attn_carries_required_value() -> None:
    """The b9049 llama-server build made --flash-attn take a REQUIRED enum value
    (on|off|auto). A bare --flash-attn swallows the following flag (--cache-type-k)
    as its value and crash-loops the container. Guard that --flash-attn is always
    immediately followed by a valid value token, never another flag (Правила #7)."""
    data = yaml.safe_load((SERVICES_DIR / "llama-llm.yaml").read_text(encoding="utf-8"))
    descriptor = ServiceDescriptor.model_validate(data)
    cmd = [str(c) for c in (descriptor.command or [])]
    valid = {"on", "off", "auto", "true", "false"}
    for i, tok in enumerate(cmd):
        # accept both --flash-attn on and --flash-attn=on
        if tok.startswith("--flash-attn="):
            assert tok.split("=", 1)[1].lower() in valid, f"bad --flash-attn value: {tok}"
            break
        if tok == "--flash-attn":
            nxt = cmd[i + 1] if i + 1 < len(cmd) else None
            assert nxt is not None and not nxt.startswith("-") and nxt.lower() in valid, (
                "llama-llm passes a bare --flash-attn; the b9049 image requires a value "
                f"(on|off/auto). Next token is {nxt!r} (a flag/missing) → server consumes "
                "it as the flash-attn value and crash-loops"
            )
            break


def test_llama_llm_healthcheck_start_period_allows_model_load() -> None:
    """A multi-GB LLM takes minutes to load into unified memory; the healthcheck
    start_period must be generous so docker does not mark llama-llm unhealthy mid-load
    and trigger a false deploy rollback (BREA02). The schema default (10s) is far too
    short — failures begin ~10s in and the container flips unhealthy ~100s in, long
    before a 35B GGUF has finished loading."""
    data = yaml.safe_load((SERVICES_DIR / "llama-llm.yaml").read_text(encoding="utf-8"))
    descriptor = ServiceDescriptor.model_validate(data)
    assert descriptor.health is not None, "llama-llm must declare a healthcheck"
    assert _duration_seconds(descriptor.health.start_period) >= 300, (
        f"llama-llm health.start_period={descriptor.health.start_period} too short "
        "for first-run model load"
    )


@pytest.mark.parametrize(
    "path",
    _service_files() or [pytest.param(Path("/dev/null"), marks=pytest.mark.skip)],
    ids=lambda p: p.stem,
)
def test_descriptor_has_schema_header(path: Path) -> None:
    """Каждый файл начинается с `# yaml-language-server: $schema=...` для VSCode."""
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("# yaml-language-server: $schema="), (
        f"{path.name} missing schema header (нужен для VSCode autocomplete)"
    )


def test_all_tiers_represented(service_files: list[Path]) -> None:
    """Sanity: каждый из 5 tiers использован хотя бы одним сервисом."""
    tiers: set[str] = set()
    for p in service_files:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        tiers.add(data["tier"])
    expected = {"edge", "inference", "app", "storage", "ops"}
    missing = expected - tiers
    assert not missing, f"tiers not used by any service: {missing}"


def test_no_duplicate_names(service_files: list[Path]) -> None:
    """Все имена сервисов уникальны (filename и descriptor.name)."""
    names = [p.stem for p in service_files]
    assert len(names) == len(set(names)), f"duplicates: {[n for n in names if names.count(n) > 1]}"


def _host_port_of(port_spec: str) -> str | None:
    """Extract host-side port from compose port spec `[ip:]host:container`."""
    parts = port_spec.split(":")
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 3:
        return parts[1]
    return None


def test_no_port_conflicts_within_profile(service_files: list[Path]) -> None:
    """Within each profile, host-side порты не должны конфликтовать.

    Альтернативные сервисы (nginx в `core-nginx` vs traefik в `core`) могут
    переиспользовать порты, потому что активируются раздельно.
    """
    # profile -> {host_port -> service_name}
    by_profile: dict[str, dict[str, str]] = {}

    for p in service_files:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        profiles = data.get("profiles") or []
        for port_spec in data.get("ports") or []:
            host_port = _host_port_of(port_spec)
            if host_port is None:
                continue
            for profile in profiles:
                slot = by_profile.setdefault(profile, {})
                if host_port in slot and slot[host_port] != p.stem:
                    pytest.fail(
                        f"port {host_port} конфликтует в profile '{profile}': "
                        f"{slot[host_port]} vs {p.stem}"
                    )
                slot[host_port] = p.stem


def test_depends_on_targets_exist(service_files: list[Path]) -> None:
    """Каждое имя в depends_on должно ссылаться на существующий сервис."""
    available = {p.stem for p in service_files}
    for p in service_files:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for dep in data.get("depends_on") or []:
            assert dep in available, f"{p.stem} depends on '{dep}' which is not defined"
