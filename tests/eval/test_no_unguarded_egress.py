"""Phase 18 (M11) — structural guard: network I/O lives only in ``agmind/eval/clients/``.

Zero-egress for the eval harness is enforced by a chokepoint, not by convention: endpoints are
classified (``agmind.eval.endpoints``) and clients take the resulting verdict rather than a bare
URL. That only holds if no other module in the package can open a socket on its own.

AST rather than grep: a grep for "urlopen" is defeated by ``getattr(request, "url" + "open")``
and produces false positives on the word appearing in a docstring. Walking the tree asserts on
what the module actually *does*.

The existing A8 gate cannot cover this — it inspects service-descriptor env keys and has no hook
into CLI code (``scripts/checks/egress_telemetry_check.py`` docstring). The precedent for the
hole is already in-repo: ``agmind/loadtest/k6.py`` carries a DEFAULT_ENDPOINT that passes every
gate untouched.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_EVAL_PKG = Path(__file__).resolve().parents[2] / "agmind" / "eval"

#: Only this subpackage may perform network I/O.
_CLIENTS_DIR = _EVAL_PKG / "clients"

#: Modules whose import anywhere outside ``clients/`` means someone can reach the network.
_NETWORK_MODULES = frozenset({"socket", "http.client", "urllib.request", "ssl", "ftplib"})

#: Bare call names that open connections even when the module import looks innocent.
_NETWORK_CALLS = frozenset({"urlopen", "create_connection", "socket", "getaddrinfo"})


def _eval_modules() -> list[Path]:
    return sorted(p for p in _EVAL_PKG.rglob("*.py") if _CLIENTS_DIR not in p.parents)


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _NETWORK_MODULES or alias.name.split(".")[0] == "socket":
                    found.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _NETWORK_MODULES or module.split(".")[0] in {"socket", "ssl"}:
                found.append(f"{path.name}:{node.lineno} from {module} import ...")
            elif module in {"urllib", "http"}:
                for alias in node.names:
                    if f"{module}.{alias.name}" in _NETWORK_MODULES:
                        found.append(f"{path.name}:{node.lineno} from {module} import {alias.name}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else (func.id if isinstance(func, ast.Name) else "")
            )
            if name in _NETWORK_CALLS:
                found.append(f"{path.name}:{node.lineno} call {name}()")

    return found


def test_discovery_sees_the_package() -> None:
    """Guard the guard: an empty file list would make every assertion below vacuous."""
    modules = _eval_modules()
    assert modules, f"no modules discovered under {_EVAL_PKG}"
    assert any(p.name == "endpoints.py" for p in modules)


def test_no_network_io_outside_clients_package() -> None:
    offences: list[str] = []
    for path in _eval_modules():
        offences.extend(_offences(path))

    assert not offences, (
        "network I/O found outside agmind/eval/clients/ — the egress chokepoint is bypassed:\n"
        + "\n".join(f"  - {o}" for o in offences)
        + "\n\nMove the call into agmind/eval/clients/ and take an EndpointVerdict."
    )


def test_guard_detects_a_planted_offence(tmp_path: Path) -> None:
    """Mutation check: the detector must actually fire, otherwise the test above is decorative."""
    planted = tmp_path / "sneaky.py"
    planted.write_text(
        "from urllib.request import urlopen\n\n\ndef go():\n    return urlopen('http://x')\n",
        encoding="utf-8",
    )
    offences = _offences(planted)
    assert any("import" in o for o in offences)
    assert any("call urlopen()" in o for o in offences)
