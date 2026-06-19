"""Phase P: tests for scripts/checks/version_check.py (regex + compare + report)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "checks" / "version_check.py"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


# ---- semver compare ----


def test_compare_up_to_date() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._compare("v1.2.3", "1.2.3") == "up_to_date"


def test_compare_patch() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._compare("1.2.3", "1.2.5") == "patch"


def test_compare_minor() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._compare("1.2.3", "1.3.0") == "minor"


def test_compare_major() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._compare("1.2.3", "2.0.0") == "major"


def test_compare_current_newer_than_probe() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._compare("v0.57.0", "v0.55.1") == "newer_than_probe"


# ---- compose pin scanner ----


def test_scan_compose_finds_known_pins() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_compose_pins(REPO_ROOT / "templates" / "services")
    images = {p[0] for p in pins}
    assert "infiniflow/ragflow" in images
    assert "langgenius/dify-api" in images
    assert "ghcr.io/ggml-org/llama.cpp" in images


def test_scan_extracts_correct_tag() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_compose_pins(REPO_ROOT / "templates" / "services")
    by_image = {p[0]: p[1] for p in pins}
    assert by_image["infiniflow/ragflow"] == "v0.25.5"


def test_scan_compose_skips_on_host_build_services() -> None:
    """Build-on-host images (agmind-agent-*, compose `build:`) carry no registry digest and have
    NO upstream version — the probe can only ❌ "probe returned no version" on them. They must not
    be scanned at all (operator screenshot: 3 permanent agent errors)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    images = {p[0] for p in vc.scan_compose_pins(REPO_ROOT / "templates" / "services")}
    assert "agmind-agent-agno" not in images
    assert "agmind-agent-pydanticai" not in images
    assert "agmind-agent-ui" not in images
    # real pulled images are still tracked
    assert "infiniflow/ragflow" in images


def test_scan_dockerfile_skips_test_dockerfiles() -> None:
    """Dockerfile.*-test are CI fixtures (ubuntu-test = clean-machine bootstrap), not shipped
    components — their FROM base must not surface as a tracked component (the absurd
    ubuntu 24.04→26.04 row). Real build Dockerfiles (their upstream base IS actionable) stay."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_dockerfile_pins(REPO_ROOT / "docker")
    files = {p[2] for p in pins}
    assert files, "expected real Dockerfile FROM pins to remain"
    assert not any(f.endswith("-test") for f in files), files


def test_scan_pyproject_deps_finds_textual() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_pyproject_deps(REPO_ROOT / "pyproject.toml")
    by_name = {pin.name: pin for pin in pins}
    assert by_name["textual"].specifier == ">=0.80,<9"
    assert by_name["ansible-core"].source == "pyproject"


def test_scan_ansible_collections_finds_community_docker() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_ansible_collections(REPO_ROOT / "ansible" / "requirements.yml")
    by_name = {pin.name: pin for pin in pins}
    assert by_name["community.docker"].specifier == ">=4.0.0"


def test_scan_dockerfile_pip_installs_finds_llama_cpp_python() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_dockerfile_pip_specs(REPO_ROOT / "docker")
    assert any(pin.name == "llama-cpp-python" and pin.specifier == ">=0.3.23,<0.4" for pin in pins)


def test_scan_constraint_specs_finds_backend_planes() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    pins = vc.scan_constraint_specs(REPO_ROOT / "constraints")
    by_source_name = {(pin.source, pin.name): pin for pin in pins}

    assert by_source_name[("constraint:core", "typer")].specifier == ">=0.26,<1"
    assert by_source_name[("constraint:vulkan", "llama-cpp-python")].file == (
        "constraints/vulkan.txt"
    )
    assert by_source_name[("constraint:rocm-gfx1151", "torch")].specifier == ">=2.7,<3"


# ---- holds parser ----


def test_holds_loaded() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    holds = vc.load_holds()
    assert "ghcr.io/ggml-org/llama.cpp" in holds


# ---- end-to-end (offline mode) ----


def test_offline_smoke_runs_no_crash() -> None:
    """`scripts/checks/version_check.py --offline` должен бежать без network."""
    p = _run("--offline")
    # Output goes to stdout — markdown
    assert p.returncode == 0
    assert "Upstream Version Check" in p.stdout
    assert "ragflow" in p.stdout.lower()


def test_offline_json_output(tmp_path: Path) -> None:
    json_out = tmp_path / "out.json"
    p = _run("--offline", "--json", str(json_out))
    assert p.returncode == 0
    data = json.loads(json_out.read_text())
    assert isinstance(data, list)
    assert any(d["image"] == "infiniflow/ragflow" for d in data)


def test_version_check_workflow_uses_runner_local_python() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "version-check.yml").read_text(
        encoding="utf-8"
    )

    assert "actions/setup-python" not in workflow
    assert '"$HOME/.local/bin/uv" venv --python python3 .venv' in workflow
    assert (
        '"$HOME/.local/bin/uv" pip install --python .venv/bin/python -c constraints/dev.txt pyyaml'
    ) in workflow
    assert (
        ".venv/bin/python scripts/checks/version_check.py --output report.md --json report.json"
        in workflow
    )


def test_holds_skip_probe() -> None:
    """Образы из version_holds.yaml выходят как 'hold' даже в online mode."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    reports = vc.build_reports(probe_fn=lambda _img: "v999.999.999")
    holds_in_report = [r for r in reports if r.status == "hold"]
    images = {r.image for r in holds_in_report}
    # ghcr.io/ggml-org/llama.cpp is held per templates/version_holds.yaml
    assert "ghcr.io/ggml-org/llama.cpp" in images


