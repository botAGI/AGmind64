"""Landmines gate: scan the RENDERED compose for mechanically-checkable Правила.

Complements the descriptor-level gates (digest_check, no-unguarded-interp) by
asserting the same invariants on the renderer's actual OUTPUT. The real catalog
must produce zero hits; a deliberately poisoned compose must fire every landmine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "checks"))

import landmines_check  # noqa: E402

pytestmark = pytest.mark.backend_any

_POISONED = """\
services:
  bad:
    image: redis:latest  # audit: allow
    volumes:
      - cache:/data
    environment:
      FOO: ${UNGUARDED_VAR}
"""


def test_real_render_has_no_landmines() -> None:
    hits = landmines_check.check_landmines()
    assert hits == [], f"landmines in rendered catalog: {[h.landmine for h in hits]}"


def test_poisoned_compose_fires_every_landmine() -> None:
    hits = landmines_check.scan_rendered(_POISONED)
    fired = {h.landmine for h in hits}
    assert {"L01", "L02", "L03", "L04", "L05"} <= fired, f"only fired: {sorted(fired)}"


def test_clean_compose_passes() -> None:
    clean = """\
services:
  ok:
    image: redis:8.4.3-alpine@sha256:%s
    volumes:
      - /var/lib/agmind/redis:/data
    logging:
      options:
        max-size: 10m
""" % ("a" * 64)
    assert landmines_check.scan_rendered(clean) == []


def test_main_exit_code_zero_on_real_catalog() -> None:
    assert landmines_check.main([]) == 0


def test_main_json_mode_returns_zero_on_real_catalog(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = landmines_check.main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"ok": true' in out


def test_landmines_doc_mirrors_the_canonical_table() -> None:
    # Every landmine in code must be documented (single-source drift guard).
    doc = (_REPO_ROOT / "tests" / "lint" / "LANDMINES.md").read_text(encoding="utf-8")
    for landmine_id in landmines_check.LANDMINES:
        assert landmine_id in doc, f"{landmine_id} missing from LANDMINES.md"
