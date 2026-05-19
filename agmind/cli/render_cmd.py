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
import sys
from pathlib import Path

from agmind.services.renderer import DEFAULT_SERVICES_DIR, render_to_string


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
