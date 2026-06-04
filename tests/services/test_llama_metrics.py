"""Regression: any llama.cpp service that advertises prometheus_scrape MUST pass
``--metrics`` to llama-server, or the /metrics endpoint 404s and every llamacpp:*
series (inference dashboards/rules/alerts) is empty (audit 2026-06-04, H#2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_prometheus_scraped_llama_services_pass_metrics_flag() -> None:
    descriptors = load_descriptors(Path("templates/services"))
    offenders = []
    for name, d in descriptors.items():
        if "llama.cpp" not in d.image:
            continue
        if not d.observability.prometheus_scrape:
            continue
        if "--metrics" not in (d.command or []):
            offenders.append(name)
    assert not offenders, (
        f"{offenders} set prometheus_scrape but omit --metrics → llama-server returns 404 "
        "on /metrics and the inference dashboards stay empty"
    )
