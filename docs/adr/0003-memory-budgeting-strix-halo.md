# ADR-0003: Memory budgeting на AMD Strix Halo (UMA через GTT)

- **Status:** accepted
- **Date:** 2026-05-19
- **Authors:** AGmind core team
- **Related:** ADR-0001 (migration), ADR-0002 (compute abstraction),
  `R10-strix-halo-bios-uma.md`, `docs/HARDWARE.md`

## Контекст

AMD Strix Halo (Ryzen AI Max+ 395) — APU с unified LPDDR5X memory (до 128
GB). В отличие от discrete GPU где VRAM = физический pool, на Strix Halo
есть два concept'а:

1. **BIOS UMA frame buffer** — карвоут RAM зарезервированный как "VRAM"
   при boot
2. **GTT (Graphics Translation Table)** — динамически allocable system
   RAM маппированная в GPU virtual address space через `ttm.pages_limit`

Legacy AGmind использовал GB10 budget logic (121 GiB pool, fixed unified
memory pool). На Strix Halo нужно решить:

- Какой source of truth для "available compute memory"?
- Как docker compose `mem_limit` соотносится с GTT vs BIOS UMA?
- Что значит "model X занимает Y GB" в этой архитектуре?

## Рассмотренные варианты

### A. Использовать `mem_info_vram_total` (BIOS UMA frame buffer)

- **Pro:** Простой 1:1 с VRAM concept'ом discrete GPU.
- **Con:** На Linux BIOS UMA должен быть **минимум** (512 MB) per AMD
  ROCm system optimization docs. Реальный pool это GTT.
- **Cap:** Если user забыл BIOS, default 512 MB — кажется "GPU has 0.5
  GB", хотя реально 117 GiB доступно.

### B. Использовать `mem_info_gtt_total` (GTT)

- **Pro:** Реальный compute pool. Изменяется через kernel cmdline
  (`ttm.pages_limit`), без BIOS reboot.
- **Pro:** R10 recon подтвердил: AMD рекомендует "BIOS UMA = minimum,
  GTT = real budget" per Strix Halo system optimization guide.
- **Con:** Незнакомая концепция для пользователей с NVIDIA experience.

### C. Использовать system RAM total (ignore GPU specifics)

- **Pro:** Прозрачно — на UMA "system RAM = GPU RAM".
- **Con:** Не учитывает CPU headroom (нужно ~10% RAM для kernel/userspace).
- **Con:** На non-UMA systems (CPU-only mode) ничего не меняет.

## Решение

**Выбран вариант B: GTT total как primary memory budget на Strix Halo.**

Detection logic в `agmind.compute.detect`:

```python
gpu = find_amd_apu_card()
total_memory_bytes = gpu.gtt_total_bytes  # mem_info_gtt_total
# Fallback: system_ram_bytes если GPU не detected (CPU-only)
```

Warning thresholds:
- **BIOS UMA > 2 GiB на Linux** → warning ("set to 512 MB minimum")
- **GTT < 70% RAM** → warning ("add ttm.pages_limit=<N> to GRUB cmdline")

## Последствия

### Положительные

- Корректное budgeting для Strix Halo (R10 verified).
- `agmind doctor` дает actionable fix (GRUB cmdline для tier-matching GTT).
- Tier auto-detection (`agmind.models.detect_tier`) использует system RAM
  как proxy для max possible GTT (94% of RAM threshold).
- Один API (`DeviceInfo.total_memory_bytes`) для всех backends — CPU
  fallback использует system RAM напрямую.

### Отрицательные

- Нужно user education ("BIOS UMA маленький, не большой") — counter-
  intuitive для NVIDIA-experienced.
- Mitigation: `docs/HARDWARE.md::BIOS settings` + `agmind doctor` warning.

### Что нужно сделать

- [x] Implementation в `agmind/compute/detect.py::detect_host`
- [x] Warnings в `agmind/diagnostics/doctor.py::_check_bios_uma`,
  `_check_gtt_pool`
- [x] `docs/HARDWARE.md` instructions для GRUB cmdline
- [x] Tier matrix tied к RAM (не BIOS UMA)
- [ ] Future: tooling для runtime `ttm.pages_limit` через `amd-debug-tools::amd-ttm` (M2)

## Бенчмарки

- Per R10: на 128 GB RAM, `ttm.pages_limit=30788044` (94%) = ~117 GiB GTT
- На текущем dev хосте: 125 GiB RAM, GTT=62.5 GiB (sub-optimal — user
  не настроил GRUB; warning срабатывает)
- Per R3 benchmarks: gpt-oss-120b MXFP4_MOE (62.8 GB) работает на 96-117
  GiB GTT, FAILS на 62 GiB GTT

## Откат

Если эта стратегия окажется fundamentally неверной (например, AMD меняет
GTT semantics) — fallback на vendor-recommended approach + новый ADR.
Frozen API: `DeviceInfo.total_memory_bytes` — semantic не менять,
изменять источник.

## Ссылки

- R10-strix-halo-bios-uma.md
- AMD ROCm docs: RDNA3.5 system optimization
- ROCm/issues/5444 (kernel < 6.17 → only 15.5 GiB visible)
- `agmind/compute/detect.py`
- `agmind/diagnostics/doctor.py::_check_{bios_uma,gtt_pool}`
