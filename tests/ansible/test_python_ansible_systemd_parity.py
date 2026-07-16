"""F4: agmind/install/steps.py generates 3 systemd unit strings that duplicate the
ansible .j2 templates under ansible/roles/*/templates/ (steps.py's own boot-unit /
GPU-metrics steps port units that previously existed ONLY in the ansible services/
observability roles — the Python installer never runs those roles). Both sides are
functionally byte-identical today modulo comment wording and 2 known substitutions
({{ agmind_install_dir }} -> install_dir, and the profiles for-loop). There was
previously no automated guard tying the two together, so a one-sided edit to either
the python f-string or the .j2 template would silently drift without failing CI —
this test locks that in permanently, normalizing both sides before comparison."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO = Path(__file__).resolve().parents[2]

_INSTALL_DIR = "/opt/agmind"
_PROFILES = ["core", "observability"]
_PROFILE_FLAGS = "".join(f"--profile {profile} " for profile in _PROFILES)


def _strip_comments_and_blanks(text: str) -> list[str]:
    """Drop every line whose stripped form starts with '#' (comment wording legitimately
    differs between the two sides) and every blank line left behind by that removal."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped:
            continue
        lines.append(line)
    return lines


def _normalize_ansible_unit(raw: str) -> list[str]:
    """Substitute the 2 known ansible-only tokens with the exact values fed to the
    python-side function under test, then apply the same comment/blank-line strip."""
    substituted = raw.replace("{{ agmind_install_dir }}", _INSTALL_DIR)
    substituted = re.sub(
        r"\{% for p in agmind_profiles %\}--profile \{\{ p \}\} \{% endfor %\}",
        _PROFILE_FLAGS,
        substituted,
    )
    return _strip_comments_and_blanks(substituted)


def test_agmind_stack_unit_matches_services_role_template() -> None:
    from agmind.install.steps import _agmind_stack_unit

    python_lines = _strip_comments_and_blanks(_agmind_stack_unit(_INSTALL_DIR, _PROFILES))
    ansible_path = (
        _REPO / "ansible" / "roles" / "services" / "templates" / "agmind-stack.service.j2"
    )
    ansible_lines = _normalize_ansible_unit(ansible_path.read_text(encoding="utf-8"))

    assert python_lines == ansible_lines


def test_gpu_metrics_service_unit_matches_observability_role_template() -> None:
    from agmind.install.steps import _gpu_metrics_service_unit

    script_path = f"{_INSTALL_DIR}/scripts/ops/amdgpu_textfile.sh"
    python_lines = _strip_comments_and_blanks(_gpu_metrics_service_unit(script_path))
    ansible_path = (
        _REPO / "ansible" / "roles" / "observability" / "templates" / "amdgpu-metrics.service.j2"
    )
    ansible_lines = _normalize_ansible_unit(ansible_path.read_text(encoding="utf-8"))

    assert python_lines == ansible_lines


def test_gpu_metrics_timer_unit_matches_observability_role_template() -> None:
    from agmind.install.steps import _GPU_METRICS_TIMER_UNIT

    python_lines = _strip_comments_and_blanks(_GPU_METRICS_TIMER_UNIT)
    ansible_path = (
        _REPO / "ansible" / "roles" / "observability" / "templates" / "amdgpu-metrics.timer.j2"
    )
    ansible_lines = _normalize_ansible_unit(ansible_path.read_text(encoding="utf-8"))

    assert python_lines == ansible_lines
