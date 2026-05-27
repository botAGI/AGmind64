"""Backend registry + auto-select.

See ADR-0008 for the entry-point plugin boundary.

Phase H'.E: backends discoverable через setuptools entry_points group
`agmind.backends`. Third-party плагины делают `pip install agmind-X-backend`,
их класс автоматически попадает в registry без правки core.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Final

from agmind.compute.base import Backend
from agmind.compute.config import ComputeConfig, Profile, read_config
from agmind.core.logging import logger

log = logger(__name__)

# Backend priority for auto-select. Третьесторонние плагины (не в этом списке)
# попадают в registry, но не участвуют в auto-select — explicit AGMIND_BACKEND=X.
_BACKEND_PRIORITY: Final[tuple[str, ...]] = ("vulkan", "rocm", "cpu", "npu")


def _load_backends() -> dict[str, type[Backend]]:
    """Discover backends через entry_points group 'agmind.backends'.

    Built-in (`cpu`, `vulkan`, `rocm`, `npu`) объявлены в pyproject.toml.
    Third-party плагины автоматически попадут через `pip install`.

    Lazy import — ошибки конкретного backend (no llama-cpp, no vulkaninfo)
    игнорируются (graceful degrade на runtime в `.available()`).
    """
    out: dict[str, type[Backend]] = {}
    eps = entry_points(group="agmind.backends")

    for ep in eps:
        try:
            out[ep.name] = ep.load()
        except Exception as exc:  # noqa: BLE001 — broad catch для plugin robustness
            log.warning("backend %s load failed: %s", ep.name, exc)

    # Safety net: если entry_points не дискаверили (e.g. agmind не установлен
    # как пакет — только PYTHONPATH), импортируем core backends напрямую.
    # Гарантирует что CPU всегда доступен — критический invariant I.1.
    if "cpu" not in out:
        from agmind.compute.backends.cpu import CPUBackend

        out["cpu"] = CPUBackend

    return out


def discover_backend_names() -> list[str]:
    """Public API: список всех зарегистрированных backends (включая third-party).

    Используется `agmind status` CLI command для отображения plugins.
    """
    return sorted(_load_backends().keys())


def list_available_backends() -> list[str]:
    """Return names of backends whose `available()` returns True."""
    backends = _load_backends()
    return [name for name in _BACKEND_PRIORITY if name in backends and backends[name].available()]


def _select_auto(config: ComputeConfig, available: list[str]) -> str:
    """Auto-select backend per §1.2.6 decision matrix.

    Args:
        config: parsed AGMIND_* env vars.
        available: list of available backend names.

    Returns:
        Selected backend name.
    """
    if not available:
        raise RuntimeError(
            "No compute backends available — even CPU. "
            "This shouldn't happen unless agmind package is corrupt."
        )

    profile = config.profile

    # Embed batch → ROCm (Infinity M2) preferable. Если ROCm недоступен — Vulkan.
    if profile == Profile.EMBED_BATCH:
        for name in ("rocm", "vulkan", "cpu"):
            if name in available:
                return name

    # PP-bound → ROCm (rocWMMA). Иначе Vulkan.
    if profile == Profile.PP:
        for name in ("rocm", "vulkan", "cpu"):
            if name in available:
                return name

    # TG / MIXED / EMBED_SINGLE → Vulkan default, fallback ROCm, fallback CPU.
    for name in ("vulkan", "rocm", "cpu"):
        if name in available:
            return name

    # Should not reach
    return available[0]


def get_backend(profile: Profile | str | None = None) -> Backend:
    """Resolve backend from env config + heuristic.

    Args:
        profile: override AGMIND_BACKEND_PROFILE (для caller'а который
            знает свой workload — e.g. CLI embed command passes EMBED_BATCH).

    Returns:
        Initialized Backend instance.

    Raises:
        RuntimeError: no backends available.
        ValueError: AGMIND_BACKEND / AGMIND_ENGINE invalid.
    """
    config = read_config()
    if profile is not None:
        if isinstance(profile, str):
            profile = Profile(profile)
        config = ComputeConfig(
            backend=config.backend,
            engine=config.engine,
            device_id=config.device_id,
            profile=profile,
        )

    backends = _load_backends()
    available = list_available_backends()

    if config.backend == "auto":
        chosen = _select_auto(config, available)
    else:
        if config.backend not in backends:
            raise RuntimeError(f"AGMIND_BACKEND={config.backend!r} not registered")
        if not backends[config.backend].available():
            raise RuntimeError(
                f"AGMIND_BACKEND={config.backend!r} requested but not available "
                f"on this host. Available: {available}"
            )
        chosen = config.backend

    log.info(
        "compute: backend=%s engine=%s profile=%s", chosen, config.engine, config.profile.value
    )
    return backends[chosen].make(engine=config.engine)
