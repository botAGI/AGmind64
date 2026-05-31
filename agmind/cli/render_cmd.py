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
from datetime import UTC, datetime
from pathlib import Path

import typer

from agmind.core.files import write_text_atomic
from agmind.services.deployment_topology import build_deployment_topology_report
from agmind.services.renderer import (
    DEFAULT_SERVICES_DIR,
    load_descriptors,
    render_to_string,
    select_services,
    unknown_profiles,
)

# Placeholder digest written for backends not supplied via --backend-digests-dir.
_BACKEND_PLACEHOLDER_DIGEST = "0" * 64

# The four self-built backend image names (without registry prefix).
_BACKEND_NAMES = ("base", "cpu", "vulkan", "rocm")

# Registry namespace for self-built images.
_GHCR_NS = "ghcr.io/botagi/agmind"


def cmd_render_catalog(
    version: str,
    output: Path | None = None,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    backend_digests_dir: Path | None = None,
    source_ref: str = "",
) -> int:
    """Render a catalog-<ver>.json from service descriptors.

    Each service entry carries::

        {image, digest: "sha256:<64hex>", ref: fq_image(), tier, profiles}

    The ``backends`` block is populated from ``<backend>.digest`` files inside
    *backend_digests_dir* when provided; otherwise placeholder refs are emitted
    so the verb is testable without a real GHCR push.

    Returns:
        0 success, 1 error.
    """
    try:
        descriptors = load_descriptors(services_dir)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Build the services map.  Services without a digest are represented with
    # ref == image (no @sha256: suffix) so the catalog is still valid JSON; the
    # digest field will be absent from the entry (catalog schema requires it for
    # services that are pinned — callers should treat missing digest as a gap).
    services: dict[str, object] = {}
    for name, desc in descriptors.items():
        entry: dict[str, object] = {
            "image": desc.image,
            "tier": desc.tier,
            "profiles": list(desc.profiles),
        }
        if desc.digest:
            hex_digest = desc.digest
            sha_digest = hex_digest if hex_digest.startswith("sha256:") else f"sha256:{hex_digest}"
            entry["digest"] = sha_digest
            entry["ref"] = desc.fq_image()
        else:
            # No digest pinned — omit the digest key entirely so the catalog
            # schema's `required: ["digest"]` rejects the entry as unpinned.
            # Never emit sha256:000…0 which would masquerade as a real pin
            # while ref still points at a mutable tag (WR-03 fail-open path).
            entry["ref"] = desc.image
        services[name] = entry

    # Build the backends block.
    backends: dict[str, object] = {}
    for backend in _BACKEND_NAMES:
        image_tag = f"{_GHCR_NS}-{backend}:{version}"
        if backend_digests_dir is not None:
            digest_file = backend_digests_dir / f"{backend}.digest"
            if digest_file.exists():
                raw = digest_file.read_text(encoding="utf-8").strip()
                sha_digest = raw if raw.startswith("sha256:") else f"sha256:{raw}"
                backends[backend] = {
                    "image": image_tag,
                    "digest": sha_digest,
                    "ref": f"{_GHCR_NS}-{backend}@{sha_digest}",
                }
                continue
        # Placeholder (testable without a real push).
        placeholder = f"sha256:{_BACKEND_PLACEHOLDER_DIGEST}"
        backends[backend] = {
            "image": image_tag,
            "digest": placeholder,
            "ref": f"{_GHCR_NS}-{backend}@{placeholder}",
        }

    catalog: dict[str, object] = {
        "schema_version": 1,
        "agmind_version": version,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "services": services,
        "backends": backends,
    }
    if source_ref:
        catalog["source_ref"] = source_ref

    content = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"

    if output is None:
        sys.stdout.write(content)
        return 0

    write_text_atomic(output, content)
    print(f"✓ wrote {output} ({len(services)} services)")
    return 0


