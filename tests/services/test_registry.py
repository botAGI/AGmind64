"""Tests для agmind.services.registry."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agmind.services import (
    Service,
    ServiceProfile,
    list_services,
    load_registry,
    services_for_profile,
)
from agmind.services.registry import (
    _fallback_yaml_parse,
    _parse_yaml,
    validate_no_latest,
)

pytestmark = pytest.mark.backend_any

VALID_SHA256 = "a" * 64


# ---- ServiceProfile enum ----


def test_service_profile_values_stable() -> None:
    """Profile values — stable API; смена ломает Ansible playbooks + tests."""
    expected = {
        "core",
        "rag",
        "rag-milvus",
        "rag-weaviate",
        "ragflow",
        "ui",
        "automation",
        "observability",
        "ops",
        "proxmox",
        "security",
        "full",
    }
    assert {p.value for p in ServiceProfile} == expected


# ---- Service dataclass ----


def test_service_minimal_construction() -> None:
    s = Service(name="test", image="alpine:3.21")
    assert s.name == "test"
    assert s.image == "alpine:3.21"
    assert s.profiles == ()
    assert s.depends_on == ()
    assert s.cpus == 0.0
    assert s.mem_limit == ""


def test_service_fq_image_without_digest() -> None:
    s = Service(name="test", image="alpine:3.21")
    assert s.fq_image() == "alpine:3.21"


@pytest.mark.parametrize(
    "image",
    [
        "alpine",
        "registry.internal:5000/alpine",
        "alpine:",
    ],
)
def test_service_fq_image_rejects_image_without_tag_or_digest(image: str) -> None:
    s = Service(name="test", image=image)
    with pytest.raises(ValueError, match="no tag"):
        s.fq_image()


@pytest.mark.parametrize(
    "image",
    [
        "alpine:3.21\n",
        "alpine:3.21 ",
        "\talpine:3.21",
    ],
)
def test_service_fq_image_rejects_image_with_whitespace(image: str) -> None:
    s = Service(name="test", image=image)
    with pytest.raises(ValueError, match="whitespace"):
        s.fq_image()


@pytest.mark.parametrize(
    "image",
    [
        f"alpine:@sha256:{VALID_SHA256}",
        f"registry.internal:5000/alpine:@sha256:{VALID_SHA256}",
    ],
)
def test_service_fq_image_rejects_empty_tag_before_inline_digest(image: str) -> None:
    s = Service(name="test", image=image)
    with pytest.raises(ValueError, match="no tag"):
        s.fq_image()


@pytest.mark.parametrize(
    "image",
    [
        "alpine:latest",
        f"alpine:latest@sha256:{VALID_SHA256}",
    ],
)
def test_service_fq_image_rejects_latest_tag(image: str) -> None:
    s = Service(name="test", image=image)
    with pytest.raises(ValueError, match="latest"):
        s.fq_image()


def test_service_fq_image_with_digest() -> None:
    s = Service(name="test", image="alpine:3.21", digest=VALID_SHA256)
    assert s.fq_image() == f"alpine:3.21@sha256:{VALID_SHA256}"


def test_service_fq_image_normalizes_prefixed_digest() -> None:
    s = Service(name="test", image="alpine:3.21", digest=f"sha256:{VALID_SHA256}")
    assert s.fq_image() == f"alpine:3.21@sha256:{VALID_SHA256}"


def test_service_fq_image_rejects_invalid_digest() -> None:
    s = Service(name="test", image="alpine:3.21", digest="abc123")
    with pytest.raises(ValueError, match="sha256 digest"):
        s.fq_image()


@pytest.mark.parametrize(
    "digest",
    [
        VALID_SHA256 + "\n",
        f"sha256:{VALID_SHA256}\n",
    ],
)
def test_service_fq_image_rejects_digest_with_trailing_newline(digest: str) -> None:
    s = Service(name="test", image="alpine:3.21", digest=digest)
    with pytest.raises(ValueError, match="sha256 digest"):
        s.fq_image()


def test_service_fq_image_rejects_inline_digest_with_trailing_newline() -> None:
    s = Service(name="test", image=f"alpine:3.21@sha256:{VALID_SHA256}\n")
    with pytest.raises(ValueError, match="sha256 digest"):
        s.fq_image()


def test_service_fq_image_rejects_duplicate_digest_source() -> None:
    s = Service(
        name="test",
        image=f"alpine:3.21@sha256:{VALID_SHA256}",
        digest=VALID_SHA256,
    )
    with pytest.raises(ValueError, match="duplicate digest"):
        s.fq_image()


# ---- YAML parser fallback ----


def test_fallback_yaml_simple() -> None:
    text = "key: value\nother: 42\n"
    out = _fallback_yaml_parse(text)
    assert out == {"key": "value", "other": 42}


def test_fallback_yaml_nested() -> None:
    text = dedent("""
        services:
          alpha:
            image: alpine:3.21
            cpus: 2.0
    """).strip()
    out = _fallback_yaml_parse(text)
    assert "services" in out


def test_fallback_yaml_skips_comments() -> None:
    text = "# top comment\nkey: value\n# trailing\n"
    out = _fallback_yaml_parse(text)
    assert out == {"key": "value"}


def test_parse_yaml_uses_pyyaml_if_available() -> None:
    text = "key: value\nlist:\n  - a\n  - b\n"
    out = _parse_yaml(text)
    # Если pyyaml установлен — list parses correctly
    # Если fallback — list может не работать; проверка что не падает
    assert isinstance(out, dict)
    assert out.get("key") == "value"


# ---- load_registry ----


def test_load_registry_missing_file(tmp_path: Path) -> None:
    """Missing services.yaml → empty dict + warning, не raise."""
    out = load_registry(tmp_path / "missing.yaml")
    assert out == {}


def test_load_registry_real_services_yaml() -> None:
    """Реальный templates/services.yaml загружается без ошибок."""
    reg = load_registry()
    assert len(reg) > 0
    # Sanity: ключевые сервисы присутствуют
    assert "llama-llm" in reg
    assert "qdrant" in reg
    assert "postgres" in reg


def test_load_registry_minimal(tmp_path: Path) -> None:
    p = tmp_path / "services.yaml"
    p.write_text(
        dedent("""
        schema_version: 1
        services:
          alpha:
            image: alpine:3.21
            profiles:
              - core
    """).strip()
    )
    reg = load_registry(p)
    assert "alpha" in reg
    assert reg["alpha"].image == "alpine:3.21"


def test_load_registry_split_dir_fails_on_invalid_descriptor(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / "good.yaml").write_text(
        dedent("""
        name: good
        image: alpine:3.21
        tier: ops
        purpose: Valid service
        """).strip(),
        encoding="utf-8",
    )
    (services_dir / "bad.yaml").write_text(
        dedent("""
        name: bad
        image: alpine
        tier: ops
        purpose: Invalid service
        """).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bad.yaml"):
        load_registry(services_dir)


def test_load_registry_split_dir_rejects_duplicate_service_names(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    for filename, image in (
        ("alpha.yaml", "alpine:3.21"),
        ("beta.yaml", "alpine:3.22"),
    ):
        (services_dir / filename).write_text(
            dedent(f"""
            name: duplicate
            image: {image}
            tier: ops
            purpose: Duplicate service
            """).strip(),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="duplicate service name 'duplicate'"):
        load_registry(services_dir)


# ---- list_services ----


def test_list_services_returns_list() -> None:
    services = list_services()
    assert isinstance(services, list)
    assert all(isinstance(s, Service) for s in services)


# ---- services_for_profile ----


def test_services_for_profile_core() -> None:
    core = services_for_profile(ServiceProfile.CORE)
    names = {s.name for s in core}
    assert "llama-llm" in names
    assert "qdrant" in names


def test_services_for_profile_string_arg() -> None:
    """profile может быть str — функция конвертирует."""
    core_enum = services_for_profile(ServiceProfile.CORE)
    core_str = services_for_profile("core")
    assert {s.name for s in core_enum} == {s.name for s in core_str}


def test_services_for_profile_accepts_rag_alternative_profiles() -> None:
    milvus = {service.name for service in services_for_profile("rag-milvus")}
    weaviate = {service.name for service in services_for_profile("rag-weaviate")}

    assert "milvus" in milvus
    assert "weaviate" in weaviate


def test_services_for_profile_full_is_union() -> None:
    full = services_for_profile(ServiceProfile.FULL)
    core = services_for_profile(ServiceProfile.CORE)
    rag = services_for_profile(ServiceProfile.RAG)
    full_names = {s.name for s in full}
    assert {s.name for s in core}.issubset(full_names)
    assert {s.name for s in rag}.issubset(full_names)


def test_services_for_profile_proxmox_is_opt_in() -> None:
    proxmox = services_for_profile(ServiceProfile.PROXMOX)
    names = {s.name for s in proxmox}
    assert "proxmox-exporter" in names
    assert "proxmox-exporter" not in {
        s.name for s in services_for_profile(ServiceProfile.OBSERVABILITY)
    }


def test_services_for_profile_automation_is_opt_in() -> None:
    automation = services_for_profile(ServiceProfile.AUTOMATION)
    names = {s.name for s in automation}
    assert "n8n" in names
    assert "n8n" not in {s.name for s in services_for_profile(ServiceProfile.CORE)}


def test_services_for_profile_invalid_string() -> None:
    with pytest.raises(ValueError):
        services_for_profile("invalidprofile")


# ---- validate_no_latest ----


def test_validate_no_latest_passes_clean_registry() -> None:
    reg = load_registry()
    issues = validate_no_latest(reg)
    assert issues == [], "templates/services.yaml имеет :latest tag — pin specific semver."


def test_validate_no_latest_detects_violation() -> None:
    reg = {
        "bad": Service(name="bad", image="redis:latest"),
        "good": Service(name="good", image="redis:7.4.5-alpine"),
    }
    issues = validate_no_latest(reg)
    assert len(issues) == 1
    assert "bad" in issues[0]
    assert "good" not in issues[0]


def test_validate_no_latest_handles_tag_in_middle() -> None:
    """tag :latest должен попадать только если это весь tag, не подстрока."""
    reg = {
        "latest_prefix": Service(name="latest_prefix", image="my-latest-image:1.0"),
    }
    issues = validate_no_latest(reg)
    assert issues == []  # 'my-latest-image:1.0' — не :latest


# ---- Smoke: все services имеют осмысленный image ----


@pytest.mark.parametrize(
    "svc",
    [pytest.param(s, id=s.name) for s in list_services()] or [None],
)
def test_each_service_has_image(svc: Service | None) -> None:
    if svc is None:
        pytest.skip("No services in registry")
    assert ":" in svc.image, f"{svc.name} image '{svc.image}' must include :tag"
    assert not svc.image.endswith(":latest"), f"{svc.name} uses :latest"


@pytest.mark.parametrize(
    "svc",
    [pytest.param(s, id=s.name) for s in list_services()] or [None],
)
def test_each_service_has_profile_assignment(svc: Service | None) -> None:
    if svc is None:
        pytest.skip("No services in registry")
    valid_profiles = {p.value for p in ServiceProfile}
    # Allow alternative-of-base profiles: `rag-weaviate` (weaviate вместо qdrant),
    # `rag-milvus`, etc.
    alt_prefix = ("core-", "rag-", "ragflow-", "ui-")
    for p in svc.profiles:
        assert p in valid_profiles or p.startswith(alt_prefix), (
            f"{svc.name} has unknown profile {p!r}"
        )
