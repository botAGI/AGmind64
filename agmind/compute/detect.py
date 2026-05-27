"""Hardware detection: GPU vendor, Strix Halo, Vulkan/ROCm capabilities.

Reads /sys/class/drm/, runs `vulkaninfo --summary` и `rocminfo` если они
есть в PATH. Все subprocess вызовы — с timeout, errors не валят детектор
(возвращают partial info).

См. R10-strix-halo-bios-uma.md и R2-vulkan-radv-vs-amdvlk.md.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Final

from agmind.core.logging import logger

log = logger(__name__)

GIB: Final[int] = 1024**3
_STRIX_HALO_PCI_IDS: Final = frozenset({0x1586, 0x150E})  # gfx1151 variants
AMDVLK_ICD_FILES: Final = (
    "/etc/vulkan/icd.d/amd_icd64.json",
    "/etc/vulkan/icd.d/amd_icd32.json",
    "/etc/vulkan/implicit_layer.d/amd_icd64.json",
    "/etc/vulkan/implicit_layer.d/amd_icd32.json",
)


@dataclass(frozen=True)
class GPUInfo:
    """Detected AMD GPU info."""

    pci_id: int  # device id (e.g. 0x1586 for gfx1151)
    vendor: str  # "amd" / "nvidia" / "intel" / "unknown"
    name: str  # "Radeon 8060S" / "Strix Halo iGPU"
    is_strix_halo: bool
    bios_uma_bytes: int  # mem_info_vram_total (BIOS frame buffer)
    gtt_total_bytes: int  # mem_info_gtt_total (effective compute pool)
    card_path: str  # /sys/class/drm/cardN


@dataclass(frozen=True)
class VulkanInfo:
    """Probed vulkaninfo state."""

    available: bool
    driver_name: str  # "radv" / "amdvlk" / "anv" / "" if missing
    driver_id: str  # DRIVER_ID_MESA_RADV etc
    mesa_version: tuple[int, int, int] | None
    device_name: str
    api_version: tuple[int, int, int] | None
    has_cooperative_matrix: bool
    has_external_memory_host: bool
    amdvlk_files_present: tuple[str, ...]  # if non-empty → AMDVLK leaked into install


@dataclass(frozen=True)
class ROCmInfo:
    """Probed rocminfo state."""

    available: bool
    gfx_targets: tuple[str, ...]
    rocm_version: str  # "7.2.0" or "" if unknown


@dataclass(frozen=True)
class HostInfo:
    """Composite hardware snapshot."""

    cpu_model: str
    cpu_cores: int
    system_ram_bytes: int
    kernel_version: str
    gpu: GPUInfo | None
    vulkan: VulkanInfo
    rocm: ROCmInfo
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _read_int(path: str) -> int | None:
    """Read int from sysfs file. Supports hex (0x...) and decimal."""
    try:
        raw = Path(path).read_text().strip()
        # int(s, 0) autodetects base from prefix: 0x → 16, 0o → 8, 0b → 2,
        # иначе decimal.
        return int(raw, 0)
    except (OSError, ValueError):
        return None


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    if not cmd or not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.debug("%s failed: %s", cmd, exc)
        return ""


def _detect_cpu() -> tuple[str, int]:
    cpuinfo = _read_text("/proc/cpuinfo")
    model = ""
    for line in cpuinfo.splitlines():
        if line.startswith("model name"):
            model = line.split(":", 1)[1].strip()
            break
    cores = max(os.cpu_count() or 1, 1)
    return model, cores


def _detect_system_ram() -> int:
    meminfo = _read_text("/proc/meminfo")
    for line in meminfo.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1]) * 1024  # kB → bytes
                except ValueError:
                    pass
    return 0


def _detect_kernel() -> str:
    return _run(["uname", "-r"], timeout=1.0).strip()


def _find_amd_card() -> Path | None:
    """Find /sys/class/drm/cardN/ for AMD APU (Strix Halo specifically)."""
    drm_root = Path("/sys/class/drm")
    if not drm_root.exists():
        return None
    candidates: list[tuple[int, Path]] = []
    for card in sorted(drm_root.glob("card[0-9]*")):
        vendor = _read_int(str(card / "device/vendor"))
        if vendor != 0x1002:  # AMD
            continue
        dev_id = _read_int(str(card / "device/device"))
        if dev_id is None:
            continue
        # Score: strix halo first, then larger GTT, then card index
        is_strix = dev_id in _STRIX_HALO_PCI_IDS
        gtt = _read_int(str(card / "device/mem_info_gtt_total")) or 0
        score = (1 if is_strix else 0) * 10**12 + gtt
        candidates.append((score, card))
    if not candidates:
        return None
    return max(candidates, key=lambda x: x[0])[1]


def detect_gpu() -> GPUInfo | None:
    card = _find_amd_card()
    if card is None:
        return None
    pci_id = _read_int(str(card / "device/device")) or 0
    bios_uma = _read_int(str(card / "device/mem_info_vram_total")) or 0
    gtt_total = _read_int(str(card / "device/mem_info_gtt_total")) or 0
    is_strix = pci_id in _STRIX_HALO_PCI_IDS

    # Human-readable name. /sys не даёт device name, используем pci_id + класс.
    name = f"AMD Radeon Graphics (PCI 1002:{pci_id:04x})"
    if is_strix:
        name = "AMD Radeon 8060S (Strix Halo, gfx1151)"

    return GPUInfo(
        pci_id=pci_id,
        vendor="amd",
        name=name,
        is_strix_halo=is_strix,
        bios_uma_bytes=bios_uma,
        gtt_total_bytes=gtt_total,
        card_path=str(card),
    )


def _parse_vulkan_summary(text: str) -> dict[str, str]:
    """Parse `vulkaninfo --summary` Devices section.

    Возвращает поля предпочитаемого GPU. На Strix Halo `vulkaninfo` показывает
    минимум два device'а — реальный AMD RADV (GPU0, deviceType=INTEGRATED_GPU)
    и software fallback llvmpipe (GPU1, deviceType=CPU). Старая версия читала
    все строки в один dict, и driverName=llvmpipe (последний) перекрывал radv.

    Стратегия выбора:
      1. INTEGRATED_GPU или DISCRETE_GPU предпочтительнее CPU/OTHER.
      2. Среди равных — radv предпочтительнее всех других AMD driver'ов
         (см. R2-vulkan-radv-vs-amdvlk recon).
      3. Иначе — первый device по порядку.
    """
    # Split на блоки по `GPUn:` заголовкам.
    devices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_devices_section = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("Devices:"):
            in_devices_section = True
            continue
        if not in_devices_section:
            continue
        if re.match(r"^GPU\d+:\s*$", line):
            if current is not None:
                devices.append(current)
            current = {}
            continue
        if current is None:
            continue
        m = re.match(r"\s+(\w+)\s*=\s*(.+?)\s*$", line)
        if m:
            current[m.group(1)] = m.group(2)
    if current is not None:
        devices.append(current)

    if not devices:
        return {}

    def _device_priority(dev: dict[str, str]) -> tuple[int, int]:
        dtype = dev.get("deviceType", "")
        # Higher = better. Hardware > CPU/llvmpipe.
        if "DISCRETE_GPU" in dtype:
            type_score = 3
        elif "INTEGRATED_GPU" in dtype:
            type_score = 2
        elif "VIRTUAL_GPU" in dtype:
            type_score = 1
        else:
            type_score = 0  # CPU / OTHER / llvmpipe
        # Driver preference: radv > amdvlk > others.
        drv = dev.get("driverName", "")
        if drv == "radv":
            drv_score = 2
        elif drv == "amdvlk":
            drv_score = 1
        else:
            drv_score = 0
        return (type_score, drv_score)

    devices.sort(key=_device_priority, reverse=True)
    return devices[0]


def detect_vulkan() -> VulkanInfo:
    amdvlk_leaked = tuple(f for f in AMDVLK_ICD_FILES if Path(f).exists())

    if not shutil.which("vulkaninfo"):
        return VulkanInfo(
            available=False,
            driver_name="",
            driver_id="",
            mesa_version=None,
            device_name="",
            api_version=None,
            has_cooperative_matrix=False,
            has_external_memory_host=False,
            amdvlk_files_present=amdvlk_leaked,
        )

    env = os.environ.copy()
    env.setdefault("AMD_VULKAN_ICD", "RADV")

    summary = _run(["vulkaninfo", "--summary"], timeout=10.0)
    if not summary:
        return VulkanInfo(
            available=True,
            driver_name="",
            driver_id="",
            mesa_version=None,
            device_name="",
            api_version=None,
            has_cooperative_matrix=False,
            has_external_memory_host=False,
            amdvlk_files_present=amdvlk_leaked,
        )

    fields = _parse_vulkan_summary(summary)
    drv_name = fields.get("driverName", "")
    drv_id = fields.get("driverID", "")
    dev_name = fields.get("deviceName", "")
    drv_info = fields.get("driverInfo", "")

    mesa_ver: tuple[int, int, int] | None = None
    mm = re.search(r"Mesa\s+(\d+)\.(\d+)\.(\d+)", drv_info)
    if mm:
        mesa_ver = (int(mm[1]), int(mm[2]), int(mm[3]))

    api_ver: tuple[int, int, int] | None = None
    api = fields.get("apiVersion", "")
    am = re.match(r"(\d+)\.(\d+)\.(\d+)", api)
    if am:
        api_ver = (int(am[1]), int(am[2]), int(am[3]))

    full = _run(["vulkaninfo"], timeout=15.0)
    has_coop = "VK_KHR_cooperative_matrix" in full
    has_extmem = "VK_EXT_external_memory_host" in full

    return VulkanInfo(
        available=True,
        driver_name=drv_name,
        driver_id=drv_id,
        mesa_version=mesa_ver,
        device_name=dev_name,
        api_version=api_ver,
        has_cooperative_matrix=has_coop,
        has_external_memory_host=has_extmem,
        amdvlk_files_present=amdvlk_leaked,
    )


def detect_rocm() -> ROCmInfo:
    if not shutil.which("rocminfo"):
        return ROCmInfo(available=False, gfx_targets=(), rocm_version="")

    out = _run(["rocminfo"], timeout=15.0)
    gfx_targets = tuple(sorted(set(re.findall(r"gfx\d{3,4}\w?", out))))

    rocm_ver = ""
    if shutil.which("rocm-smi"):
        smi_out = _run(["rocm-smi", "--showdriverversion"], timeout=5.0)
        vm = re.search(r"(\d+\.\d+\.\d+)", smi_out)
        if vm:
            rocm_ver = vm.group(1)

    return ROCmInfo(
        available=True,
        gfx_targets=gfx_targets,
        rocm_version=rocm_ver,
    )


def detect_host() -> HostInfo:
    """Полный snapshot текущей машины. Тяжёлые subprocess'ы — с timeouts."""
    cpu_model, cpu_cores = _detect_cpu()
    sys_ram = _detect_system_ram()
    kernel = _detect_kernel()
    gpu = detect_gpu()
    vulkan = detect_vulkan()
    rocm = detect_rocm()

    warnings: list[str] = []
    if vulkan.amdvlk_files_present:
        warnings.append(
            "AMDVLK ICD files detected — RADV may be silently overridden. "
            f"Remove: {', '.join(vulkan.amdvlk_files_present)}"
        )
    if vulkan.available and vulkan.driver_name and vulkan.driver_name != "radv":
        warnings.append(
            f"Active Vulkan driver is {vulkan.driver_name!r}, expected 'radv'. "
            "AMDVLK is discontinued (2025-09-15) and has a 2 GiB allocation cap."
        )
    if vulkan.available and vulkan.mesa_version is not None and vulkan.mesa_version < (25, 2, 8):
        warnings.append(
            f"Mesa {'.'.join(map(str, vulkan.mesa_version))} below recommended 25.2.8. "
            "Consider `ppa:kisak/kisak-mesa` for 26.0+."
        )
    if gpu is not None and gpu.is_strix_halo:
        if gpu.bios_uma_bytes > 2 * GIB:
            warnings.append(
                f"BIOS UMA frame buffer = {gpu.bios_uma_bytes / GIB:.1f} GiB. "
                "On Linux set to 512 MB minimum and use ttm.pages_limit instead "
                "(see docs/HARDWARE.md)."
            )
        # Sub-optimal GTT (рекомендация R10: ~95% of system RAM для max iGPU)
        if sys_ram > 0 and gpu.gtt_total_bytes < int(sys_ram * 0.7):
            recommended_pages = int(sys_ram * 0.94 / 4096)
            warnings.append(
                f"GTT pool = {gpu.gtt_total_bytes / GIB:.1f} GiB on {sys_ram / GIB:.0f} GiB "
                f"system. Sub-optimal — recommended ~{sys_ram * 0.94 / GIB:.0f} GiB. "
                f"Add to GRUB cmdline: ttm.pages_limit={recommended_pages} "
                "(см. docs/HARDWARE.md)."
            )
        # Kernel version warning
        km = re.match(r"(\d+)\.(\d+)\.(\d+)", kernel)
        if km:
            kv = (int(km[1]), int(km[2]), int(km[3]))
            if kv < (6, 18, 4):
                # Specifically 6.17.0-19 HWE и 6.18.4+ mainline считаются OK.
                # 6.17.0-29 — кажется выше чем 6.17.0-19, проверим формат.
                # `6.17.0-29` parses как (6,17,0) — patch и сборка not parsed.
                # Конкретный HWE check невозможен без full Ubuntu version.
                warnings.append(
                    f"Kernel {kernel} may be below recommended 6.18.4 mainline. "
                    "ROCm may see only ~15.5 GiB VRAM на старых ядрах "
                    "(ROCm/issues/5444). Verify via "
                    "`cat /sys/class/drm/card*/device/mem_info_vram_total`."
                )

    return HostInfo(
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        system_ram_bytes=sys_ram,
        kernel_version=kernel,
        gpu=gpu,
        vulkan=vulkan,
        rocm=rocm,
        warnings=tuple(warnings),
    )


def to_json(info: HostInfo) -> str:
    """Сериализовать HostInfo в JSON (для `agmind doctor --json`)."""

    def asdict(obj: object) -> object:
        if is_dataclass(obj):
            return {f.name: asdict(getattr(obj, f.name)) for f in fields(obj)}
        if isinstance(obj, tuple):
            return [asdict(x) for x in obj]
        if isinstance(obj, list):
            return [asdict(x) for x in obj]
        if isinstance(obj, dict):
            return {k: asdict(v) for k, v in obj.items()}
        return obj

    return json.dumps(asdict(info), indent=2, ensure_ascii=False)
