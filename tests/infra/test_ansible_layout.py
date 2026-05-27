"""Tests для ansible/ layout — YAML syntax + role structure."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_ANSIBLE_DIR = Path(__file__).resolve().parents[2] / "ansible"
_LOCAL_ANSIBLE_DIRS = {".galaxy", ".facts_cache"}


def _has_yaml_module() -> bool:
    try:
        import yaml  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark_yaml = pytest.mark.skipif(
    not _has_yaml_module(),
    reason="PyYAML not installed",
)


def test_ansible_dir_exists() -> None:
    assert _ANSIBLE_DIR.is_dir(), "ansible/ root must exist"


def test_ansible_config_present() -> None:
    assert (_ANSIBLE_DIR / "ansible.cfg").exists()


def test_inventory_present() -> None:
    assert (_ANSIBLE_DIR / "inventory" / "hosts.yml").exists()


def test_install_playbook_present() -> None:
    assert (_ANSIBLE_DIR / "install.yml").exists()


def test_requirements_collections_present() -> None:
    assert (_ANSIBLE_DIR / "requirements.yml").exists()


def test_group_vars_all_present() -> None:
    assert (_ANSIBLE_DIR / "group_vars" / "all.yml").exists()


@pytest.mark.parametrize(
    "role",
    [
        "preflight",
        "bootstrap",
        "strix_halo",
        "docker",
        "agmind_python",
        "models",
        "services",
        "observability",
        "security",
        "smoke_test",
    ],
)
def test_role_has_tasks_main(role: str) -> None:
    assert (_ANSIBLE_DIR / "roles" / role / "tasks" / "main.yml").exists()


@pytest.mark.skipif(not _has_yaml_module(), reason="PyYAML not installed")
def test_all_yaml_files_parse() -> None:
    import yaml

    errors = []
    for p in _ANSIBLE_DIR.rglob("*.yml"):
        if any(part in _LOCAL_ANSIBLE_DIRS for part in p.relative_to(_ANSIBLE_DIR).parts):
            continue
        try:
            with p.open() as f:
                yaml.safe_load(f)
        except Exception as e:
            errors.append(f"{p}: {e}")
    assert not errors, "YAML parse errors:\n" + "\n".join(errors)


@pytest.mark.skipif(not _has_yaml_module(), reason="PyYAML not installed")
def test_install_yml_imports_all_roles() -> None:
    """install.yml должен ссылаться на все 10 roles."""
    import yaml

    with (_ANSIBLE_DIR / "install.yml").open() as f:
        playbook = yaml.safe_load(f)

    referenced_roles: set[str] = set()
    for play in playbook:
        for role_block in play.get("roles", []) or []:
            if isinstance(role_block, str):
                referenced_roles.add(role_block)
            elif isinstance(role_block, dict):
                referenced_roles.add(role_block.get("role", ""))

    expected = {
        "preflight",
        "bootstrap",
        "strix_halo",
        "docker",
        "agmind_python",
        "models",
        "services",
        "observability",
        "security",
        "smoke_test",
    }
    assert expected.issubset(referenced_roles), (
        f"install.yml missing roles: {expected - referenced_roles}"
    )


@pytest.mark.skipif(not _has_yaml_module(), reason="PyYAML not installed")
def test_services_descriptors_loadable() -> None:
    """Phase H'.E: каждый `templates/services/*.yaml` имеет валидную структуру.

    После H'.E Ansible вызывает `agmind render compose` вместо lookup'а
    монолитного services.yaml (см. ADR-0006 + ADR-0008). Этот тест
    проверяет что split-файлы содержат ожидаемые ключи (image + name + tier).
    """
    import yaml

    services_dir = Path(__file__).resolve().parents[2] / "templates" / "services"
    assert services_dir.exists(), "templates/services/ directory missing"

    files = sorted(services_dir.glob("*.yaml"))
    assert len(files) > 0, "no service descriptor files found"

    for path in files:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict), f"{path.name} must be a mapping"
        assert "name" in data, f"{path.name} missing 'name'"
        assert "image" in data, f"{path.name} missing 'image'"
        assert "tier" in data, f"{path.name} missing 'tier'"
        assert data["name"] == path.stem, (
            f"{path.name}: filename stem != name field ({data['name']})"
        )
        if "profiles" in data:
            assert isinstance(data["profiles"], list)


def test_no_latest_tags_in_jinja_templates() -> None:
    """Templates НЕ должны рендерить :latest."""
    issues: list[str] = []
    for p in _ANSIBLE_DIR.rglob("*.j2"):
        text = p.read_text(encoding="utf-8")
        if ":latest" in text and "audit: allow" not in text:
            for i, line in enumerate(text.splitlines(), 1):
                if ":latest" in line and "audit: allow" not in line:
                    issues.append(f"{p}:{i}: {line.strip()}")
    assert not issues, "Templates render :latest tag:\n" + "\n".join(issues)


def test_docker_role_checks_compose_plugin_before_bootstrap() -> None:
    """Clean install must fix missing/old Compose plugin, not only missing docker."""
    text = (_ANSIBLE_DIR / "roles" / "docker" / "tasks" / "main.yml").read_text(encoding="utf-8")

    assert "docker compose version --short" in text
    assert "docker_compose_detected_version" in text
    assert "docker_compose_final_version" in text
    assert "2.24.0" in text
    assert "docker-compose-plugin" in text
    assert "Docker Compose plugin must be >= 2.24.0" in text


def test_inventory_default_is_localhost() -> None:
    """Default inventory — single-node localhost (no remote SSH)."""
    text = (_ANSIBLE_DIR / "inventory" / "hosts.yml").read_text()
    assert "localhost" in text
    assert "ansible_connection: local" in text
