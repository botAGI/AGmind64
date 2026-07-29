"""Phase M3.R: `agmind upgrade` — bump pinned image + redeploy с rollback.

Three modes:
  agmind upgrade --check                — run version_check.py scanner
  agmind upgrade --component X --version Y — bump pin в template
  agmind upgrade --apply                — redeploy after bump (использует Phase L.B)
  agmind upgrade --rollback             — revert last bump + redeploy

Respects templates/version_holds.yaml (refuse без --force).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

import typer

from agmind.core.files import write_text_atomic
from agmind.core.logging import logger
from agmind.core.paths import data_root

log = logger(__name__)

REPO_ROOT = data_root()
SERVICES_DIR = REPO_ROOT / "templates" / "services"
COMPONENTS_DIR = REPO_ROOT / "templates" / "components"
HOLDS_FILE = REPO_ROOT / "templates" / "version_holds.yaml"
UPGRADE_STATE_DIR = Path.home() / ".local" / "share" / "agmind" / "upgrades"

_IMAGE_LINE_RE = re.compile(
    r"^image:\s*(?P<image>[^\s:]+):(?P<tag>[^\s@]+)(?:@sha256:(?P<digest>[a-f0-9]+))?\s*$"
)


@dataclass(frozen=True)
class UpgradePlanItem:
    service: str
    yaml_path: str
    image: str
    old_tag: str
    new_tag: str
    old_digest: str | None
    new_digest: str | None = None


@dataclass(frozen=True)
class UpgradePlan:
    component: str
    items: tuple[UpgradePlanItem, ...]
    policy: str
    is_component: bool


def _load_holds() -> dict[str, dict[str, str]]:
    # A version hold is a deliberate safety lever (freeze an image). Distinguish ABSENT
    # (no holds → {}) from present-but-UNPARSEABLE: silently treating a corrupt holds file as
    # empty let `agmind upgrade` sail past every hold and bump frozen images (review MEDIUM
    # upgrade-corrupt-holds-dropped). On a parse error or a non-mapping payload, abort loudly.
    if not HOLDS_FILE.exists():
        return {}
    import yaml

    try:
        data = yaml.safe_load(HOLDS_FILE.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(
            f"agmind upgrade: version holds file {HOLDS_FILE} is unparseable ({exc}); "
            "refusing to proceed — a corrupt hold could silently un-freeze pinned images.",
            file=sys.stderr,
        )
        raise typer.Exit(2) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(
            f"agmind upgrade: version holds file {HOLDS_FILE} must be a mapping, got "
            f"{type(data).__name__}; refusing to proceed.",
            file=sys.stderr,
        )
        raise typer.Exit(2)
    return data


def _find_descriptor_for_service(service_name: str) -> Path | None:
    """Find templates/services/<service>.yaml для image pin."""
    candidate = SERVICES_DIR / f"{service_name}.yaml"
    if candidate.exists():
        return candidate
    return None


_DIGEST_LINE_RE = re.compile(r"^digest:\s*(?:sha256:)?(?P<digest>[a-f0-9]+)\s*$")


def _read_current_pin(yaml_path: Path) -> tuple[str, str, str | None] | None:
    """Return (image, tag, digest_or_None) from descriptor. None if no `image:` line.

    The returned digest is ONLY the inline `@sha256:` one (captured off the
    `image:` line). The catalog norm is the SEPARATE `digest:` line, which this
    does not see — read it with `_read_separate_digest`. Both forms render to
    `name:tag@sha256:<digest>` (ServiceDescriptor.image_ref), so a tag-only bump
    that drops either digest source pins the new tag to the OLD digest.
    """
    text = yaml_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = _IMAGE_LINE_RE.match(line)
        if m:
            return m.group("image"), m.group("tag"), m.group("digest")
    return None


def _read_separate_digest(yaml_path: Path) -> str | None:
    """Return the descriptor's separate `digest:` line value, or None if absent.

    This is the catalog-norm digest source (43/43 catalog descriptors; 0 inline).
    A tag bump that leaves this line stale renders the new tag against the OLD
    digest — docker then resolves BY DIGEST and silently deploys the OLD image.
    """
    text = yaml_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = _DIGEST_LINE_RE.match(line)
        if m:
            return m.group("digest")
    return None


def _bump_pin_in_yaml(
    yaml_path: Path, new_tag: str, new_digest: str | None = None
) -> tuple[str, str]:
    """Replace image tag (and digest) в descriptor. Returns (old_tag, new_tag).

    Digest single-source invariant (`_check_single_digest_source`): a descriptor
    must carry its sha256 in EITHER the inline `image: name:tag@sha256:<d>` form
    OR a separate `digest:` line, never both. The catalog uses the separate form
    (34/40 descriptors; 0 inline), so a digest bump always keeps `image:` as a
    bare `name:tag` and writes the sha256 ONLY on the separate `digest:` line —
    either by rewriting an existing `digest:` line or, if none exists, by adding
    one right after `image:`. Writing both inline and separate forms would corrupt
    the descriptor into an un-loadable "duplicate digest" state (F.1).
    """
    text = yaml_path.read_text(encoding="utf-8")
    new_lines: list[str] = []
    digest_replaced = False
    old_tag = ""
    image = ""
    for line in text.splitlines():
        m = _IMAGE_LINE_RE.match(line)
        if m:
            image = m.group("image")
            old_tag = m.group("tag")
            # Always emit a bare `image: name:tag`; the digest is single-sourced
            # on the separate `digest:` line below (never inline `@sha256:`).
            new_lines.append(f"image: {image}:{new_tag}")
            continue
        if line.startswith("digest:") and new_digest is not None:
            new_lines.append(f"digest: {new_digest}")
            digest_replaced = True
            continue
        new_lines.append(line)
    if new_digest and not digest_replaced:
        # Add `digest:` line right after `image:` если её ещё не было
        out: list[str] = []
        for line in new_lines:
            out.append(line)
            if line.startswith("image:"):
                out.append(f"digest: {new_digest}")
        new_lines = out
    write_text_atomic(yaml_path, "\n".join(new_lines) + "\n")
    return old_tag, new_tag


def _save_upgrade_state(
    service: str,
    yaml_path: Path,
    old_tag: str,
    new_tag: str,
    old_digest: str | None,
) -> Path:
    """Persist info для rollback. Returns saved state path."""
    UPGRADE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    state_file = UPGRADE_STATE_DIR / f"{ts}_{service}.json"
    import json

    write_text_atomic(
        state_file,
        json.dumps(
            {
                "service": service,
                "yaml_path": str(yaml_path),
                "old_tag": old_tag,
                "new_tag": new_tag,
                "old_digest": old_digest,
                "timestamp": ts,
            },
            indent=2,
        ),
    )
    return state_file


def _save_upgrade_plan_state(plan: UpgradePlan) -> Path:
    """Persist grouped upgrade plan for rollback."""
    UPGRADE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    import json
    from datetime import datetime

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    state_file = UPGRADE_STATE_DIR / f"{ts}_{plan.component}.json"
    payload = {
        "component": plan.component,
        "policy": plan.policy,
        "timestamp": ts,
        "items": [asdict(item) for item in plan.items],
    }
    write_text_atomic(state_file, json.dumps(payload, indent=2))
    return state_file


def _latest_upgrade_state() -> dict[str, Any] | None:
    """Read most recent upgrade state file."""
    if not UPGRADE_STATE_DIR.exists():
        return None
    files = sorted(UPGRADE_STATE_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    import json

    try:
        payload = json.loads(files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


# ---- main CLI entry points ----


def cmd_check() -> int:
    """Run scripts/checks/version_check.py."""
    script = REPO_ROOT / "scripts" / "checks" / "version_check.py"
    if not script.exists():
        print(f"ERROR: {script} not found", file=sys.stderr)
        return 1
    rc = subprocess.run(
        [sys.executable, str(script)],
        check=False,
    ).returncode
    return rc


def build_component_upgrade_plan(
    component: str,
    version: str,
    digest: str | None = None,
) -> UpgradePlan:
    """Build an update plan from a component id or raw service name."""
    from agmind.components.registry import load_component_contracts

    contracts = load_component_contracts(COMPONENTS_DIR)
    policy: str
    reference_tag: str | None = None
    if component in contracts:
        contract = contracts[component]
        service_names = contract.runtime.service_descriptors
        policy = contract.core.update_policy
        is_component = True
        # G.3 same-scheme guard: the reference family that `version` replaces.
        # Prefer the contract's declared pin (current_pin first, then
        # recommended_version); fall back to the modal member tag only if the
        # contract carries neither (resolved below once member tags are read).
        reference_tag = contract.core.current_pin or contract.core.recommended_version
    else:
        service_names = (component,)
        policy = "service"
        is_component = False

    # Read each member's current pin up front so a missing-descriptor or
    # missing-image error still aborts the whole plan (unchanged behavior).
    members: list[tuple[str, str, str, str, str | None]] = []
    for service_name in service_names:
        yaml_path = _find_descriptor_for_service(service_name)
        if yaml_path is None:
            raise ValueError(f"no descriptor for service {service_name!r}")
        current = _read_current_pin(yaml_path)
        if current is None:
            raise ValueError(f"no image line in {yaml_path}")
        image, old_tag, old_digest = current
        if old_digest is None:
            # D-01 (P0.2): _read_current_pin only sees an inline `@sha256:`
            # digest. 34/40 catalog descriptors carry it on a SEPARATE
            # `digest:` line instead, invisible here. Without this fallback,
            # UpgradePlanItem.old_digest (and thus the persisted upgrade-state)
            # would carry None for those descriptors, and a later rollback
            # would restore old_tag while leaving the NEW digest in place —
            # docker resolves by digest, so it silently deploys the NEW image
            # under the restored OLD tag.
            old_digest = _read_separate_digest(yaml_path)
        members.append((service_name, str(yaml_path), image, old_tag, old_digest))

    # Modal fallback: when a component contract declares no pin, treat the
    # most common member tag as the reference family.
    if is_component and not reference_tag:
        tag_counts: dict[str, int] = {}
        for _name, _path, _img, old_tag, _dig in members:
            tag_counts[old_tag] = tag_counts.get(old_tag, 0) + 1
        if tag_counts:
            reference_tag = max(tag_counts, key=lambda tag: (tag_counts[tag], tag))

    items: list[UpgradePlanItem] = []
    for service_name, yaml_path_str, image, old_tag, old_digest in members:
        # Component upgrades bump ONLY members that share the component's
        # reference version family; divergent members are marked unchanged
        # (new_tag == old_tag) so cmd_component's changed_items filter drops
        # them, and a per-member WARNING is surfaced. Raw single-service
        # upgrades (is_component is False) always bump.
        if is_component and reference_tag is not None and old_tag != reference_tag:
            print(
                f"WARNING: skipping {service_name} ({old_tag}) — divergent version "
                f"scheme (component reference {reference_tag}); not bumping to {version}",
                file=sys.stderr,
            )
            new_tag = old_tag
            member_digest: str | None = old_digest
        else:
            new_tag = version
            member_digest = digest
            # Release-blocker (deep audit: 34/40 separate-digest descriptors
            # corrupted): a tag bump that keeps the OLD digest renders the new
            # tag as `name:NEWTAG@sha256:OLDDIGEST` (ServiceDescriptor.image_ref),
            # so docker resolves BY DIGEST and silently deploys the OLD image.
            # When the member is actually changing tag AND carries a digest pin
            # (separate `digest:` line — the catalog norm — or an inline
            # `@sha256:`) but no replacement digest was supplied, REFUSE rather
            # than mutate. The operator must fetch the matching digest first.
            if new_tag != old_tag and digest is None:
                pinned_digest = old_digest or _read_separate_digest(Path(yaml_path_str))
                if pinned_digest is not None:
                    raise ValueError(
                        f"refusing to bump {service_name} {old_tag} -> {version} without a "
                        f"matching --digest: {image} is digest-pinned, so a tag-only bump would "
                        f"keep the OLD digest and silently deploy the OLD image under the new "
                        f"tag. Get the new digest:\n"
                        f"  docker buildx imagetools inspect {image}:{version}\n"
                        f"then re-run with --digest <sha256-hex> (no 'sha256:' prefix)."
                    )
        items.append(
            UpgradePlanItem(
                service=service_name,
                yaml_path=yaml_path_str,
                image=image,
                old_tag=old_tag,
                new_tag=new_tag,
                old_digest=old_digest,
                new_digest=member_digest,
            )
        )

    return UpgradePlan(
        component=component,
        items=tuple(items),
        policy=policy,
        is_component=is_component,
    )


def cmd_component(
    service: str,
    version: str,
    force: bool = False,
    digest: str | None = None,
    plan_only: bool = False,
) -> int:
    """Bump pin for one service or all descriptors owned by a component."""
    try:
        plan = build_component_upgrade_plan(service, version, digest=digest)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    holds = _load_holds()
    blocked = [item for item in plan.items if item.image in holds]
    if blocked and not force:
        for item in blocked:
            reason = holds[item.image].get("reason", "(no reason)")
            print(f"ERROR: {item.image} is HELD: {reason}", file=sys.stderr)
        print("  Use --force to bump anyway.", file=sys.stderr)
        return 1

    if plan.is_component:
        print(f"Upgrade plan for {plan.component} ({plan.policy}):")
        for item in plan.items:
            print(f"  {item.service}: {item.image}:{item.old_tag} -> {item.image}:{item.new_tag}")
        if plan_only:
            return 0
        changed_items = [item for item in plan.items if item.old_tag != item.new_tag]
        if not changed_items:
            print(f"{plan.component}: already at {version} (no change)")
            return 0
        for item in changed_items:
            _bump_pin_in_yaml(Path(item.yaml_path), item.new_tag, item.new_digest)
            print(f"  ✓ updated {item.yaml_path}")
        state_file = _save_upgrade_plan_state(plan)
        print(f"  ✓ saved upgrade state to {state_file}")
        print("  Next: `agmind upgrade --apply` to re-deploy")
        return 0

    item = plan.items[0]
    if plan_only:
        print(f"Upgrade plan for {service} ({plan.policy}):")
        print(f"  {item.service}: {item.image}:{item.old_tag} -> {item.image}:{item.new_tag}")
        return 0
    if item.old_tag == version:
        print(f"{service}: already at {item.old_tag} (no change)")
        return 0

    yaml_path = Path(item.yaml_path)
    print(f"Bumping {service}: {item.image}:{item.old_tag} → {item.image}:{version}")
    bumped_old, bumped_new = _bump_pin_in_yaml(yaml_path, version, digest)
    state_file = _save_upgrade_state(
        service,
        yaml_path,
        bumped_old,
        bumped_new,
        item.old_digest,
    )
    print(f"  ✓ updated {yaml_path}")
    print(f"  ✓ saved upgrade state to {state_file}")
    print("  Next: `agmind upgrade --apply` to re-deploy")
    return 0


def cmd_apply(
    install_dir: Path = Path("/opt/agmind"),
    healthcheck_timeout: int | None = None,
    skip_data_backup: bool = False,
) -> int:
    """Re-run deploy after bump. Reuses Phase L.B runner для snapshot+rollback.

    D-03 (P0.1 landing): the applied selection is READ from `deploy-state.json`
    instead of the historical `profiles=["core","rag","observability"]` hardcode —
    a re-apply can no longer silently ``--remove-orphans`` a service that was never
    part of the recorded baseline. Falls back to the legacy `setup-state.json`
    (best-effort — see `agmind.cli.install_state.load_prior_setup_state`) with a
    loud stderr WARNING when no deploy-state exists yet; refuses outright (no
    silent hardcoded last resort) when NEITHER is found.
    """
    from agmind.deploy.runner import deploy
    from agmind.deploy.state import load_deploy_state

    print(f"Re-deploying from {install_dir}")

    state = load_deploy_state(install_dir)
    if state is not None:
        profiles = state.profiles
        services = state.resolved_services or None
        domain = state.domain
    else:
        from agmind.cli.install_state import load_prior_setup_state
        from agmind.cli.tui.setup_wizard import STATE_PATH

        legacy = load_prior_setup_state(STATE_PATH)
        if legacy is None:
            print(
                f"ERROR: no deploy-state.json at {install_dir} and no legacy setup "
                f"state at {STATE_PATH}; refusing to guess what was previously "
                "applied. Run `agmind deploy --apply --profile <profiles>` "
                "explicitly, or `agmind install` first to establish a deploy state.",
                file=sys.stderr,
            )
            return 2
        print(
            f"WARNING: no deploy-state.json at {install_dir} yet; falling back to "
            f"legacy setup state at {STATE_PATH} (profiles={legacy.profiles}, "
            f"services={legacy.services}, domain={legacy.domain!r}) — "
            "deploy-state.json будет записан этим apply.",
            file=sys.stderr,
        )
        profiles = legacy.profiles
        services = legacy.services or None
        domain = legacy.domain or None

    try:
        result = deploy(
            profiles=profiles,
            services=services,
            install_dir=install_dir,
            domain=domain,
            apply=True,
            no_prompt=True,
            healthcheck_timeout=healthcheck_timeout,
            allow_removal=False,
            skip_data_backup=skip_data_backup,
            # Services come from deploy-state.resolved_services (already closure-resolved +
            # model-normalized); re-expanding would re-add a skipped llama-llm (P0).
            expand_closure=False,
        )
    except Exception as exc:
        print(f"ERROR: deploy crashed: {exc}", file=sys.stderr)
        return 1

    if not result.success:
        print(f"ERROR: {result.message}", file=sys.stderr)
        if result.rollback_performed:
            print("  Deployment rolled back to snapshot.")
        return 1

    print(f"✓ {result.message}")
    return 0


def _digest_currently_pinned(yaml_path: Path) -> bool:
    """True if `yaml_path` currently carries ANY digest pin (inline or separate).

    Used by the legacy-state rollback guard (D-01 / P0.2): a state file written
    before that fix has `old_digest=None` even when the descriptor WAS
    digest-pinned pre-upgrade, because `_read_current_pin` alone cannot see the
    separate `digest:` line. If the descriptor is STILL digest-pinned now
    (post-upgrade), a tag-only rollback would leave that stale NEW digest in
    place while restoring old_tag — silently deploying the new image under the
    restored old tag.
    """
    current = _read_current_pin(yaml_path)
    inline_digest = current[2] if current is not None else None
    return inline_digest is not None or _read_separate_digest(yaml_path) is not None


def _refuse_legacy_digest_rollback(yaml_path: Path, service: str, old_tag: str) -> None:
    print(
        f"ERROR: legacy upgrade-state for {service} has no recorded old_digest, but "
        f"{yaml_path} is currently digest-pinned. A tag-only rollback would restore "
        f"old_tag while leaving the NEW digest in place, so docker would resolve by "
        f"digest and silently keep deploying the NEW image under the restored old "
        f"tag. Re-pin {yaml_path} manually to the desired digest, or re-run "
        f"`agmind upgrade --component {service} --version {old_tag} --digest "
        f"<sha256-hex>` to rebuild a state file with old_digest recorded.",
        file=sys.stderr,
    )


def cmd_rollback() -> int:
    """Revert last bump (read latest state file + restore template)."""
    state = _latest_upgrade_state()
    if state is None:
        print("ERROR: no upgrade state found (nothing to rollback)", file=sys.stderr)
        return 1

    if "items" in state:
        component = state["component"]
        print(f"Rolling back component {component}")
        # D-01 (P0.2): pre-check EVERY member before mutating ANY of them, so a
        # legacy-state refuse on a later member never leaves earlier members
        # partially rolled back.
        for item in state["items"]:
            yaml_path = Path(item["yaml_path"])
            if not yaml_path.exists():
                print(f"ERROR: descriptor missing: {yaml_path}", file=sys.stderr)
                return 1
            if item.get("old_digest") is None and _digest_currently_pinned(yaml_path):
                _refuse_legacy_digest_rollback(yaml_path, item["service"], item["old_tag"])
                return 1

        for item in state["items"]:
            yaml_path = Path(item["yaml_path"])
            _bump_pin_in_yaml(yaml_path, item["old_tag"], item.get("old_digest"))
            print(f"  ✓ restored {item['service']}: {item['old_tag']}")

        _archive_latest_state()
        print("  Next: `agmind upgrade --apply` to re-deploy with restored pins")
        return 0

    yaml_path = Path(state["yaml_path"])
    service = state["service"]
    old_tag = state["old_tag"]
    old_digest = state.get("old_digest")

    if not yaml_path.exists():
        print(f"ERROR: descriptor missing: {yaml_path}", file=sys.stderr)
        return 1

    current = _read_current_pin(yaml_path)
    if current is None:
        print(f"ERROR: no image line in {yaml_path}", file=sys.stderr)
        return 1
    _, current_tag, _ = current

    # D-01 (P0.2): refuse BEFORE mutating rather than silently rolling back the
    # tag while leaving a stale (post-upgrade) digest in place.
    if old_digest is None and _digest_currently_pinned(yaml_path):
        _refuse_legacy_digest_rollback(yaml_path, service, old_tag)
        return 1

    print(f"Rolling back {service}: {current_tag} → {old_tag}")
    _bump_pin_in_yaml(yaml_path, old_tag, old_digest)
    print(f"  ✓ restored {yaml_path}")

    _archive_latest_state()
    print("  Next: `agmind upgrade --apply` to re-deploy with restored pin")
    return 0


def _archive_latest_state() -> None:
    """Move latest state file aside so the next rollback does not double-revert."""
    import shutil as _sh
    from datetime import datetime

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    state_files = sorted(UPGRADE_STATE_DIR.glob("*.json"), reverse=True)
    if state_files:
        archived_dir = UPGRADE_STATE_DIR / "rolled_back"
        archived_dir.mkdir(exist_ok=True)
        _sh.move(str(state_files[0]), str(archived_dir / f"{ts}_{state_files[0].name}"))


def register(app: typer.Typer) -> None:
    """Attach the ``upgrade`` command group to ``app``."""

    # ---- upgrade subcommand group (Phase M3.R) ----
    upgrade_app = typer.Typer(
        name="upgrade",
        help="Bump pinned image versions + safely redeploy with rollback.",
        no_args_is_help=False,
        invoke_without_command=True,
    )
    app.add_typer(upgrade_app)

    @upgrade_app.callback(invoke_without_command=True)
    def upgrade_cb(
        ctx: typer.Context,
        component: str | None = typer.Option(
            None,
            "--component",
            "-c",
            help="Service name (e.g. ragflow). Bump its image tag.",
        ),
        version: str | None = typer.Option(
            None,
            "--version",
            "-v",
            help="Target tag (e.g. v0.25.5). Required with --component.",
        ),
        digest: str | None = typer.Option(
            None,
            "--digest",
            help="Optional sha256 digest (without the `sha256:` prefix).",
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Run the version_check.py scanner and exit.",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Re-deploy after bump.",
        ),
        plan: bool = typer.Option(
            False,
            "--plan",
            help="Print component update plan without editing files.",
        ),
        rollback: bool = typer.Option(
            False,
            "--rollback",
            help="Revert last bump (read latest state + restore template).",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Bump even if the pin is held in version_holds.yaml.",
        ),
        healthcheck_timeout: int | None = typer.Option(
            None,
            "--healthcheck-timeout",
            help="Seconds to wait for healthy state on --apply (default: sized "
            "from the redeployed services' slowest start_period)",
        ),
        skip_data_backup: bool = typer.Option(
            False,
            "--skip-data-backup",
            help="Bypass the fresh-data-backup guard when --apply recreates a "
            "stateful service (default: refuse without a fresh "
            "`agmind backup --include-data` marker; P1-3).",
        ),
    ) -> None:
        """Upgrade lifecycle: bump a pinned image and safely redeploy with rollback."""
        if ctx.invoked_subcommand is not None:
            return

        if check:
            raise typer.Exit(code=cmd_check())
        if rollback:
            raise typer.Exit(code=cmd_rollback())
        if apply and not component:
            raise typer.Exit(
                code=cmd_apply(
                    healthcheck_timeout=healthcheck_timeout,
                    skip_data_backup=skip_data_backup,
                )
            )
        if component:
            if version is None:
                typer.echo("ERROR: --component requires --version", err=True)
                raise typer.Exit(code=2)
            rc = cmd_component(
                service=component,
                version=version,
                force=force,
                digest=digest,
                plan_only=plan,
            )
            if rc != 0 or not apply or plan:
                raise typer.Exit(code=rc)
            raise typer.Exit(
                code=cmd_apply(
                    healthcheck_timeout=healthcheck_timeout,
                    skip_data_backup=skip_data_backup,
                )
            )

        typer.echo(
            "Usage: agmind upgrade [--check | --component X --version Y "
            "[--plan] [--apply] | --apply | --rollback]"
        )
        raise typer.Exit(code=2)


__all__ = [
    "UpgradePlan",
    "UpgradePlanItem",
    "build_component_upgrade_plan",
    "cmd_check",
    "cmd_component",
    "cmd_apply",
    "cmd_rollback",
    "register",
]
