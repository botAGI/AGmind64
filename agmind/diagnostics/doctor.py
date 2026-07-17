"""`agmind doctor` — preflight + health diagnostics.

Не делает изменений системы — read-only check. Возвращает list нарушений
+ optional auto-fix suggestions.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agmind.compute.detect import detect_host
from agmind.core.logging import logger

log = logger(__name__)

MIN_COMPOSE_VERSION = (2, 24, 0)


@dataclass(frozen=True)
class CheckResult:
    """Single preflight check result."""

    name: str
    """Short identifier: "kernel-version" / "vulkan-installed" etc."""

    status: str
    """"ok" | "warn" | "fail" | "skip"."""

    message: str
    """Human-readable message."""

    fix_hint: str = ""
    """Optional one-liner suggestion (commands, links to HARDWARE.md)."""


@dataclass
class DoctorReport:
    """Композиция всех preflight результатов."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(c.status == "fail" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warn" for c in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "total": len(self.checks),
                "ok": sum(1 for c in self.checks if c.status == "ok"),
                "warn": sum(1 for c in self.checks if c.status == "warn"),
                "fail": sum(1 for c in self.checks if c.status == "fail"),
                "skip": sum(1 for c in self.checks if c.status == "skip"),
            },
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "fix_hint": c.fix_hint,
                }
                for c in self.checks
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


def _check_kernel() -> CheckResult:
    host = detect_host()
    try:
        major, minor, patch = host.kernel_version.split("-", 1)[0].split(".")[:3]
        kv = (int(major), int(minor), int(patch))
    except (ValueError, IndexError):
        return CheckResult(
            name="kernel-version",
            status="warn",
            message=f"Cannot parse kernel version: {host.kernel_version}",
        )
    if kv >= (6, 18, 4):
        return CheckResult(
            name="kernel-version",
            status="ok",
            message=f"Kernel {host.kernel_version} ≥ 6.18.4 mainline",
        )
    if kv >= (6, 17, 0):
        return CheckResult(
            name="kernel-version",
            status="warn",
            message=(
                f"Kernel {host.kernel_version} below 6.18.4 mainline. "
                "OK if this is Ubuntu HWE 6.17.0-19+; otherwise ROCm may see "
                "only ~15.5 GiB VRAM (ROCm/issues/5444)."
            ),
            fix_hint=("sudo apt install --install-recommends linux-generic-hwe-24.04"),
        )
    return CheckResult(
        name="kernel-version",
        status="fail",
        message=(
            f"Kernel {host.kernel_version} too old. "
            "Strix Halo gfx1151 requires ≥6.17.0 (HWE) or 6.18.4 (mainline)."
        ),
        fix_hint=("sudo apt install --install-recommends linux-generic-hwe-24.04 && reboot"),
    )


def _check_strix_halo_gpu() -> CheckResult:
    host = detect_host()
    if host.gpu is None:
        return CheckResult(
            name="gpu-detected",
            status="warn",
            message="No AMD GPU detected (CPU fallback only)",
        )
    if not host.gpu.is_strix_halo:
        return CheckResult(
            name="gpu-detected",
            status="warn",
            message=f"GPU detected but not Strix Halo: {host.gpu.name}",
        )
    return CheckResult(
        name="gpu-detected",
        status="ok",
        message=f"{host.gpu.name} (PCI 0x{host.gpu.pci_id:04x})",
    )


def _check_gtt_pool() -> CheckResult:
    host = detect_host()
    if host.gpu is None or not host.gpu.is_strix_halo:
        return CheckResult(
            name="gtt-pool",
            status="skip",
            message="Not on Strix Halo",
        )
    gib = host.gpu.gtt_total_bytes / 1024**3
    ram_gib = host.system_ram_bytes / 1024**3
    if gib >= ram_gib * 0.7:
        return CheckResult(
            name="gtt-pool",
            status="ok",
            message=f"GTT pool {gib:.1f} GiB on {ram_gib:.0f} GiB RAM",
        )
    pages = int(host.system_ram_bytes * 0.94 / 4096)
    return CheckResult(
        name="gtt-pool",
        status="warn",
        message=(f"GTT pool only {gib:.1f} GiB on {ram_gib:.0f} GiB RAM — sub-optimal."),
        fix_hint=(
            f"Add to GRUB cmdline: ttm.pages_limit={pages} "
            "ttm.page_pool_size=" + str(pages) + " — see docs/HARDWARE.md."
        ),
    )


