"""Tests for README.md / README.ru.md mirror drift guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECK_SCRIPT = _REPO_ROOT / "scripts" / "checks" / "docs_mirror_check.py"


def _run_docs_check(*args: str, cwd: Path = _REPO_ROOT) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(_CHECK_SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def test_docs_mirror_check_accepts_current_readmes() -> None:
    code, out = _run_docs_check()

    assert code == 0, out
    assert "README mirror OK" in out


def test_docs_mirror_check_json_output_includes_counts() -> None:
    code, out = _run_docs_check("--json")

    assert code == 0, out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["error_count"] == 0
    # README: 11 headings, 4 code blocks (3 shell + 1 mermaid). The 2026-06-13 polish added a
    # "How it compares" section (+1 heading) and a mermaid architecture diagram (+1 code block);
    # the check excludes `#` bash comments inside ``` fences (see docs_mirror_check._heading_topology).
    assert payload["heading_count"] == 11
    assert payload["code_block_count"] == 4


def test_docs_mirror_check_rejects_shell_command_drift(tmp_path: Path) -> None:
    en = tmp_path / "README.md"
    ru = tmp_path / "README.ru.md"
    en.write_text(
        "# Demo\n\n"
        "## Install\n\n"
        "```bash\n"
        "# English comment may differ.\n"
        "agmind setup\n"
        "agmind verify install --domain lab.example.com\n"
        "```\n",
        encoding="utf-8",
    )
    ru.write_text(
        "# Demo\n\n"
        "## Установка\n\n"
        "```bash\n"
        "# Русский комментарий может отличаться.\n"
        "agmind setup\n"
        "agmind verify install --domain prod.example.com\n"
        "```\n",
        encoding="utf-8",
    )

    code, out = _run_docs_check("--english", str(en), "--russian", str(ru))

    assert code == 1
    assert "code block 1 differs" in out
    assert "prod.example.com" in out


def test_docs_mirror_check_rejects_section_topology_drift(tmp_path: Path) -> None:
    en = tmp_path / "README.md"
    ru = tmp_path / "README.ru.md"
    en.write_text("# Demo\n\n## Install\n\n## Operate\n", encoding="utf-8")
    ru.write_text("# Demo\n\n## Установка\n\n### Операции\n", encoding="utf-8")

    code, out = _run_docs_check("--english", str(en), "--russian", str(ru))

    assert code == 1
    assert "heading topology differs" in out


def test_docs_mirror_check_json_reports_errors(tmp_path: Path) -> None:
    en = tmp_path / "README.md"
    ru = tmp_path / "README.ru.md"
    en.write_text("# Demo\n\n```bash\nagmind setup\n```\n", encoding="utf-8")
    ru.write_text("# Demo\n\n```bash\nagmind doctor\n```\n", encoding="utf-8")

    code, out = _run_docs_check("--json", "--english", str(en), "--russian", str(ru))

    assert code == 1
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error_count"] == 1
    assert "code block 1 differs" in payload["issues"][0]["message"]