def test_markdown_table_format() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    reports = vc.build_reports(probe_fn=lambda _img: None)
    md = vc.render_markdown(reports)
    assert "| Component |" in md
    assert "### Legend" in md
    assert "### How to bump" in md


# ---- M3.P.fix: variant / prerelease / SHA filtering ----


def test_variant_filter_windowsservercore() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._is_variant_or_prerelease("2.11.3-windowsservercore-ltsc2025")
    assert vc._is_variant_or_prerelease("v3.7.1-windowsservercore-ltsc2025")


def test_variant_filter_arch_suffix() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._is_variant_or_prerelease("3.7.2-arm64")  # audit: allow test-string
    assert vc._is_variant_or_prerelease("v1.18-unprivileged")
    # MinIO RELEASE.* tag не semver — фильтруется через _VERSION_RE регэкса,
    # не через _is_variant_or_prerelease.
    assert not vc._VERSION_RE.match("RELEASE.2025-09-07T16-13-09Z.hotfix.7aa24e772")


def test_variant_filter_os_variants() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._is_variant_or_prerelease("13.1.0-25893932881-ubuntu")
    assert vc._is_variant_or_prerelease("1.31.0-trixie-perl")
    assert vc._is_variant_or_prerelease("v1.83.0-alpine")
    assert vc._is_variant_or_prerelease("9.7.0-oraclelinux9")


def test_variant_filter_prerelease() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._is_variant_or_prerelease("v3.12.0-rc.0-distroless")
    assert vc._is_variant_or_prerelease("v0.32.1-rc.0")
    assert vc._is_variant_or_prerelease("1.38.0-dev-fc90344-arm64")  # audit: allow test-string


def test_variant_filter_sha_tags() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    # 40-char hex string = SHA tag
    assert vc._is_variant_or_prerelease("5631afef06ec88f80c28129aec7fd22a30006b14")
    assert vc._is_variant_or_prerelease("2565637e36448ae343a146281453a04a3a16fba7")


def test_variant_filter_plain_semver_passes() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    # Plain semver — should NOT be filtered
    assert not vc._is_variant_or_prerelease("v0.25.5")
    assert not vc._is_variant_or_prerelease("1.14.2")
    assert not vc._is_variant_or_prerelease("v3.5.3")
    assert not vc._is_variant_or_prerelease("4.39.19")


def test_probe_dispatch_quay() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    # No network — just verify dispatch picks Quay handler
    called = []
    vc._quay_latest = lambda owner, image: called.append((owner, image)) or "v9.9.9"
    assert vc.probe_latest("quay.io/minio/minio") == "v9.9.9"
    assert called[0] == ("minio", "minio")


def test_probe_dispatch_gcr() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    called = []
    vc._gcr_latest = lambda project, image: called.append((project, image)) or "v0.99.0"
    assert vc.probe_latest("gcr.io/cadvisor/cadvisor") == "v0.99.0"
    assert called[0] == ("cadvisor", "cadvisor")


# ---- issue #7: report-quality fixes (parser / filters / non-semver / holds / ghcr) ----


def test_parse_semver_handles_version_prefix_tag() -> None:
    """Arize Phoenix tags as ``version-X.Y.Z``. The old ``lstrip('v')`` turned
    ``version`` into ``ersion`` → parse failed → the row showed a useless ``? unknown``.
    The parser must recognise the ``version-`` prefix."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._parse_semver("version-17.2.0")[:3] == (17, 2, 0)
    assert vc._parse_semver("version-17.2.0-nonroot")[:3] == (17, 2, 0)


def test_compare_phoenix_version_prefix_is_up_to_date() -> None:
    """phoenix pin ``version-17.2.0-nonroot`` vs probed ``version-17.2.0`` must compare
    equal (up_to_date), not ``unknown``."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._compare("version-17.2.0-nonroot", "version-17.2.0") == "up_to_date"
    # a plain leading-v tag must still parse (regression guard for the prefix change)
    assert vc._compare("v1.2.3", "1.2.3") == "up_to_date"