def _check_bios_uma() -> CheckResult:
    host = detect_host()
    if host.gpu is None or not host.gpu.is_strix_halo:
        return CheckResult(
            name="bios-uma",
            status="skip",
            message="Not on Strix Halo",
        )
    gib = host.gpu.bios_uma_bytes / 1024**3
    if gib <= 2.0:
        return CheckResult(
            name="bios-uma",
            status="ok",
            message=f"BIOS UMA frame buffer = {gib:.2f} GiB (optimal)",
        )
    return CheckResult(
        name="bios-uma",
        status="warn",
        message=(
            f"BIOS UMA frame buffer = {gib:.1f} GiB. "
            "On Linux set to 512 MB minimum (see docs/HARDWARE.md)."
        ),
        fix_hint="Reboot to BIOS → AMD CBS → GFX Configuration → UMA Frame Buffer Size = Auto/512 MB",
    )


def _check_amdvlk_absent() -> CheckResult:
    host = detect_host()
    leaked = host.vulkan.amdvlk_files_present
    if not leaked:
        return CheckResult(
            name="amdvlk-absent",
            status="ok",
            message="No AMDVLK ICD files detected (RADV only)",
        )
    return CheckResult(
        name="amdvlk-absent",
        status="fail",
        message=(
            "AMDVLK ICD files detected — they silently override RADV "
            "and have a 2 GiB allocation cap that breaks LLM ≥30B."
        ),
        fix_hint="sudo rm -f " + " ".join(leaked),
    )


def _check_vulkan_tooling() -> CheckResult:
    if not shutil.which("vulkaninfo"):
        return CheckResult(
            name="vulkan-tooling",
            status="warn",
            message="vulkaninfo not installed — Vulkan backend disabled",
            fix_hint="sudo apt install vulkan-tools mesa-vulkan-drivers libvulkan1",
        )
    host = detect_host()
    vk = host.vulkan
    if not vk.available:
        return CheckResult(
            name="vulkan-tooling",
            status="warn",
            message="vulkaninfo present but did not return device info",
        )
    if vk.driver_name != "radv":
        return CheckResult(
            name="vulkan-tooling",
            status="fail",
            message=f"Active Vulkan driver is {vk.driver_name!r}, expected 'radv'",
            fix_hint="See docs/HARDWARE.md § 'Vulkan driver'",
        )
    if vk.mesa_version is None or vk.mesa_version < (25, 2, 8):
        ver = ".".join(map(str, vk.mesa_version)) if vk.mesa_version else "unknown"
        return CheckResult(
            name="vulkan-tooling",
            status="warn",
            message=f"Mesa {ver} below recommended 25.2.8",
            fix_hint="sudo add-apt-repository ppa:kisak/kisak-mesa && sudo apt upgrade",
        )
    return CheckResult(
        name="vulkan-tooling",
        status="ok",
        message=f"Vulkan RADV + Mesa {'.'.join(map(str, vk.mesa_version))}",
    )


def _check_rocm_tooling() -> CheckResult:
    if not shutil.which("rocminfo"):
        return CheckResult(
            name="rocm-tooling",
            status="warn",
            message="rocminfo not installed — ROCm backend disabled (Vulkan still usable)",
            fix_hint="See docs/HARDWARE.md § 'ROCm install'",
        )
    host = detect_host()
    if "gfx1151" not in host.rocm.gfx_targets and "gfx11-generic" not in host.rocm.gfx_targets:
        return CheckResult(
            name="rocm-tooling",
            status="warn",
            message=(
                f"rocminfo found but no gfx1151 target: {host.rocm.gfx_targets}. "
                "Need ROCm ≥ 7.2 with gfx1151 support."
            ),
            fix_hint="ROCm 7.2.x install — see docs/HARDWARE.md",
        )
    return CheckResult(
        name="rocm-tooling",
        status="ok",
        message=f"ROCm with gfx targets: {list(host.rocm.gfx_targets)}",
    )


def _check_docker_compose() -> CheckResult:
    """Docker Engine + Compose plugin required for the TUI install path."""
    fix_hint = (
        "Install official Docker Engine packages: sudo apt install docker-ce "
        "docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    )
    if not shutil.which("docker"):
        return CheckResult(
            name="docker-compose",
            status="warn",
            message="docker command not found; bootstrap can install Docker Engine",
            fix_hint=fix_hint,
        )
    try:
        result = subprocess.run(
            ["docker", "compose", "version", "--short"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            name="docker-compose",
            status="warn",
            message=f"docker compose version failed: {exc}",
            fix_hint=fix_hint,
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return CheckResult(
            name="docker-compose",
            status="warn",
            message=f"docker compose plugin unavailable: {detail or result.returncode}",
            fix_hint=fix_hint,
        )

    version = _parse_version(result.stdout)
    if version is None:
        return CheckResult(
            name="docker-compose",
            status="warn",
            message=f"Cannot parse Docker Compose version: {result.stdout.strip()!r}",
            fix_hint="Run: docker compose version --short",
        )
    if version < MIN_COMPOSE_VERSION:
        return CheckResult(
            name="docker-compose",
            status="warn",
            message=(
                "Docker Compose "
                f"{_format_version(version)} is below required 2.24.0+ "
                "for the self-hosted AI stack; bootstrap should update it."
            ),
            fix_hint=fix_hint,
        )
    return CheckResult(
        name="docker-compose",
        status="ok",
        message=f"Docker Compose {_format_version(version)}",
    )


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3) or 0),
    )


