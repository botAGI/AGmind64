#!/usr/bin/env python3
"""Gate A8: zero-egress telemetry — every service that CAN disable phone-home declaratively
ships the verified telemetry-kill env key, and UI-only opt-out services are classified exempt.

Many third-party images POST anonymous usage stats or poll an update endpoint on a timer by
default (qdrant → telemetry.qdrant.io, grafana → stats.grafana.org, ChromaDB-in-open-webui →
PostHog, dify → updates.dify.ai / marketplace.dify.ai, …). For an air-gap-clean default those
must be turned off at authoring time. This gate makes the choice CONSCIOUS, mirroring the A7
healthcheck-coverage discipline: a service either carries the verified env key=value, or is
listed in _EGRESS_EXEMPT with a reason (its telemetry has NO env knob — it is UI-only opt-out,
so it can only be contained at the network layer).

Fail-closed on:
  - a required key MISSING from a descriptor's env;
  - a required key present but the WRONG value;
  - a STALE exemption (an exempt UI-only service that gained a required env knob — move it to
    _REQUIRED_EGRESS_ENV);
  - a STALE rule (a required-env / exempt service that left the catalog — full-catalog only).

Every key/value here is verified (2026-06-09) against the pinned image's upstream docs/source —
see the per-descriptor `# zero-egress:` WHY-comments. Keep this gate in lock-step with those.

Exit codes: 0 — every required key present with the right value, no stale entry; 1 — otherwise.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# service -> {env_key: required_value}. Each entry mirrors a `# zero-egress:` WHY-comment in the
# descriptor and was verified against the pinned image (not guessed). Adding a telemetry-disable
# key to a descriptor WITHOUT recording it here means the gate cannot enforce it on a re-edit.
_REQUIRED_EGRESS_ENV: dict[str, dict[str, str]] = {
    # qdrant POSTs anonymized usage stats to telemetry.qdrant.io unless disabled.
    "qdrant": {"QDRANT__TELEMETRY_DISABLED": "true"},
    # phoenix: PHOENIX_TELEMETRY_ENABLED defaults true (browser product analytics); pinned false
    # here at the v19 bump (was a pre-existing gap since 17.2.0).
    "phoenix": {"PHOENIX_TELEMETRY_ENABLED": "false"},
    # weaviate POSTs anonymous telemetry to a hosted endpoint on a timer unless disabled.
    "weaviate": {"DISABLE_TELEMETRY": "true"},
    # grafana: all four — reporting_enabled alone still lets check_for_updates hit
    # stats.grafana.org and check_for_plugin_updates hit grafana.com.
    "grafana": {
        "GF_ANALYTICS_REPORTING_ENABLED": "false",
        "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
        "GF_ANALYTICS_FEEDBACK_LINKS_ENABLED": "false",
    },
    # open-webui: kill bundled-Chroma PostHog + Scarf egress + HF auto-download.
    "openwebui": {
        "ANONYMIZED_TELEMETRY": "False",
        "DO_NOT_TRACK": "True",
        "SCARF_NO_ANALYTICS": "True",
        "OFFLINE_MODE": "True",
    },
    # dify-api/console: CHECK_UPDATE_URL='' disables the updates.dify.ai poll;
    # MARKETPLACE_ENABLED=false stops the marketplace.dify.ai calls.
    "dify-api": {"CHECK_UPDATE_URL": "", "MARKETPLACE_ENABLED": "false"},
    # dify-plugin-daemon: kill the Sentry crash-reporting egress (NOT signature verification).
    "dify-plugin-daemon": {"PLUGIN_SENTRY_ENABLED": "false"},
    # netdata: anonymous-statistics phone-home + Netdata Cloud claim — already shipped, pinned
    # here so a re-edit that drops it reds the gate.
    "netdata": {"DO_NOT_TRACK": "1"},
    # n8n: diagnostics/telemetry to n8n's PostHog — already shipped, pinned here.
    "n8n": {"N8N_DIAGNOSTICS_ENABLED": "false"},
    # dozzle: anonymous beacon POSTs to https://b.dozzle.dev/event by default; kill-switch verified
    # in v10.6.13 source (internal/support/cli/args.go). Gap found by the 2026-07-30 bump sweep —
    # the beacon predates the bump (rule #15 catch, not a v10.6.13 regression).
    "dozzle": {"DOZZLE_NO_ANALYTICS": "true"},
}

# Services whose telemetry kill-switch is a COMMAND-LINE FLAG, not an env var, so the env-only
# scan above is structurally blind to it (traefik's global.checkNewVersion → update.traefik.io;
# alloy's usage reporting → stats.grafana.org). Each maps service -> {required command-list item:
# reason}; check_egress asserts the exact flag is present in descriptor.command.
_REQUIRED_EGRESS_CMD: dict[str, dict[str, str]] = {
    "traefik": {
        "--global.checknewversion=false": "checkNewVersion defaults ON → GET update.traefik.io "
        "(running version + public IP) from the internet-facing edge.",
        "--global.sendanonymoususage=false": "explicit opt-out of anonymous usage stats.",
    },
    "alloy": {
        "--disable-reporting": "Grafana Alloy usage reporting POSTs to stats.grafana.org every 4h.",
    },
}

# UI-only opt-out services: their telemetry has NO env/CLI knob in the pinned version, so it can
# only be contained at the network layer (or unchecked in the UI on first login). Listing them
# keeps the gate honest — they are KNOWN to phone home, just not declaratively fixable here. A
# stale entry (the image grew an env knob → it appears in a descriptor's env) fails the gate.
_EGRESS_EXEMPT: dict[str, str] = {
    "portainer": "ui-only: Matomo anonymous-statistics is a General-settings checkbox; the legacy "
    "--no-analytics CLI flag is deprecated/unreliable under Docker in 2.x, no env var.",
    "homarr": "ui-only: the pre-v1 DISABLE_ANALYTICS env was removed in the v1 rewrite; v1.62 "
    "Umami analytics is opt-out via UI settings only, no env var.",
    "uptime-kuma": "ui-only: privacy-first, no telemetry of its own; the only outbound call is the "
    "version update check, disabled via Settings -> General toggle, no env var.",
}


def _service_env(descriptor: Any) -> dict[str, str]:
    env = getattr(descriptor, "env", None)
    return dict(env) if isinstance(env, dict) else {}


def _service_command(descriptor: Any) -> list[str]:
    cmd = getattr(descriptor, "command", None)
    return [str(x) for x in cmd] if isinstance(cmd, (list, tuple)) else []


def _issue(service: str, kind: str, message: str) -> dict[str, str]:
    return {"severity": "error", "kind": kind, "service": service, "message": message}


def check_egress(
    descriptors: dict[str, Any] | None = None,
    *,
    full_catalog: bool | None = None,
) -> list[dict[str, str]]:
    """Return a list of egress violations (empty == compliant).

    ``descriptors`` maps service-name -> object with an ``.env`` dict (defaults to the live
    catalog). ``full_catalog`` forces the stale-rule sweep (which is only meaningful against the
    real catalog); it defaults to True when ``descriptors`` is the live catalog, else False.
    """
    if descriptors is None:
        from agmind.services.renderer import load_descriptors

        descriptors = load_descriptors()
        if full_catalog is None:
            full_catalog = True
    if full_catalog is None:
        full_catalog = False

    issues: list[dict[str, str]] = []

    # Required-env services: every key present with the exact value.
    for name, required in sorted(_REQUIRED_EGRESS_ENV.items()):
        descriptor = descriptors.get(name)
        if descriptor is None:
            continue  # stale-rule sweep handles "left the catalog" below.
        env = _service_env(descriptor)
        for key, want in required.items():
            if key not in env:
                issues.append(
                    _issue(
                        name,
                        "egress_key_missing",
                        f"Service '{name}' must set '{key}={want}' for zero-egress but the key is "
                        f"absent. Add it to the descriptor env (see the # zero-egress: comment).",
                    )
                )
            elif env[key] != want:
                issues.append(
                    _issue(
                        name,
                        "egress_value_wrong",
                        f"Service '{name}' sets '{key}={env[key]!r}' but zero-egress requires "
                        f"'{key}={want!r}'.",
                    )
                )

    # Required-command-flag services: telemetry killed via a CLI flag, invisible to the env scan.
    for name, required_flags in sorted(_REQUIRED_EGRESS_CMD.items()):
        descriptor = descriptors.get(name)
        if descriptor is None:
            continue
        cmd = _service_command(descriptor)
        for flag, reason in required_flags.items():
            if flag not in cmd:
                issues.append(
                    _issue(
                        name,
                        "egress_flag_missing",
                        f"Service '{name}' must pass '{flag}' in its command for zero-egress but "
                        f"it is absent. {reason}",
                    )
                )

    # Stale exemption: a UI-only service that gained a required key (move it to required).
    for name in sorted(_EGRESS_EXEMPT):
        descriptor = descriptors.get(name)
        if descriptor is None:
            continue
        env = _service_env(descriptor)
        for required in _REQUIRED_EGRESS_ENV.values():
            present = sorted(key for key in required if key in env)
            if present:
                issues.append(
                    _issue(
                        name,
                        "egress_stale_exemption",
                        f"Service '{name}' is listed UI-only exempt but its env now carries "
                        f"telemetry-kill key(s) {present} — it is no longer UI-only. Move it from "
                        f"_EGRESS_EXEMPT to _REQUIRED_EGRESS_ENV (stale exemption).",
                    )
                )
                break

    # Stale rule: a required/exempt service that is no longer in the catalog.
    if full_catalog:
        catalog = set(descriptors)
        for name in sorted(
            set(_REQUIRED_EGRESS_ENV) | set(_REQUIRED_EGRESS_CMD) | set(_EGRESS_EXEMPT)
        ):
            if name not in catalog:
                issues.append(
                    _issue(
                        name,
                        "egress_stale_rule",
                        f"egress rule for '{name}' is not in the catalog — remove the stale rule.",
                    )
                )
    return issues


def _payload(issues: list[dict[str, str]]) -> dict[str, Any]:
    required_keys = sum(len(keys) for keys in _REQUIRED_EGRESS_ENV.values())
    required_flags = sum(len(flags) for flags in _REQUIRED_EGRESS_CMD.values())
    return {
        "ok": len(issues) == 0,
        "required_count": len(_REQUIRED_EGRESS_ENV),
        "required_key_count": required_keys,
        "required_cmd_count": len(_REQUIRED_EGRESS_CMD),
        "required_flag_count": required_flags,
        "exempt_count": len(_EGRESS_EXEMPT),
        "error_count": len(issues),
        "issues": issues,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    issues = check_egress()
    ok = len(issues) == 0

    if as_json:
        print(json.dumps(_payload(issues), indent=2, ensure_ascii=False))
        return 0 if ok else 1

    if ok:
        print(
            f"A8 OK: zero-egress satisfied for {len(_REQUIRED_EGRESS_ENV)} env-gated + "
            f"{len(_REQUIRED_EGRESS_CMD)} command-gated services "
            f"({len(_EGRESS_EXEMPT)} UI-only exempt)."
        )
        return 0
    for issue in issues:
        print(f"ERROR {issue['service']}: {issue['message']}")
    print(f"\nA8 FAILED: {len(issues)} zero-egress violation(s).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