def test_variant_filter_debug_rootless_nonroot() -> None:
    """The probe picked junk 'latest' tags because these variant suffixes were not filtered:
    milvus ``v2.6.18-gpu-debug``, uptime-kuma ``2.4.0-rootless``, phoenix ``-nonroot``."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._is_variant_or_prerelease("v2.6.18-gpu-debug")
    assert vc._is_variant_or_prerelease("v2.6.18-gpu")  # bare accelerator variant (unused on AMD)
    assert vc._is_variant_or_prerelease("v2.6.18-debug")
    assert vc._is_variant_or_prerelease("2.4.0-rootless")
    assert vc._is_variant_or_prerelease("version-17.2.0-nonroot")


def test_variant_filter_short_git_sha_suffix() -> None:
    """n8n publishes ``2.25.5-85bf84a`` (version + short git sha) per build — a nightly,
    not a stable release. The all-hex 7+ char suffix must be filtered."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    assert vc._is_variant_or_prerelease("2.25.5-85bf84a")
    assert vc._is_variant_or_prerelease("v1.38.0-636f483")
    # a plain semver and a normal patch tag must NOT be filtered
    assert not vc._is_variant_or_prerelease("2.25.5")
    assert not vc._is_variant_or_prerelease("v1.18.2")


def test_minio_calendar_tag_reports_non_semver_not_error() -> None:
    """minio uses calendar ``RELEASE.<date>`` tags, not semver — the probe returns None.
    That must surface as an informative ``non_semver`` status, not a scary ``❌ error`` that
    makes the whole report look broken. A genuinely-semver image with a failed probe stays
    ``error``."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    reports = vc.build_reports(probe_fn=lambda _img: None)
    by_image = {r.image: r for r in reports}
    assert by_image["quay.io/minio/minio"].status == "non_semver"
    # ragflow's current pin IS semver (v0.25.5); a None probe is a real error, not non_semver
    assert by_image["infiniflow/ragflow"].status == "error"


def test_non_semver_status_has_glyph_and_legend() -> None:
    """The non_semver status renders with its own glyph + a legend entry (not '?')."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    reports = vc.build_reports(probe_fn=lambda _img: None)
    md = vc.render_markdown(reports)
    minio = next(r for r in reports if r.image == "quay.io/minio/minio")
    assert minio.glyph != "?"
    assert "non_semver" in md  # legend documents it


def test_dify_stack_and_mysql_are_held() -> None:
    """dify-web/dify-sandbox are part of the Dify stack (co-bumped with dify-api, which is
    already held) — they must be held too so they don't probe-error in isolation. mysql is
    pinned to 8.x for Dify; hold it so the 9.x major isn't flagged as an actionable bump."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    holds = vc.load_holds()
    assert "langgenius/dify-web" in holds
    assert "langgenius/dify-sandbox" in holds
    assert "mysql" in holds


def test_ghcr_probe_prefers_github_releases(monkeypatch: pytest.MonkeyPatch) -> None:
    """ghcr ``/tags/list`` is lexical + capped (n=200), so for big repos it MISSES the
    newest tag (homarr showed ``newer_than_probe``: pinned v1.62.0, probe only saw v1.59.3).
    The GitHub Releases API is authoritative — prefer it for ghcr.io/<owner>/<image>."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    monkeypatch.setattr(
        vc,
        "_github_release_latest",
        lambda owner, repo: "v1.62.0" if (owner, repo) == ("homarr-labs", "homarr") else None,
    )
    # must NOT fall through to the (network) tag-list path when releases answers
    monkeypatch.setattr(
        vc,
        "_http_get_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not hit tags/list")),
    )
    assert vc._ghcr_latest("homarr-labs", "homarr") == "v1.62.0"


def test_ghcr_probe_falls_back_to_tags_when_no_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """If GitHub Releases has nothing usable, the ghcr tag-list path is still used."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "checks"))
    import version_check as vc

    monkeypatch.setattr(vc, "_github_release_latest", lambda owner, repo: None)
    monkeypatch.setattr(
        vc,
        "_http_get_json",
        lambda url, headers=None, timeout=10: (
            {"token": "t"} if "token" in url else {"tags": ["v1.0.0", "v1.2.0", "latest"]}
        ),
    )
    assert vc._ghcr_latest("acme", "widget") == "v1.2.0"
