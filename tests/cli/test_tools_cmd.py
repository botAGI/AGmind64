from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.backend_any


def test_tools_list_json_includes_optional_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import tools_cmd

    rc = tools_cmd.cmd_list(as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    proxmox = next(item for item in payload["tools"] if item["id"] == "proxmox-exporter")
    assert proxmox["status"] == "accepted"
    assert proxmox["admission"]["scope"] == "service-profile"
    assert "proxmox" in proxmox["profiles"]
    assert "9221" in proxmox["ports"]
    longhorn = next(item for item in payload["tools"] if item["id"] == "longhorn")
    assert longhorn["recommended_version"] == "1.11.2"
    assert payload["summary"]["accepted"] >= 1


def test_tools_status_reports_admission_for_accepted_candidate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import tools_cmd

    rc = tools_cmd.cmd_status("proxmox-exporter")

    assert rc == 0
    out = capsys.readouterr().out
    assert "proxmox-exporter" in out
    assert "Status: accepted" in out
    assert "Admission: OK" in out
    assert "agmind render compose --profile core,observability,proxmox" in out


def test_tools_status_missing_candidate_returns_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import tools_cmd

    rc = tools_cmd.cmd_status("missing-tool")

    assert rc == 1
    assert "Tool candidate 'missing-tool' not found" in capsys.readouterr().err


def test_tools_validate_uses_runtime_admission_gate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import tools_cmd

    rc = tools_cmd.cmd_validate()

    assert rc == 0
    assert "tool candidates OK" in capsys.readouterr().out
