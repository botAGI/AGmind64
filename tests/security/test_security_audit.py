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


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses dir-traversal perms")
def test_scan_file_perms_skips_unreadable_path_without_crashing(tmp_path: Path) -> None:
    """A secret in a root-owned 0700 dir (e.g. /var/lib/agmind/secrets after a live deploy) is
    not traversable by the non-root audit user — scan_file_perms must SKIP it, not crash the
    whole `agmind security audit` with PermissionError (surfaced by a real post-deploy run)."""
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    token = secret_dir / "cf_dns_api_token"
    token.write_text("token", encoding="utf-8")
    os.chmod(token, 0o600)
    os.chmod(secret_dir, 0o000)  # deny traversal → stat(token) raises PermissionError
    try:
        result = scan_file_perms([token])  # must not raise
    finally:
        os.chmod(secret_dir, 0o700)  # restore so tmp cleanup can remove it
    assert result == []


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
    # Isolate data_dir into the sandbox so the scan never reaches the real /var/lib/agmind
    # (a live deploy's root-owned secrets there would otherwise leak into this unit test).
    findings, installed = audit_install(d, data_dir=tmp_path / "data")
    assert installed is True
    assert gate_exit(findings, block="high") == 0


def test_audit_install_exposed_port_blocks(tmp_path: Path) -> None:
    d = _install(tmp_path, "services:\n  api:\n    ports:\n      - 0.0.0.0:9000:9000\n")
    findings, _ = audit_install(d, data_dir=tmp_path / "data")
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


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file read perms")
def test_audit_unreadable_env_degrades_without_false_positives(tmp_path: Path) -> None:
    """Live deploy: /opt/agmind/.env is root:root 0600 — a non-root `agmind security audit`
    can't read it. It must NOT crash, must record an env-unreadable info finding, and must NOT
    false-flag descriptor `${GENERATED_VAR:-changeme}` defaults as weak (it can't see that the
    installer generated the override) — exactly the 4 phantom HIGH findings a live audit hit."""
    d = _install(tmp_path, "services:\n  dify-api: {}\n  dify-plugin-daemon: {}\n")
    env = d / ".env"
    os.chmod(env, 0o000)  # unreadable even to the owner (non-root)
    try:
        findings, installed = audit_install(d, data_dir=tmp_path / "data")
    finally:
        os.chmod(env, 0o600)  # restore so tmp cleanup works
    assert installed is True
    checks = [f.check for f in findings]
    assert "env-unreadable" in checks, checks
    # surfaced as a WARNING (the secret-value scan was skipped), not a buried info finding
    unreadable = next(f for f in findings if f.check == "env-unreadable")
    assert unreadable.severity == "warning", unreadable.severity
    assert "weak-secret" not in checks, f"unreadable .env must not false-flag defaults: {checks}"


def test_audit_flags_loosely_permed_credentials_file(tmp_path: Path) -> None:
    """Review MEDIUM security-scan-omits-secret-files: a world-readable credentials.txt /
    cf_dns_api_token must be flagged, not just .env — the scan catches operator perms drift."""
    d = _install(tmp_path, "services:\n  db:\n    ports:\n      - 127.0.0.1:5432:5432\n")
    creds = d / "credentials.txt"
    creds.write_text("admin: hunter2\n", encoding="utf-8")
    os.chmod(creds, 0o644)  # drifted from 0600
    data_dir = tmp_path / "data"
    token = data_dir / "secrets" / "cf_dns_api_token"
    token.parent.mkdir(parents=True)
    token.write_text("cf-token\n", encoding="utf-8")
    os.chmod(token, 0o644)

    findings, _ = audit_install(d, data_dir=data_dir)
    flagged = {str(f.target) for f in findings if f.check == "file-perms"}
    assert str(creds) in flagged, flagged
    assert str(token) in flagged, flagged


def test_audit_cli_live_fails_fast(tmp_path: Path) -> None:
    """Review LOW security-live-stub: --live is not implemented — it must exit 2, not append
    a fake 'live verified' finding."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    d = _install(tmp_path, "services:\n  db:\n    ports:\n      - 127.0.0.1:5432:5432\n")
    result = CliRunner().invoke(
        _make_app(), ["security", "audit", "--install-dir", str(d), "--live"]
    )
    assert result.exit_code == 2
    assert "not yet implemented" in result.output.lower()


def test_security_audit_cli_not_installed_exits_2(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    result = CliRunner().invoke(
        _make_app(), ["security", "audit", "--install-dir", str(tmp_path / "nope")]
    )
    assert result.exit_code == 2