def _format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _check_user_groups() -> CheckResult:
    """User должен быть в render + video для /dev/kfd, /dev/dri access."""
    import grp
    import os
    import pwd

    uid = os.getuid()
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        return CheckResult(
            name="user-groups",
            status="skip",
            message="Cannot resolve current user",
        )

    user_groups = {g.gr_name for g in grp.getgrall() if user in g.gr_mem}
    user_groups.add(grp.getgrgid(pwd.getpwuid(uid).pw_gid).gr_name)
    missing = {"render", "video"} - user_groups
    if not missing:
        return CheckResult(
            name="user-groups",
            status="ok",
            message=f"User {user!r} in render + video",
        )
    return CheckResult(
        name="user-groups",
        status="warn",
        message=f"User {user!r} not in: {sorted(missing)}",
        fix_hint=f"sudo usermod -aG video,render {user} && newgrp render",
    )


def _check_devices() -> CheckResult:
    """Validate GPU device nodes only when an AMD GPU is detected."""
    host = detect_host()
    if host.gpu is None:
        return CheckResult(
            name="devices",
            status="skip",
            message="CPU fallback: GPU devices are not required",
        )

    devices = ["/dev/dri"]
    if shutil.which("rocminfo"):
        devices.append("/dev/kfd")
    missing = [d for d in devices if not Path(d).exists()]
    if not missing:
        return CheckResult(
            name="devices",
            status="ok",
            message=f"Devices present: {devices}",
        )
    return CheckResult(
        name="devices",
        status="fail",
        message=f"Missing devices: {missing}",
        fix_hint="Install amdgpu-dkms; reboot if just installed",
    )


_MES_FW_DEBUGFS = "/sys/kernel/debug/dri/{index}/amdgpu_firmware_info"
_MES_FEATURE_RE = re.compile(r"^MES feature version:\s*\d+,\s*firmware version:\s*(0x[0-9a-fA-F]+)")
_MES_FW_KNOWN_BAD = 0x83  # zenn 9-mo report (web-sourced) — warn only, never auto-pin.


def _read_mes_firmware_hex(card_index: int) -> str | None:
    """Read the MES firmware version hex string from root-only debugfs.

    `/sys/kernel/debug` is `drwx------ root root` — PermissionError/FileNotFoundError
    propagate to the caller, which degrades to status="skip" (never auto-sudo).
    """
    text = Path(_MES_FW_DEBUGFS.format(index=card_index)).read_text()
    for line in text.splitlines():
        match = _MES_FEATURE_RE.match(line.strip())
        if match:
            return match.group(1)
    return None


def _check_mes_firmware() -> CheckResult:
    """Best-effort MES firmware advisory — read-only, never auto-sudo (D-03).

    `/sys/kernel/debug/dri/<N>/amdgpu_firmware_info` is root-only; a non-root
    `agmind doctor` run almost always degrades to status="skip" here — that is
    the designed, honest behaviour, not a bug.
    """
    host = detect_host()
    if host.gpu is None or not host.gpu.is_strix_halo:
        return CheckResult(
            name="mes-firmware",
            status="skip",
            message="Not on Strix Halo",
        )

    match = re.search(r"(\d+)$", host.gpu.card_path)
    if not match:
        return CheckResult(
            name="mes-firmware",
            status="skip",
            message=f"Cannot parse card index from {host.gpu.card_path!r}",
        )
    card_index = int(match.group(1))
    manual_hint = "sudo cat /sys/kernel/debug/dri/*/amdgpu_firmware_info | grep '^MES '"

    try:
        hex_str = _read_mes_firmware_hex(card_index)
    except PermissionError:
        return CheckResult(
            name="mes-firmware",
            status="skip",
            message="Could not read MES firmware version (debugfs is root-only)",
            fix_hint=f"Run manually: {manual_hint}",
        )
    except FileNotFoundError:
        return CheckResult(
            name="mes-firmware",
            status="skip",
            message=f"MES firmware debugfs path not found for card{card_index}",
        )

    if hex_str is None:
        return CheckResult(
            name="mes-firmware",
            status="skip",
            message="Could not determine MES firmware version",
        )

    try:
        version = int(hex_str, 16)
    except ValueError:
        return CheckResult(
            name="mes-firmware",
            status="skip",
            message=f"Could not parse MES firmware version: {hex_str!r}",
        )

    if version == _MES_FW_KNOWN_BAD:
        return CheckResult(
            name="mes-firmware",
            status="warn",
            message=f"MES firmware 0x{version:02x} is known-buggy (GPU hangs under load)",
            fix_hint="Pin MES firmware to 0x80; re-check after kernel/firmware updates",
        )
    return CheckResult(
        name="mes-firmware",
        status="ok",
        message=f"MES firmware 0x{version:02x}",
    )


