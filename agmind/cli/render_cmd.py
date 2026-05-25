"""Phase H'.C: `agmind render compose` CLI команда.

Использует agmind.services.renderer для генерации docker-compose.yml из
ServiceDescriptor catalog. Заменяет Ansible Jinja2 шаблон.

Примеры:
    agmind render compose --profile core
    agmind render compose --profile core,rag --output /opt/agmind/docker-compose.yml
    agmind render compose --profile core --no-traefik    # без Traefik labels
    agmind render compose --diff /opt/agmind/docker-compose.yml  # diff с текущим
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

from agmind.services.deployment_topology import build_deployment_topology_report
from agmind.services.renderer import (
    DEFAULT_SERVICES_DIR,
    load_descriptors,
    render_to_string,
    select_services,
)


def cmd_render_compose(
    profiles: list[str],
    output: Path | None = None,
    traefik: bool = True,
    diff: Path | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    domain: str | None = None,
) -> int:
    """Render compose YAML from service descriptors.

    Returns:
        0 success, 1 error, 2 diff has changes (если --diff)
    """
    try:
        rendered = render_to_string(
            profiles=profiles,
            services_dir=services_dir,
            traefik_enabled=traefik,
            domain=domain,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if diff is not None:
        if not diff.exists():
            print(f"ERROR: --diff target {diff} does not exist", file=sys.stderr)
            return 1
        current = diff.read_text(encoding="utf-8").splitlines(keepends=True)
        new = rendered.splitlines(keepends=True)
        diff_text = "".join(
            difflib.unified_diff(
                current,
                new,
                fromfile=str(diff),
                tofile="(rendered)",
                lineterm="",
            )
        )
        if not diff_text:
            print(f"✓ {diff} matches rendered output (no changes)")
            return 0
        sys.stdout.write(diff_text)
        return 2

    if output is None:
        # stdout
        sys.stdout.write(rendered)
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"✓ wrote {output} ({len(rendered)} bytes)")
    return 0


def cmd_render_kubernetes(
    profiles: list[str],
    output: Path | None = None,
    namespace: str = "agmind",
    strict: bool = False,
    include_namespace: bool = True,
    services_dir: Path = DEFAULT_SERVICES_DIR,
) -> int:
    """Render Kubernetes YAML from service descriptors.

    Returns:
        0 success, 1 error.
    """
    try:
        from agmind.services.kubernetes_renderer import render_to_string as render_k8s_to_string

        rendered = render_k8s_to_string(
            profiles=profiles,
            services_dir=services_dir,
            namespace=namespace,
            strict=strict,
            include_namespace=include_namespace,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if output is None:
        sys.stdout.write(rendered)
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(f"✓ wrote {output} ({len(rendered)} bytes)")
    return 0


def cmd_render_topology(
    profiles: list[str],
    services: list[str] | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    as_json: bool = False,
    fail_on_warning: bool = False,
) -> int:
    """Render an operator topology report for profiles or explicit services."""
    try:
        descriptors = load_descriptors(services_dir)
        selected = select_services(
            descriptors,
            profiles=profiles,
            services=services if services else None,
        )
        if not selected:
            print(
                f"ERROR: No services match: profiles={profiles}, services={services}",
                file=sys.stderr,
            )
            return 1
        report = build_deployment_topology_report(
            selected,
            all_descriptors=descriptors,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if as_json:
        sys.stdout.write(json.dumps(report.to_payload(), indent=2, ensure_ascii=False) + "\n")
        return 2 if fail_on_warning and report.has_warnings else 0

    lines = report.block_lines()
    if lines:
        sys.stdout.write("\n".join(lines) + "\n")
    else:
        sys.stdout.write("TOPOLOGY OK\n")
    return 2 if fail_on_warning and report.has_warnings else 0
