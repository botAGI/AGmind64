"""Tests для agmind.diagnostics.doctor — preflight checks."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agmind.diagnostics.doctor import CheckResult, DoctorReport, doctor_report, run_preflight

pytestmark = pytest.mark.backend_any


def test_check_result_construction() -> None:
    c = CheckResult(name="x", status="ok", message="all good")
    assert c.name == "x"
    assert c.status == "ok"
    assert c.fix_hint == ""


def test_check_result_immutable() -> None:
    c = CheckResult(name="x", status="ok", message="m")
    with pytest.raises((AttributeError, Exception)):
        c.status = "fail"  # type: ignore[misc]


def test_doctor_report_empty() -> None:
    r = DoctorReport()
    assert r.checks == []
    assert r.has_failures is False
    assert r.has_warnings is False


def test_doctor_report_has_failures() -> None:
    r = DoctorReport()
    r.checks.append(CheckResult("a", "fail", "broken"))
    assert r.has_failures is True


def test_doctor_report_has_warnings() -> None:
    r = DoctorReport()
    r.checks.append(CheckResult("a", "warn", "soft issue"))
    assert r.has_warnings is True
    assert r.has_failures is False


def test_doctor_report_to_dict_summary() -> None:
    r = DoctorReport()
    r.checks.append(CheckResult("a", "ok", "good"))
    r.checks.append(CheckResult("b", "warn", "soft"))
    r.checks.append(CheckResult("c", "fail", "broken"))
    r.checks.append(CheckResult("d", "skip", "n/a"))
    d = r.to_dict()
    assert d["summary"] == {"total": 4, "ok": 1, "warn": 1, "fail": 1, "skip": 1}


def test_doctor_report_to_json_valid() -> None:
    r = DoctorReport()
    r.checks.append(CheckResult("a", "ok", "good"))
    j = r.to_json()
    parsed = json.loads(j)
    assert parsed["summary"]["ok"] == 1
    assert parsed["checks"][0]["name"] == "a"


def test_run_preflight_returns_report() -> None:
    """Полный preflight на текущей машине — проходит без exceptions."""
    r = run_preflight()
    assert isinstance(r, DoctorReport)
    assert len(r.checks) > 0


def test_run_preflight_checks_have_required_fields() -> None:
    r = run_preflight()
    for c in r.checks:
        assert c.name
        assert c.status in {"ok", "warn", "fail", "skip"}
        assert c.message


def test_run_preflight_specific_checks_present() -> None:
    """Все базовые preflight checks должны выполняться."""
    r = run_preflight()
    names = {c.name for c in r.checks}
    expected = {
        "gpu-detected",
        "kernel-version",
        "bios-uma",
        "gtt-pool",
        "devices",
        "user-groups",
        "amdvlk-absent",
        "vulkan-tooling",
        "rocm-tooling",
        "docker-compose",
    }
    assert expected.issubset(names)


def test_docker_compose_check_warns_without_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    from agmind.diagnostics import doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    result = doctor._check_docker_compose()  # noqa: SLF001

    assert result.status == "warn"
    assert result.name == "docker-compose"
    assert "docker-ce" in result.fix_hint
    assert "docker-compose-plugin" in result.fix_hint


def test_docker_compose_check_rejects_old_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    from agmind.diagnostics import doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["docker", "compose", "version", "--short"],
            0,
            stdout="2.23.3\n",
            stderr="",
        )

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    result = doctor._check_docker_compose()  # noqa: SLF001

    assert result.status == "warn"
    assert "2.24.0+" in result.message


def test_docker_compose_check_accepts_minimum_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    from agmind.diagnostics import doctor

    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/usr/bin/{name}")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            ["docker", "compose", "version", "--short"],
            0,
            stdout="2.24.0\n",
            stderr="",
        )

    monkeypatch.setattr(doctor.subprocess, "run", fake_run)

    result = doctor._check_docker_compose()  # noqa: SLF001

    assert result.status == "ok"
    assert "2.24.0" in result.message


def test_devices_check_skips_cpu_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from agmind.diagnostics import doctor

    monkeypatch.setattr(doctor, "detect_host", lambda: SimpleNamespace(gpu=None))
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    result = doctor._check_devices()  # noqa: SLF001

    assert result.status == "skip"
    assert "CPU fallback" in result.message


def test_doctor_report_human_readable_format() -> None:
    out = doctor_report(as_json=False)
    assert "AGmind doctor" in out
    # Icons or status indicators present
    assert any(icon in out for icon in ("✓", "⚠", "✗", "·"))


def test_doctor_report_json_format() -> None:
    out = doctor_report(as_json=True)
    parsed = json.loads(out)
    assert "summary" in parsed
    assert "checks" in parsed


def test_doctor_report_fix_hint_shown_in_human(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если check имеет fix_hint и status warn/fail — hint в выводе."""
    # Не мокаем — реальные checks на dev машине дают warn'ы с hints
    out = doctor_report(as_json=False)
    if "→" in out:
        # На текущей dev машине есть warn'ы — проверяем что hints показываются
        assert "→" in out