def cmd_render_compose(
    profiles: list[str],
    services: list[str] | None = None,
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
            services=services,
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

    write_text_atomic(output, rendered)
    print(f"✓ wrote {output} ({len(rendered)} bytes)")
    return 0


def cmd_render_kubernetes(
    profiles: list[str],
    services: list[str] | None = None,
    output: Path | None = None,
    namespace: str = "agmind",
    strict: bool = False,
    include_namespace: bool = True,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    target_id: str | None = None,
) -> int:
    """Render Kubernetes YAML from service descriptors.

    Returns:
        0 success, 1 error.
    """
    try:
        exclude_services: list[str] | None = None
        if target_id is not None:
            from agmind.deploy.targets import load_deploy_targets

            targets = load_deploy_targets()
            target = targets.get(target_id)
            if target is None:
                raise ValueError(f"unknown deployment target: {target_id}")
            if target.runtime.kind != "kubernetes":
                raise ValueError(f"deployment target is not Kubernetes-backed: {target_id}")
            if services is not None:
                raise ValueError("--target cannot be combined with explicit --service selection")
            profiles = list(target.runtime.profiles)
            exclude_services = list(target.runtime.excluded_services)

        from agmind.services.kubernetes_renderer import render_to_string as render_k8s_to_string

        rendered = render_k8s_to_string(
            profiles=profiles,
            services=services,
            exclude_services=exclude_services,
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

    write_text_atomic(output, rendered)
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
        if services is not None:
            missing = sorted(set(services).difference(descriptors))
            if missing:
                print(
                    "ERROR: unknown selected services for topology: " + ", ".join(missing),
                    file=sys.stderr,
                )
                return 1
        else:
            missing_profiles = unknown_profiles(descriptors, profiles)
            if missing_profiles:
                print(
                    "ERROR: unknown selected profiles for topology: " + ", ".join(missing_profiles),
                    file=sys.stderr,
                )
                return 1
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


def register(app: typer.Typer) -> None:
    """Attach the ``render`` command group to ``app``."""

    # ---- render subcommand group (Phase H'.C) ----
    render_app = typer.Typer(
        name="render",
        help="Render compose / configs from templates/services/*.yaml descriptors",
        no_args_is_help=True,
    )
    app.add_typer(render_app)

    @render_app.command("catalog")
    def render_catalog(
        version: str = typer.Option(
            ...,
            "--version",
            "-v",
            help="Release version string (e.g. 0.2.0), written into the catalog",
        ),
        output: Path | None = typer.Option(
            None, "--output", "-o", help="Output file (default: stdout)"
        ),
        backend_digests_dir: Path | None = typer.Option(
            None,
            "--backend-digests-dir",
            help=(
                "Directory with <backend>.digest files written by the release matrix jobs. "
                "When absent, placeholder digests are emitted for testability."
            ),
        ),
        source_ref: str = typer.Option(
            "",
            "--source-ref",
            help="git tag/SHA the catalog was built from (informational, optional)",
        ),
    ) -> None:
        """Render catalog-<ver>.json from ServiceDescriptor catalog.

        Emits a schema-valid AGmind release catalog covering all service
        descriptors (each with image, sha256 digest, fq_image ref, tier, and
        profiles) plus the four self-built backend image entries.
        """
        rc = cmd_render_catalog(
            version=version,
            output=output,
            backend_digests_dir=backend_digests_dir,
            source_ref=source_ref,
        )
        raise typer.Exit(code=rc)

    @render_app.command("compose")
    def render_compose(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
        ),
        output: Path | None = typer.Option(
            None, "--output", "-o", help="Output file (default: stdout)"
        ),
        no_traefik: bool = typer.Option(
            False, "--no-traefik", help="Skip Traefik labels generation"
        ),
        diff: Path | None = typer.Option(
            None, "--diff", help="Diff against existing compose file (no write)"
        ),
        domain: str | None = typer.Option(
            None,
            "--domain",
            envvar="AGMIND_DOMAIN",
            help="Override agmind.dev placeholder (e.g. yourdomain.com)",
        ),
    ) -> None:
        """Render docker-compose.yml from ServiceDescriptor catalog."""
        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_render_compose(
            profiles=profiles,
            services=service,
            output=output,
            traefik=not no_traefik,
            diff=diff,
            domain=domain,
        )
        raise typer.Exit(code=rc)

    @render_app.command("kubernetes")
    def render_kubernetes(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
        ),
        output: Path | None = typer.Option(
            None, "--output", "-o", help="Output file (default: stdout)"
        ),
        namespace: str = typer.Option(
            "agmind",
            "--namespace",
            "-n",
            help="Kubernetes namespace for rendered objects",
        ),
        strict: bool = typer.Option(
            False,
            "--strict",
            help="Fail if selected descriptors contain Docker-only fields",
        ),
        target: str | None = typer.Option(
            None,
            "--target",
            help="Deployment target id; uses target profiles and exclusions",
        ),
        no_namespace: bool = typer.Option(
            False,
            "--no-namespace",
            help="Do not emit a Namespace object",
        ),
    ) -> None:
        """Render Kubernetes manifests from ServiceDescriptor catalog."""
        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_render_kubernetes(
            profiles=profiles,
            services=service,
            output=output,
            namespace=namespace,
            strict=strict,
            include_namespace=not no_namespace,
            target_id=target,
        )
        raise typer.Exit(code=rc)

    @render_app.command("topology")
    def render_topology(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        fail_on_warning: bool = typer.Option(
            False,
            "--fail-on-warning",
            help="Exit 2 when topology warnings are present",
        ),
    ) -> None:
        """Render selected-service topology warnings and RAG storage plan."""
        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_render_topology(
            profiles=profiles,
            services=service,
            as_json=as_json,
            fail_on_warning=fail_on_warning,
        )
        raise typer.Exit(code=rc)
