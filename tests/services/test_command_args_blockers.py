"""Crash-loop blockers fixed in the service catalog (descriptor-only).

- proxmox-exporter: v3.x argparse rejects the old positional args
  ("unrecognized arguments: /etc/pve.yml 9221 0.0.0.0") — must use named flags.
- minio: no `command:` → image prints USAGE and exits — needs `server /data
  --console-address :9001`.
- watchtower: containrrr/watchtower:1.7.1's API client (1.25) is too old for
  Docker Engine 29 (min 1.44) — force DOCKER_API_VERSION=1.44.
"""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_proxmox_exporter_uses_named_cli_flags() -> None:
    d = load_descriptors()["proxmox-exporter"]
    assert list(d.command) == [
        "--config.file=/etc/pve.yml",
        "--web.listen-address=0.0.0.0:9221",
    ]


def test_minio_has_server_subcommand() -> None:
    d = load_descriptors()["minio"]
    assert list(d.command) == ["server", "/data", "--console-address", ":9001"]


def test_command_value_flags_not_swallowed() -> None:
    """A1: catalog-wide gate — known value-taking long-flags must not be immediately
    followed by another flag token.

    The b9049 regression class: --flash-attn (takes on|off|auto) had its next token
    --cache-type-k consumed as the value, crash-looping llama-llm. This test
    generalises: for each descriptor command, for each known value-taking flag,
    assert the next token is NOT a '-'-prefixed flag.

    Mutation-verified: reverting llama-llm.yaml to a bare --flash-attn followed by
    --cache-type-k causes this test to RED (next token starts with '-').
    """
    # Known value-taking flags: flag token (exact string) → set of accepted values
    # Use None to mean "any non-flag non-empty token is accepted"
    VALUE_TAKING_FLAGS: dict[str, set[str] | None] = {
        "--flash-attn": {"on", "off", "auto", "true", "false"},
    }

    descriptors = load_descriptors()
    violations: list[str] = []

    for name, d in descriptors.items():
        if not d.command:
            continue
        cmd = [str(tok) for tok in d.command]
        for i, tok in enumerate(cmd):
            if tok not in VALUE_TAKING_FLAGS:
                continue
            accepted = VALUE_TAKING_FLAGS[tok]
            nxt = cmd[i + 1] if i + 1 < len(cmd) else None
            if nxt is None:
                violations.append(f"{name}: {tok!r} is the last token — missing required value")
            elif nxt.startswith("-"):
                violations.append(
                    f"{name}: {tok!r} followed by flag token {nxt!r} — "
                    f"value is being swallowed (b9049 regression class)"
                )
            elif accepted is not None and nxt.lower() not in accepted:
                violations.append(f"{name}: {tok!r} value {nxt!r} not in accepted set {accepted}")

    assert not violations, (
        "A1 command-vs-CLI violation (value-taking flag has no valid value):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_etcd_flags_use_single_hyphen_prefix() -> None:
    """etcd uses single-hyphen flags (e.g. -advertise-client-urls) for long options.
    This is the documented etcd convention (not GNU --double-hyphen). Guard that
    the etcd command flags remain in their single-hyphen form so the flag prefix
    invariant is explicit and protected against accidental normalisation.

    Only applies to the known advertise/listen flag family; --data-dir uses double
    hyphens in etcd v3.5 and is also fine — we document only the single-hyphen flags.
    """
    d = load_descriptors()["etcd"]
    cmd = [str(tok) for tok in (d.command or [])]
    # The flags that must start with single hyphen in our descriptor
    single_hyphen_flags = [
        tok for tok in cmd if tok.startswith("-advertise-") or tok.startswith("-listen-")
    ]
    assert single_hyphen_flags, (
        "etcd command should contain -advertise-* or -listen-* single-hyphen flags; "
        f"command is: {cmd}"
    )
    for flag in single_hyphen_flags:
        assert not flag.startswith("--"), (
            f"etcd flag {flag!r} uses double-hyphen prefix; etcd single-hyphen flags "
            "must not be rewritten to --double-hyphen form"
        )


def test_watchtower_forces_compatible_docker_api_version() -> None:
    d = load_descriptors()["watchtower"]
    assert d.env.get("DOCKER_API_VERSION") == "1.44"
