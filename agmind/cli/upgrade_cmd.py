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
import shutil
import subprocess
import sys
from pathlib import Path

from agmind.log import logger

log = logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_DIR = REPO_ROOT / "templates" / "services"
HOLDS_FILE = REPO_ROOT / "templates" / "version_holds.yaml"
UPGRADE_STATE_DIR = Path.home() / ".local" / "share" / "agmind" / "upgrades"

_IMAGE_LINE_RE = re.compile(r"^image:\s*(?P<image>[^\s:]+):(?P<tag>[^\s@]+)(?:@sha256:(?P<digest>[a-f0-9]+))?\s*$")


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


def _bump_pin_in_yaml(yaml_path: Path, new_tag: str, new_digest: str | None = None) -> tuple[str, str]:
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
    yaml_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return old_tag, new_tag


def _save_upgrade_state(
    service: str, yaml_path: Path, old_tag: str, new_tag: str,
    old_digest: str | None,
) -> Path:
    """Persist info для rollback. Returns saved state path."""
    UPGRADE_STATE_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    state_file = UPGRADE_STATE_DIR / f"{ts}_{service}.json"
    import json
    state_file.write_text(json.dumps({
        "service": service,
        "yaml_path": str(yaml_path),
        "old_tag": old_tag,
        "new_tag": new_tag,
        "old_digest": old_digest,
        "timestamp": ts,
    }, indent=2), encoding="utf-8")
    return state_file


def _latest_upgrade_state() -> dict | None:
    """Read most recent upgrade state file."""
    if not UPGRADE_STATE_DIR.exists():
        return None
    files = sorted(UPGRADE_STATE_DIR.glob("*.json"), reverse=True)
    if not files:
        return None
    import json
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---- main CLI entry points ----


def cmd_check() -> int:
    """Run scripts/version_check.py."""
    script = REPO_ROOT / "scripts" / "version_check.py"
    if not script.exists():
        print(f"ERROR: {script} not found", file=sys.stderr)
        return 1
    rc = subprocess.run(
        [sys.executable, str(script)], check=False,
    ).returncode
    return rc


def cmd_component(service: str, version: str, force: bool = False,
                  digest: str | None = None) -> int:
    """Bump pin для одного service в template + save rollback state."""
    yaml_path = _find_descriptor_for_service(service)
    if yaml_path is None:
        print(f"ERROR: no descriptor для service {service!r} в {SERVICES_DIR}",
              file=sys.stderr)
        return 1

    current = _read_current_pin(yaml_path)
    if current is None:
        print(f"ERROR: no `image:` line in {yaml_path}", file=sys.stderr)
        return 1
    image, old_tag, old_digest = current

    # Check holds
    holds = _load_holds()
    if image in holds and not force:
        reason = holds[image].get("reason", "(no reason)")
        print(f"ERROR: {image} is HELD: {reason}", file=sys.stderr)
        print(f"  Use --force to bump anyway.", file=sys.stderr)
        return 1

    if old_tag == version:
        print(f"{service}: already at {old_tag} (no change)")
        return 0

    print(f"Bumping {service}: {image}:{old_tag} → {image}:{version}")
    bumped_old, bumped_new = _bump_pin_in_yaml(yaml_path, version, digest)
    state_file = _save_upgrade_state(
        service, yaml_path, bumped_old, bumped_new, old_digest,
    )
    print(f"  ✓ updated {yaml_path}")
    print(f"  ✓ saved upgrade state to {state_file}")
    print(f"  Next: `agmind upgrade --apply` to re-deploy")
    return 0


def cmd_apply(install_dir: Path = Path("/opt/agmind"),
              healthcheck_timeout: int = 300) -> int:
    """Re-run deploy after bump. Reuses Phase L.B runner для snapshot+rollback."""
    from agmind.deploy.runner import deploy

    print(f"Re-deploying from {install_dir}")
    # Read profile из existing compose? Use sensible default.
    # User должен запускать `agmind deploy --apply` сам если хочет custom profiles.
    # Здесь — minimal: re-render по последнему saved состоянию.
    try:
        result = deploy(
            profiles=[],
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

    # Move state file aside so next --rollback не двойной revert
    import shutil as _sh
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    state_files = sorted(UPGRADE_STATE_DIR.glob("*.json"), reverse=True)
    if state_files:
        archived_dir = UPGRADE_STATE_DIR / "rolled_back"
        archived_dir.mkdir(exist_ok=True)
        _sh.move(str(state_files[0]), str(archived_dir / f"{ts}_{state_files[0].name}"))

    print(f"  Next: `agmind upgrade --apply` to re-deploy with restored pin")
    return 0


__all__ = ["cmd_check", "cmd_component", "cmd_apply", "cmd_rollback"]
