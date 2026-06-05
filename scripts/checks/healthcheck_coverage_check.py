#!/usr/bin/env python3
"""Gate A7: every service ships a Docker healthcheck OR is explicitly classified exempt.

The deploy runner counts a container with NO Docker healthcheck as healthy the moment it is
merely ``running`` (``agmind/deploy/runner.py`` — ``Health == "" → healthy if running``).
For a stateful / operator-facing service that overstates readiness: the stack can be declared
up while a web/db service is still warming, so a rollback-aware deploy may freeze a half-ready
stack. This gate makes the choice CONSCIOUS — a service either ships a ``health:`` probe, or is
listed below with a classified reason. Adding a new service without either FAILS the gate,
so readiness can never be skipped silently.

This gate does NOT change deploy behavior (changing the runner to fail un-probed services
would false-rollback every exempt service). It is the authoring-time discipline that makes
the runner's "running == ready" sound: by construction, only classified services lack a probe.

Categories: ``scraper`` (stateless exporter/agent — running is ready; Prometheus marks it
down if it stops), ``no-probe`` (no shell/HTTP readiness surface to exec), ``tech-debt``
(SHOULD ship a probe — tracked here, not silently passing).

Exit codes: 0 — full coverage; 1 — an un-probed, un-classified service (or a stale exemption).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# Services intentionally WITHOUT a Docker `health:` probe, each with a classified reason.
# Keep in sync with the catalog: a stale entry (service gained a probe, or no longer exists)
# also FAILS the gate so this list cannot rot.
_HEALTHCHECK_EXEMPT: dict[str, str] = {
    # scraper: stateless exporter/agent — "running" is "ready"; Prometheus/Loki surface a stop
    "alloy": "scraper: log/metric shipper, running == collecting; no readiness endpoint",
    "cadvisor": "scraper: stateless container-metrics exporter",
    "netdata": "scraper: stateless host-metrics agent (not in default selection)",
    "node-exporter": "scraper: stateless metrics exporter; Prometheus up == reachable",
    "postgres-exporter": "scraper: stateless metrics exporter",
    "proxmox-exporter": "scraper: stateless metrics exporter",
    "redis-exporter": "scraper: stateless metrics exporter",
    "watchtower": "sidecar: periodic image updater, no long-lived readiness state",
    # no-probe: no shell / no HTTP readiness surface to exec a probe against
    "ssrf-proxy": "no-probe: ubuntu/squid rock runs under pebble with no usable shell",
    "komodo-periphery": "no-probe: outbound agent (dials core over ws, listens 8120 self-signed TLS), no unauth liveness endpoint (ops profile, opt-in)",
    "dify-sandbox": "no-probe: internal code sandbox (ssrf-caged), no readiness endpoint",
    "dify-worker": "no-probe: celery worker, no HTTP readiness endpoint",
    # tech-debt: SHOULD ship a Docker health probe — tracked so it is not silently passing
    "alertmanager": "tech-debt: add /-/healthy probe",
    "authelia": "tech-debt: add /api/health probe",
    "dify-api": "tech-debt: add /health probe (or Traefik active healthcheck)",
    "dify-plugin-daemon": "tech-debt: add plugin-daemon health probe",
    "dify-web": "tech-debt: add web readiness probe",
    "docling": "tech-debt: add /health probe",
    "grafana": "tech-debt: add /api/health probe",
    "homarr": "tech-debt: add web readiness probe (not in default selection)",
    "loki": "tech-debt: add /ready probe",
    "milvus": "tech-debt: add health probe (distributed)",
    "openwebui": "tech-debt: add /health probe",
    "portainer": "tech-debt: add web readiness probe (not in default selection)",
    "prometheus": "tech-debt: add /-/healthy probe",
    "ragflow": "tech-debt: add probe (has Traefik healthcheck_path /api/v1/system/healthz)",
    "weaviate": "tech-debt: add /v1/.well-known/ready probe",
}


def _has_health(descriptor: Any) -> bool:
    return getattr(descriptor, "health", None) is not None


def check_coverage(descriptors: dict[str, Any] | None = None) -> list[str]:
    """Return a list of error messages (empty == full coverage).

    ``descriptors`` maps service-name → object with a ``.health`` attribute (defaults to the
    live catalog). An entry is a violation when it has no ``health`` and no exemption, or when
    an exemption is stale (service now has a probe, or no longer exists).
    """
    full_catalog = descriptors is None
    if descriptors is None:
        from agmind.services.renderer import load_descriptors

        descriptors = load_descriptors()

    errors: list[str] = []
    for name, descriptor in sorted(descriptors.items()):
        if _has_health(descriptor):
            if name in _HEALTHCHECK_EXEMPT:
                errors.append(
                    f"'{name}' now ships a healthcheck but is still listed exempt — "
                    f"remove it from _HEALTHCHECK_EXEMPT (stale exemption)."
                )
            continue
        if name not in _HEALTHCHECK_EXEMPT:
            errors.append(
                f"'{name}' has NO Docker healthcheck and is not classified. Add a `health:` "
                f"probe, or list it in healthcheck_coverage_check._HEALTHCHECK_EXEMPT with a "
                f"reason (scraper / no-probe / tech-debt). The deploy runner would otherwise "
                f"count it ready as soon as it is merely running."
            )

    # Only meaningful against the full live catalog; a synthetic subset (tests) is not stale.
    if full_catalog:
        catalog_names = set(descriptors)
        for name in sorted(_HEALTHCHECK_EXEMPT):
            if name not in catalog_names:
                errors.append(
                    f"exempt service '{name}' is not in the catalog — remove the stale exemption."
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    errors = check_coverage()
    for e in errors:
        print(f"ERROR {e}")
    if errors:
        print(f"\nA7 FAILED: {len(errors)} healthcheck-coverage violation(s).")
        return 1
    covered = "ships health: probe"
    exempt = len(_HEALTHCHECK_EXEMPT)
    print(f"A7 OK: every service either {covered} or is classified exempt ({exempt} exempt).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
