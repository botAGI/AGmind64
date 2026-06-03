"""Disaster-recovery drill orchestrator (pure, hardware-free).

Runs the DR sequence — backup → integrity-verify → SANDBOX restore (into a throwaway
location, never the live install) → optional LIVE restore + health — measuring RTO.
The primitives are injected, so steps 1-3 are fully unit-tested offline with fakes;
the live restore (steps 5-6) is skipped by default and only runs on a real host.

A corrupt backup aborts before any restore — the whole point of the drill is to
catch that the backup is unusable BEFORE a real disaster.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DrillStepResult:
    name: str
    ok: bool
    detail: str

    def to_payload(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class DrillReport:
    steps: tuple[DrillStepResult, ...]
    ok: bool
    rto_seconds: float

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "rto_seconds": round(self.rto_seconds, 3),
            "steps": [s.to_payload() for s in self.steps],
        }


def run_drill(
    *,
    backup_fn: Callable[[], Path],
    verify_fn: Callable[[Path], list[str]],
    restore_fn: Callable[[Path], list[str]],
    live_restore_fn: Callable[[], bool] | None = None,
    health_fn: Callable[[], bool] | None = None,
    skip_restore: bool = True,
    clock: Callable[[], float] = time.monotonic,
) -> DrillReport:
    """Run the DR drill, returning a :class:`DrillReport` with per-step results + RTO.

    ``backup_fn`` returns the archive path; ``verify_fn`` returns integrity issues
    (empty = OK); ``restore_fn`` restores into a SANDBOX and returns restored labels.
    With ``skip_restore=False`` and ``live_restore_fn`` set, the live restore + health
    steps run (host-only). Aborts at the first failed step.
    """
    start = clock()
    steps: list[DrillStepResult] = []

    def _finish() -> DrillReport:
        return DrillReport(tuple(steps), all(s.ok for s in steps), clock() - start)

    try:
        archive = backup_fn()
        steps.append(DrillStepResult("backup", True, f"archive {archive}"))
    except Exception as exc:  # noqa: BLE001 - report any backup failure as a failed step
        steps.append(DrillStepResult("backup", False, str(exc)))
        return _finish()

    issues = verify_fn(archive)
    steps.append(
        DrillStepResult(
            "integrity", not issues, "ok" if not issues else f"{len(issues)} issue(s): {issues[0]}"
        )
    )
    if issues:
        return _finish()  # never restore from a corrupt backup

    try:
        restored = restore_fn(archive)
        steps.append(DrillStepResult("sandbox-restore", True, f"restored {len(restored)} label(s)"))
    except Exception as exc:  # noqa: BLE001
        steps.append(DrillStepResult("sandbox-restore", False, str(exc)))
        return _finish()

    if not skip_restore and live_restore_fn is not None:
        live_ok = live_restore_fn()
        steps.append(DrillStepResult("live-restore", live_ok, "ok" if live_ok else "failed"))
        if not live_ok:
            return _finish()
        if health_fn is not None:
            healthy = health_fn()
            steps.append(DrillStepResult("health", healthy, "healthy" if healthy else "unhealthy"))

    return _finish()
