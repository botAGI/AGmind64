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

from agmind.core.files import write_text_atomic
from agmind.core.logging import logger

log = logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    if not HOLDS_FILE.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(HOLDS_FILE.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _find_descriptor_for_service(service_name: str) -> Path | None:
    """Find templates/services/<service>.yaml для image pin."""
    candidate = SERVICES_DIR / f"{service_name}.yaml"
    if candidate.exists():
        return candidate
    return None


def _read_current_pin(yaml_path: Path) -> tuple[str, str, str | None] | None:
    """Return (image, tag, digest_or_None) from descriptor. None if no `image:` line."""
    text = yaml_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        m = _IMAGE_LINE_RE.match(line)
        if m:
            return m.group("image"), m.group("tag"), m.group("digest")
    return None


def _bump_pin_in_yaml(
    yaml_path: Path, new_tag: str, new_digest: str | None = None
) -> tuple[str, str]:
    """Replace image tag (and digest) в descriptor. Returns (old_tag, new_tag)."""
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
            tag_part = f"{image}:{new_tag}"
            if new_digest:
                new_lines.append(f"image: {tag_part}@sha256:{new_digest}")
            else:
                new_lines.append(f"image: {tag_part}")
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
    if component in contracts:
        contract = contracts[component]
        service_names = contract.runtime.service_descriptors
        policy = contract.core.update_policy
        is_component = True
    else:
        service_names = (component,)
        policy = "service"
        is_component = False

    items: list[UpgradePlanItem] = []
    for service_name in service_names:
        yaml_path = _find_descriptor_for_service(service_name)
        if yaml_path is None:
            raise ValueError(f"no descriptor for service {service_name!r}")
        current = _read_current_pin(yaml_path)
        if current is None:
            raise ValueError(f"no image line in {yaml_path}")
        image, old_tag, old_digest = current
        items.append(
            UpgradePlanItem(
                service=service_name,
                yaml_path=str(yaml_path),
                image=image,
                old_tag=old_tag,
                new_tag=version,
                old_digest=old_digest,
                new_digest=digest,
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


def cmd_apply(install_dir: Path = Path("/opt/agmind"), healthcheck_timeout: int = 300) -> int:
    """Re-run deploy after bump. Reuses Phase L.B runner для snapshot+rollback."""
    from agmind.deploy.runner import deploy

    print(f"Re-deploying from {install_dir}")
    # Upgrade apply uses the supported Compose baseline. Custom profile sets
    # should be redeployed explicitly through `agmind deploy --apply`.
    try:
        result = deploy(
            profiles=["core", "rag", "observability"],
            install_dir=install_dir,
            domain=None,
            apply=True,
            no_prompt=True,
            healthcheck_timeout=healthcheck_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: deploy crashed: {exc}", file=sys.stderr)
        return 1

    if not result.success:
        print(f"ERROR: {result.message}", file=sys.stderr)
        if result.rollback_performed:
            print("  Deployment rolled back to snapshot.")
        return 1

    print(f"✓ {result.message}")
    return 0


def cmd_rollback() -> int:
    """Revert last bump (read latest state file + restore template)."""
    state = _latest_upgrade_state()
    if state is None:
        print("ERROR: no upgrade state found (nothing to rollback)", file=sys.stderr)
        return 1

    if "items" in state:
        component = state["component"]
        print(f"Rolling back component {component}")
        for item in state["items"]:
            yaml_path = Path(item["yaml_path"])
            if not yaml_path.exists():
                print(f"ERROR: descriptor missing: {yaml_path}", file=sys.stderr)
                return 1
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


__all__ = [
    "UpgradePlan",
    "UpgradePlanItem",
    "build_component_upgrade_plan",
    "cmd_check",
    "cmd_component",
    "cmd_apply",
    "cmd_rollback",
]
