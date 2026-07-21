"""Cloudflare DNS-01 token validation step.

Split out of the historical single-file ``agmind/install/steps.py``; every name
here is re-exported from the package ``__init__``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from agmind.install.orchestrator import (
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressKind,
)

from ._common import _make_event
from .configs import _redact_install_secrets

_CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"


def _cloudflare_zone_candidates(domain: str) -> list[str]:
    labels = [part for part in domain.strip().strip(".").lower().split(".") if part]
    if len(labels) < 2:
        return []
    return [".".join(labels[index:]) for index in range(0, len(labels) - 1)]


def _cloudflare_payload_errors(payload: dict[str, object], status: int) -> str:
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors:
        return f"HTTP {status}"
    parts: list[str] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        message = item.get("message")
        if code and message:
            parts.append(f"{code}: {message}")
        elif message:
            parts.append(str(message))
        elif code:
            parts.append(str(code))
    return "; ".join(parts) if parts else f"HTTP {status}"


def _cloudflare_request_json(
    token: str,
    path: str,
    query: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    url = f"{_CLOUDFLARE_API_BASE}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "agmind-installer/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = int(exc.code)
    except (OSError, urllib.error.URLError) as exc:
        raise ConnectionError(f"Cloudflare API request failed: {exc}") from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cloudflare API returned invalid JSON (HTTP {status})") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Cloudflare API returned non-object JSON (HTTP {status})")
    return status, payload


# ---------- Step 2: Cloudflare token ----------


class CloudflareTokenStep(InstallStep):
    """Validate the DNS-01 token before deploy can reach the ACME path."""

    step_id = "cloudflare_token"
    label = "Validate Cloudflare DNS token"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if "traefik" not in set(config.services):
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message="traefik not selected — Cloudflare token not needed",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        if len(config.cf_api_token.strip()) < 20:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="Cloudflare token missing or too short",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        try:
            status, payload = _cloudflare_request_json(
                config.cf_api_token,
                "/user/tokens/verify",
            )
        except (ConnectionError, ValueError) as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=_redact_install_secrets(str(exc), config),
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        if status != 200 or payload.get("success") is not True:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=_redact_install_secrets(
                    f"Cloudflare token validation failed: "
                    f"{_cloudflare_payload_errors(payload, status)}",
                    config,
                ),
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        candidates = _cloudflare_zone_candidates(config.domain)
        if not candidates:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"cannot derive Cloudflare zone candidate from domain {config.domain}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        for candidate in candidates:
            callback(
                _make_event(
                    self.step_id,
                    ProgressKind.LOG,
                    f"checking Cloudflare zone access: {candidate}",
                )
            )
            try:
                zone_status, zone_payload = _cloudflare_request_json(
                    config.cf_api_token,
                    "/zones",
                    {"name": candidate, "status": "active", "per_page": "1"},
                )
            except (ConnectionError, ValueError) as exc:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=_redact_install_secrets(str(exc), config),
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            if zone_status != 200 or zone_payload.get("success") is not True:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=_redact_install_secrets(
                        f"Cloudflare zone lookup failed for {candidate}: "
                        f"{_cloudflare_payload_errors(zone_payload, zone_status)}",
                        config,
                    ),
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            zones = zone_payload.get("result")
            if isinstance(zones, list) and zones:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=True,
                    message=f"Cloudflare token valid; zone access OK ({candidate})",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        return InstallStepResult(
            step_id=self.step_id,
            success=False,
            message=(
                "Cloudflare token is valid but cannot access an active Cloudflare zone "
                f"for domain {config.domain} (tried: {', '.join(candidates)})"
            ),
            elapsed=timedelta(seconds=time.monotonic() - start),
        )