_CHECKS = (
    _check_strix_halo_gpu,
    _check_kernel,
    _check_bios_uma,
    _check_gtt_pool,
    _check_devices,
    _check_mes_firmware,
    _check_user_groups,
    _check_amdvlk_absent,
    _check_vulkan_tooling,
    _check_rocm_tooling,
    _check_docker_compose,
)


def run_preflight() -> DoctorReport:
    """Run all preflight checks. Returns aggregated report."""
    report = DoctorReport()
    for check_fn in _CHECKS:
        try:
            result = check_fn()
        except Exception as exc:
            result = CheckResult(
                # removeprefix, NOT lstrip — lstrip strips the CHAR SET {_,c,h,e,k} so
                # _check_kernel → 'rnel' on a raised check (review LOW doctor-lstrip-check-name).
                name=check_fn.__name__.removeprefix("_check_"),
                status="fail",
                message=f"Check raised: {exc!r}",
            )
        report.checks.append(result)
    return report


def doctor_report(*, as_json: bool = False, color: bool | None = None) -> str:
    """Human-readable или JSON отчёт. Runs preflight, then formats.

    Thin wrapper kept for back-compat. Callers that need the exit code should
    run :func:`run_preflight` once and pass the report to
    :func:`format_doctor_report` instead, to avoid running checks twice.
    """
    return format_doctor_report(run_preflight(), as_json=as_json, color=color)


def format_doctor_report(
    report: DoctorReport, *, as_json: bool = False, color: bool | None = None
) -> str:
    """Render an already-computed :class:`DoctorReport` to text or JSON.

    Phase M4.4: colored output via Rich markup. Enable controlled by:
      - `color` kwarg (None → auto-detect: True if stdout TTY, False otherwise)
      - `NO_COLOR=1` env var → forced off
    """
    if as_json:
        return report.to_json()

    # Auto-detect color support
    if color is None:
        import os as _os
        import sys as _sys

        color = (
            _sys.stdout.isatty()
            and _os.environ.get("NO_COLOR", "") == ""
            and _os.environ.get("AGMIND_NO_COLOR", "") == ""
        )

    if color:
        # Use rich to render markup → ANSI codes
        try:
            from io import StringIO

            from rich.console import Console

            buf = StringIO()
            console = Console(file=buf, force_terminal=True, color_system="truecolor", width=120)
            _render_colored(report, console)
            return buf.getvalue().rstrip("\n")
        except ImportError:
            pass  # fall through to plain

    # Plain (no color) — legacy format preserved
    lines: list[str] = []
    icons = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}
    for c in report.checks:
        icon = icons.get(c.status, "?")
        lines.append(f"  {icon} {c.name:24s} {c.message}")
        if c.fix_hint and c.status in ("warn", "fail"):
            lines.append(f"      → {c.fix_hint}")

    lines.insert(0, "")
    lines.insert(
        0,
        "AGmind doctor — "
        f"{sum(1 for c in report.checks if c.status == 'ok')} ok / "
        f"{sum(1 for c in report.checks if c.status == 'warn')} warn / "
        f"{sum(1 for c in report.checks if c.status == 'fail')} fail / "
        f"{sum(1 for c in report.checks if c.status == 'skip')} skip",
    )
    return "\n".join(lines)


def _render_colored(report, console) -> None:  # type: ignore[no-untyped-def]
    """Print doctor report через Rich console (M4.4)."""
    summary = report.to_dict()["summary"]
    header = (
        f"[bold]AGmind doctor[/bold]  —  "
        f"[green]{summary['ok']} ok[/green] / "
        f"[yellow]{summary['warn']} warn[/yellow] / "
        f"[red]{summary['fail']} fail[/red] / "
        f"[dim]{summary['skip']} skip[/dim]"
    )
    console.print(header)
    console.print()

    style_map = {
        "ok": ("[green]✓[/green]", "default"),
        "warn": ("[yellow]⚠[/yellow]", "yellow"),
        "fail": ("[red]✗[/red]", "red"),
        "skip": ("[dim]·[/dim]", "dim"),
    }
    for c in report.checks:
        glyph, msg_style = style_map.get(c.status, ("?", "default"))
        if msg_style == "default":
            console.print(f"  {glyph}  [bold]{c.name:<24s}[/bold] {c.message}")
        else:
            console.print(
                f"  {glyph}  [bold]{c.name:<24s}[/bold] [{msg_style}]{c.message}[/{msg_style}]"
            )
        if c.fix_hint and c.status in ("warn", "fail"):
            console.print(f"      [dim]→ {c.fix_hint}[/dim]")
