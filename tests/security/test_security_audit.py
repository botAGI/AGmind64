"""agmind security audit — read-only posture scan of the DEPLOYED artifacts.

Scoped to operator drift in /opt/agmind (hand-edited compose, weak operator .env,
world-readable secret files) — NOT a re-check of the catalog (the build-time
contract test already gates that). Pure scanners are unit-tested on synthetic
fixtures; the audit never prints a secret value (SC2 invariant).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agmind.security.audit import (
    SEVERITY_LEVELS,
    SecurityFinding,
    audit_install,
    gate_exit,
    scan_compose,
    scan_env,
    scan_file_perms,
)

pytestmark = pytest.mark.backend_any


# ---- scan_compose ----


def test_loopback_bind_is_not_flagged() -> None:
    compose = "services:\n  db:\n    ports:\n      - 127.0.0.1:5432:5432\n"
    assert scan_compose(compose) == []


def test_explicit_0000_bind_flagged() -> None:
    compose = "services:\n  api:\n    ports:\n      - 0.0.0.0:9000:9000\n"
    findings = scan_compose(compose)
    assert any(f.check == "exposed-port" for f in findings)


def test_bare_hostport_implicit_all_interfaces_flagged() -> None:
    compose = "services:\n  api:\n    ports:\n      - 8080:8080\n"
    findings = scan_compose(compose)
    assert any(f.check == "exposed-port" for f in findings)


def test_privileged_is_critical() -> None:
    compose = "services:\n  x:\n    privileged: true\n"
    findings = scan_compose(compose)
    priv = [f for f in findings if f.check == "privileged"]
    assert priv and priv[0].severity == "critical"


def test_docker_sock_rw_is_high_ro_is_medium() -> None:
    rw = scan_compose(
        "services:\n  a:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n"
    )
    ro = scan_compose(
        "services:\n  b:\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock:ro\n"
    )
    rw_f = [f for f in rw if f.check == "docker-sock"]
    ro_f = [f for f in ro if f.check == "docker-sock"]
    assert rw_f and rw_f[0].severity == "high"
    assert ro_f and ro_f[0].severity == "medium"


def test_command_arg_listen_addr_is_not_a_false_positive() -> None:
    # alloy exposes 0.0.0.0:12345 as a COMMAND arg, not a published port — a
    # structural YAML parse must not flag it (a raw-text grep would).
    compose = (
        "services:\n  alloy:\n"
        "    command:\n      - --server.http.listen-addr=0.0.0.0:12345\n"
        "    ports:\n      - 127.0.0.1:12345:12345\n"
    )
    assert [f for f in scan_compose(compose) if f.check == "exposed-port"] == []


# ---- scan_env ----


def _descriptors_with_weak_default():
    from agmind.schemas import ServiceDescriptor

    desc = ServiceDescriptor(
        name="dify-api",
        image="example/dify:1.0.0",
        tier="app",
        env={"SECRET_KEY": "${DIFY_SECRET_KEY:-changeme-weak}"},
    )
    return {"dify-api": desc}


def test_weak_descriptor_default_flagged_without_leaking_value() -> None:
    findings = scan_env({}, _descriptors_with_weak_default())
    assert any(f.check == "weak-secret" for f in findings)


def test_operator_weak_env_value_flagged() -> None:
    findings = scan_env({"POSTGRES_PASSWORD": "admin"}, {})
    assert any(f.check == "weak-secret" for f in findings)


def test_strong_env_value_not_flagged() -> None:
    findings = scan_env({"POSTGRES_PASSWORD": "x9F2qWlk38aZpQ7vBmN1"}, {})
    assert findings == []


def test_scan_env_never_emits_the_secret_value() -> None:
    secret = "admin"  # weak but must still never be echoed
    findings = scan_env({"GRAFANA_PASSWORD": secret}, {})
    for f in findings:
        blob = f"{f.target} {f.detail} {f.fix}"
        assert secret not in blob, "audit must never print the secret value"


# ---- scan_file_perms ----


def test_private_secret_file_ok(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("GRAFANA_PASSWORD=x9F2qWlk38aZpQ7vBmN1\n", encoding="utf-8")
    os.chmod(env, 0o600)
    assert scan_file_perms([env]) == []


def test_world_readable_secret_file_flagged(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("GRAFANA_PASSWORD=x9F2qWlk38aZpQ7vBmN1\n", encoding="utf-8")
    os.chmod(env, 0o644)
    findings = scan_file_perms([env])
    assert findings and findings[0].check == "file-perms"
    assert findings[0].severity in ("high", "medium")


# ---- gate / exit ----


def test_gate_exit_blocks_at_threshold() -> None:
    findings = [SecurityFinding("exposed-port", "high", "api", "0.0.0.0", "bind loopback")]
    assert gate_exit(findings, block="high") == 1
    assert gate_exit(findings, block="critical") == 0
    assert gate_exit([], block="high") == 0


def test_severity_levels_ordered() -> None:
    assert SEVERITY_LEVELS == ("info", "low", "medium", "high", "critical")


# ---- audit_install + CLI ----


def _install(tmp_path: Path, compose: str) -> Path:
    d = tmp_path / "opt"
    d.mkdir()
    (d / "docker-compose.yml").write_text(compose, encoding="utf-8")
    env = d / ".env"
    env.write_text("GRAFANA_PASSWORD=x9F2qWlk38aZpQ7vBmN1\n", encoding="utf-8")
    os.chmod(env, 0o600)
    return d


def test_audit_install_not_installed_returns_uninstalled(tmp_path: Path) -> None:
    findings, installed = audit_install(tmp_path / "nope")
    assert installed is False


def test_audit_install_clean_has_no_blocking_findings(tmp_path: Path) -> None:
    d = _install(tmp_path, "services:\n  db:\n    ports:\n      - 127.0.0.1:5432:5432\n")
    findings, installed = audit_install(d)
    assert installed is True
    assert gate_exit(findings, block="high") == 0


def test_audit_install_exposed_port_blocks(tmp_path: Path) -> None:
    d = _install(tmp_path, "services:\n  api:\n    ports:\n      - 0.0.0.0:9000:9000\n")
    findings, _ = audit_install(d)
    assert gate_exit(findings, block="high") == 1


def test_security_audit_cli_json_and_exit(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    d = _install(tmp_path, "services:\n  api:\n    ports:\n      - 0.0.0.0:9000:9000\n")
    result = CliRunner().invoke(
        _make_app(), ["security", "audit", "--install-dir", str(d), "--json"]
    )
    assert result.exit_code == 1  # exposed port blocks at default --block high
    payload = json.loads(result.output)
    assert payload["findings"]
    assert any(f["check"] == "exposed-port" for f in payload["findings"])


def test_security_audit_cli_not_installed_exits_2(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    result = CliRunner().invoke(
        _make_app(), ["security", "audit", "--install-dir", str(tmp_path / "nope")]
    )
    assert result.exit_code == 2
