#!/usr/bin/env python3
"""Gate A6: a service healthcheck must not invoke a tool absent from its image.

The qdrant descriptor once shipped ``healthcheck.test: [CMD, curl, -f, ...]`` on
``qdrant/qdrant`` — an image that ships NO curl/wget/nc (only bash/sh). Docker reports
'executable file not found' on every probe → the container stays unhealthy forever → the
deploy false-rolls-back. No static gate caught it; it surfaced only on a live deploy (and the
same class hit ragflow/dify-web/portainer via healthcheck-path/scheme mismatches). This gate
closes the tool half at authoring time.

Offline (default, CI-deterministic): consult the curated _KNOWN_MISSING_TOOLS /
_KNOWN_PRESENT_TOOLS maps. Flag ONLY when an image is recorded as lacking the exact tool;
unknown image/tool pairs → info (never block on unproven data).
Probe (--probe): run ``docker run --rm --entrypoint sh <image> -c 'command -v <tool>'`` to
refresh the maps (needs docker; not the default CI lane).

Exit codes: 0 — no healthcheck invokes a proven-absent tool; 1 — at least one does.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# External networking/probe tools that minimal images routinely OMIT. Only these argv[0]
# values are policed; bash/sh/node/redis-cli/pg_isready/mysqladmin/mc/etcdctl/traefik/
# <absolute paths> are the image's own bundled tooling and are never flagged.
_EXTERNAL_TOOLS: frozenset[str] = frozenset(
    {
        "curl",
        "wget",
        "nc",
        "ncat",
        "netcat",
        "socat",
        "nslookup",
        "dig",
        "host",
        "ping",
        "telnet",
        "openssl",
    }
)

# Curated offline DENY-map: per pinned image (repo:tag, matches descriptor.image), the
# _EXTERNAL_TOOLS proven ABSENT. qdrant ships bash/sh only — this row catches the original
# qdrant-curl regression class. redis/postgres lack these too but use bundled
# redis-cli/pg_isready, so their healthchecks never reach this gate (documented for safety).
_KNOWN_MISSING_TOOLS: dict[str, frozenset[str]] = {
    "qdrant/qdrant:v1.18.0": frozenset({"curl", "wget", "nc", "ncat", "netcat"}),
    "redis:8.4.3-alpine": frozenset({"curl", "wget", "nc"}),
    "postgres:17.10-alpine3.22": frozenset({"curl", "wget", "nc"}),
}

# Curated offline ALLOW-map: per pinned image, _EXTERNAL_TOOLS proven PRESENT. A hit
# short-circuits the "unverified info" path → verified OK. llama.cpp ships curl (the
# llama-llm/embed/rerank healthchecks rely on it and run healthy live).
_KNOWN_PRESENT_TOOLS: dict[str, frozenset[str]] = {
    "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049": frozenset({"curl"}),
    "mysql:8.0.46-oraclelinux9": frozenset({"curl"}),
    # curl verified present by live `docker exec ... command -v curl` (2026-06-05).
    "elasticsearch:8.19.16": frozenset({"curl"}),
    "ghcr.io/moghtech/komodo-core:2.1.0": frozenset({"curl"}),
}

_PIPELINE_SEPARATORS: frozenset[str] = frozenset({"&&", "||", ";", "|", "&"})


def _command_position_tokens(shell_str: str) -> list[str]:
    """Return only command-position tokens (argv[0] of each pipeline segment).

    Avoids false-flagging a subcommand named like a tool — e.g. ``mysqladmin ping`` invokes
    mysqladmin with subcommand ``ping``; ``ping`` is not a binary on PATH there. Leading
    ``FOO=bar`` env assignments are skipped.
    """
    try:
        tokens = shlex.split(shell_str)
    except ValueError:
        tokens = shell_str.split()

    commands: list[str] = []
    expect_command = True
    for tok in tokens:
        if tok in _PIPELINE_SEPARATORS:
            expect_command = True
            continue
        if expect_command:
            if "=" in tok and tok.split("=", 1)[0].isidentifier():
                continue
            commands.append(tok)
            expect_command = False
    return commands


def _extract_external_tools(test: Sequence[str]) -> list[str]:
    """Return external tool name(s) a healthcheck ``test`` shells out to.

    ``["CMD", prog, ...]`` → tool is prog (test[1]).
    ``["CMD-SHELL", "<shell>"]`` → only command-position tokens are policed.
    Absolute/relative paths (contain '/') are never external tools.
    """
    items = [str(i) for i in test]
    if not items:
        return []
    mode = items[0]
    found: list[str] = []

    def _consider(token: str) -> None:
        name = token.strip()
        if not name or "/" in name:
            return
        if name in _EXTERNAL_TOOLS and name not in found:
            found.append(name)

    if mode == "CMD":
        if len(items) >= 2:
            _consider(items[1])
    elif mode == "CMD-SHELL":
        for shell_str in items[1:]:
            for tok in _command_position_tokens(shell_str):
                _consider(tok)
    else:
        for tok in items:
            _consider(tok)
    return found


def _issue(service: str, image: str, tool: str, *, probed: bool) -> dict[str, str]:
    how = "live docker probe" if probed else "curated _KNOWN_MISSING_TOOLS"
    return {
        "severity": "error",
        "kind": "healthcheck_tool_absent",
        "service": service,
        "image": image,
        "tool": tool,
        "message": (
            f"Service '{service}' healthcheck invokes '{tool}' but image '{image}' does NOT "
            f"ship it ({how}) → docker reports 'executable file not found', the container "
            f"stays unhealthy forever and the deploy false-rolls-back. Use a tool the image "
            f"has (bash /dev/tcp, the app's own CLI) or a different probe."
        ),
    }


def _probe_tool_present(image_ref: str, tool: str) -> bool:
    """Live mode: True iff ``tool`` resolves on PATH inside ``image_ref``."""
    proc = subprocess.run(  # noqa: S603
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            image_ref,
            "-c",
            f"command -v {shlex.quote(tool)}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def check_healthcheck_tools(
    *,
    services_dir: Path | None = None,
    probe: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    """Return ``(errors, unknowns, external_tool_healthcheck_count)``."""
    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors(services_dir) if services_dir is not None else load_descriptors()
    errors: list[dict[str, str]] = []
    unknowns: list[dict[str, str]] = []
    count = 0

    for name, desc in sorted(descriptors.items()):
        if desc.health is None:
            continue
        tools = _extract_external_tools(desc.health.test)
        if not tools:
            continue
        count += 1
        image_tag = desc.image
        known_missing = _KNOWN_MISSING_TOOLS.get(image_tag, frozenset())
        known_present = _KNOWN_PRESENT_TOOLS.get(image_tag, frozenset())
        for tool in tools:
            if probe:
                if not _probe_tool_present(desc.fq_image(), tool):
                    errors.append(_issue(name, image_tag, tool, probed=True))
            elif tool in known_missing:
                errors.append(_issue(name, image_tag, tool, probed=False))
            elif tool in known_present:
                continue
            else:
                unknowns.append(
                    {
                        "severity": "info",
                        "kind": "healthcheck_tool_unverified",
                        "service": name,
                        "image": image_tag,
                        "tool": tool,
                        "message": (
                            f"Service '{name}' healthcheck invokes external tool '{tool}' but "
                            f"image '{image_tag}' is in neither curated map. Run "
                            f"healthcheck_tool_check.py --probe, then record it in "
                            f"_KNOWN_PRESENT_TOOLS / _KNOWN_MISSING_TOOLS."
                        ),
                    }
                )
    return errors, unknowns, count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A6: healthcheck tool-in-image gate")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Live-probe each image with `docker run` instead of the curated maps.",
    )
    args = parser.parse_args(argv)

    errors, unknowns, count = check_healthcheck_tools(probe=args.probe)
    for u in unknowns:
        print(f"INFO  {u['service']}: {u['message']}")
    for e in errors:
        print(f"ERROR {e['service']}: {e['message']}")
    if errors:
        print(f"\nA6 FAILED: {len(errors)} healthcheck(s) invoke a tool absent from the image.")
        return 1
    print(
        f"A6 OK: {count} external-tool healthcheck(s) verified present "
        f"({len(unknowns)} unverified/info)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
