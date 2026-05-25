from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agmind.deploy.proxmox_exporter import (
    probe_exporter,
    render_validation_summary,
    validate_pve_config,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "proxmox_exporter_check.py"
SERVICES_TASKS = REPO_ROOT / "ansible" / "roles" / "services" / "tasks" / "main.yml"


def _write_config(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _valid_config(path: Path) -> Path:
    return _write_config(
        path,
        "\n".join(
            [
                "default:",
                "  user: prometheus@pve",
                "  token_name: agmind",
                "  token_value: fake-token-value-for-tests",
                "  verify_ssl: true",
            ]
        )
        + "\n",
    )


def test_validate_pve_config_accepts_token_auth(tmp_path: Path) -> None:
    result = validate_pve_config(_valid_config(tmp_path / "pve.yml"))

    assert result.ok is True
    assert result.errors == ()
    assert result.warnings == ()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("default: {}\n", "missing required token fields"),
        (
            "default:\n  user: prometheus@pve\n  token_name: agmind\n",
            "token_value",
        ),
        (
            "default:\n  user: prometheus@pve\n  token_name: agmind\n  token_value: <PVE_TOKEN_VALUE>\n",
            "placeholder",
        ),
        (
            "default:\n  user: prometheus@pve\n  password: super-secret-password\n",
            "password auth",
        ),
    ],
)
def test_validate_pve_config_rejects_unsafe_configs(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    result = validate_pve_config(_write_config(tmp_path / "pve.yml", body))

    assert result.ok is False
    joined = "\n".join(result.errors)
    assert message in joined
    assert "super-secret-password" not in joined


def test_validate_pve_config_reports_missing_file_without_traceback(tmp_path: Path) -> None:
    result = validate_pve_config(tmp_path / "missing.yml")

    assert result.ok is False
    assert "does not exist" in "\n".join(result.errors)


def test_render_validation_summary_is_operator_readable(tmp_path: Path) -> None:
    result = validate_pve_config(_valid_config(tmp_path / "pve.yml"))

    assert render_validation_summary(result) == "Proxmox exporter config OK"


def test_script_exits_zero_for_valid_config(tmp_path: Path) -> None:
    config = _valid_config(tmp_path / "pve.yml")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "config OK" in result.stdout


def test_script_exits_nonzero_for_invalid_config(tmp_path: Path) -> None:
    config = _write_config(tmp_path / "pve.yml", "default: {}\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "missing required token fields" in result.stderr


def test_probe_exporter_builds_pve_query_and_accepts_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"# HELP pve_version_info Proxmox version\npve_version_info 1\n"

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        seen["url"] = request.full_url
        seen["timeout"] = str(timeout)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = probe_exporter(
        endpoint="http://127.0.0.1:9221",
        target="192.168.1.10",
        module="default",
        timeout=2.5,
    )

    assert result.ok is True
    assert seen["url"] == "http://127.0.0.1:9221/pve?module=default&target=192.168.1.10"
    assert seen["timeout"] == "2.5"


def test_services_role_invokes_proxmox_validator_before_compose_render() -> None:
    import yaml

    tasks = yaml.safe_load(SERVICES_TASKS.read_text(encoding="utf-8"))
    names = [str(task.get("name", "")) for task in tasks]

    validate_index = names.index("Validate Proxmox exporter config")
    render_index = names.index("Render docker-compose.yml через agmind render compose")
    assert validate_index < render_index

    task = tasks[validate_index]
    command = task["ansible.builtin.command"]["cmd"]
    assert "python -m agmind.deploy.proxmox_exporter" in command
    assert "--config {{ agmind_config_dir }}/proxmox-exporter/pve.yml" in command
    assert "'proxmox' in agmind_profiles" in " ".join(task["when"])
