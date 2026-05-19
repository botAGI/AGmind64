# AGmind Migration Plan
## GB10 (aarch64+CUDA) → x86-64 / AMD Strix Halo

> Этот документ — детальный план миграции. Создаётся в фазе A
> «Inventory & Plan» по требованию `AGMIND_MIGRATION_SPEC.md` §2.2.
> **Статус:** DRAFT — ожидает апрува пользователя.
> **Без апрува фаза B не начинается** (DoD фазы A).
>
> **Версия:** 1.0-draft (2026-05-19, ночная сессия)
> **Спека:** `AGMIND_MIGRATION_SPEC.md` (единственный источник правды)
> **Аудит baseline:** `.planning/research/x86-migration/baseline-audit.json`
> (1811 находок в 10759 файлах)

---

## 0. Контекст и решения

### 0.1 Что мигрируем

Существующий проект: **AGmind v3.2.0** — Bash + Docker Compose installer
для DGX Spark (GB10, aarch64). 50 сервисов, ~30k LOC Bash, 13 ADR,
строгий GSD workflow.

Цель: **новая Python-кодовая база** на AMD Strix Halo (Ryzen AI Max+ 395
+ Radeon 8060S, gfx1151, RDNA 3.5, 128 GB unified LPDDR5X). Compute через
runtime-абстракцию (Vulkan/ROCm/CPU/NPU-stub) per спеке Part 1.4.

### 0.2 Зафиксированные решения

- **Полный rewrite в Python** (не Bash retrofit) — подтверждено
  пользователем 2026-05-18.
- **Fresh git** — `rm -rf .git && git init`, baseline commit как
  «initial: fork of AGmind v3.2.0 baseline» — подтверждено.
- **Никогда не удалять старый код** — только `git mv` в `legacy/gb10/`
  (спека Part 1.5 #1).
- **`AGMIND_MIGRATION_SPEC.md` — источник правды**, читать в начале
  каждой сессии.

### 0.3 Открытые архитектурные вопросы (требуют апрува)

| ID | Вопрос | Влияние на план |
|----|--------|------------------|
| **OQ-1** | Сохраняем ли стек Dify+Weaviate+RAGFlow или минимализм? | определяет ~40% scope фаз E-F |
| **OQ-2** | Entrypoint: CLI / REST API / library / все три? | определяет `agmind/cli/` vs `agmind/api/` |
| **OQ-3** | Installer как Python entrypoint (`pip install + agmind install`) или только inference? | определяет `agmind/install/` |
| **OQ-4** | UI для wizard: questionary / rich / textual / flags-only? | мелкая, default questionary |
| **OQ-5** | Multi-node x86 cluster (новое требование) — Swarm/k3s/Ansible/SSH? | P2/P3, не критично для M1 |
| **OQ-6** | mDNS .local URLs vs обычный DNS + Caddy с auto-HTTPS? | мелкая, default mDNS |
| **OQ-7** | Применить ли расширенные правила R1-R12 (Karpathy-style) к спеке? | определяет executable-DoD, frozen-files, etc. |

**Рекомендация:** OQ-1 решить как «opt-in стек через `agmind deploy <stack>`,
ядро minimal». OQ-2 — CLI default, optional REST API. OQ-3 — да, Python
entrypoint для installer. OQ-7 — да, применить (см. §6).

---

## 1. Inventory

Полный audit baseline в `.planning/research/x86-migration/baseline-audit.json`.
Группировки в `A3-findings-analysis.md`.

### 1.1 Кратко

- **Файлов проверено:** 10,759
- **Всего находок:** 1,811
- **Покрытие правил:** 7/7 (cuda_runtime, cuda_python, cuda_paths,
  arm_aarch64, nvidia_hw, cuda_arch_flags, native_march)
- **Файлов с находками:** ~150-200

### 1.2 По правилам

| Rule | Findings | % |
|------|---------:|--:|
| `arm_aarch64` | 1,041 | 57% |
| `nvidia_hw` | 681 | 38% |
| `cuda_python` | 39 | 2% |
| `cuda_arch_flags` | 22 | 1% |
| `cuda_paths` | 15 | <1% |
| `cuda_runtime` | 7 | <1% |
| `native_march` | 6 | <1% |

**Хорошая новость:** `cuda_runtime` всего 7 — прямых CUDA C API нет,
только Python torch.cuda через `cuda_python`. C++/CUDA-модулей которые
нужно компилировать — нет.

### 1.3 По директориям

| Dir | Findings | Природа | Куда уходит |
|-----|---------:|---------|--------------|
| `.planning/` | 1,080 | Historical planning | `legacy/gb10/.planning/` |
| `<root>` | 162 | README/CLAUDE/CHANGELOG/install.sh | разнесено в legacy/ + новые |
| `docs/` | 119 | 13 ADR + matrices | `legacy/gb10/docs/` (новые ADR-0001/0002 в новом `docs/adr/`) |
| `lib/` | 110 | Bash modules (24 файла) | `legacy/gb10/lib/` |
| `documentation/` | 103 | Articles | `legacy/gb10/documentation/` |
| `scripts/` | 86 | Utility scripts | `legacy/gb10/scripts/` (кроме audit_forbidden.py) |
| `tests/` | 81 | Unit/integration/golden | `legacy/gb10/tests/` |
| `templates/` | 39 | docker-compose/env | `legacy/gb10/templates/` |
| `benchmarks/` | 11 | Spark/AEON-7 benchmarks | `legacy/gb10/benchmarks/` |
| `.github/` | 8 | CI workflows | заменяются по Part 5.4 спеки |
| Прочие | <10 | monitoring, dify-workflows, etc | в legacy/ блоками |

### 1.4 Код-файлы (.sh/.py) с находками

32 файла, 242 находки. Топ-10:
1. `lib/wizard.sh` — 35
2. `scripts/check-upstream.sh` — 27
3. `lib/doctor.sh` — 24
4. `scripts/generate-manifest.sh` — 21
5. `lib/detect.sh` — 14
6. `scripts/detect.sh` — 14
7. `tests/unit/test_versions_env_arm64_holds.sh` — 13
8. `scripts/audit_forbidden.py` — 13 *(self-reference, см. §7.3)*
9. `install.sh` — 12
10. `lib/i18n.sh` — 11

Полная таблица в `.planning/research/x86-migration/A3-findings-analysis.md`.

### 1.5 Config-файлы (.yml/.json/Dockerfile) с находками

15 файлов, 79 находок. Топ-5:
1. `templates/release-manifest.json` — 21
2. `templates/docker-compose.yml` — 14
3. `.github/workflows/test.yml` — 8
4. `templates/docker-compose.worker.yml` — 4
5. `Makefile` — 3

---

## 2. Hot path

Полный анализ в `.planning/research/x86-migration/A5-hot-path.md`.

### 2.1 Что воспроизводим в Python (HOT)

- **Compute layer** (новый, по спеке Part 1.4 + 5.3):
  `agmind/compute/{base.py, detect.py, config.py, __init__.py}`,
  `agmind/compute/backends/{cpu.py, vulkan.py, rocm.py, npu_stub.py}`.
- **HW detection** (`lib/detect.sh` → `agmind/compute/detect.py`):
  CPU info, GPU info через `rocminfo`/`vulkaninfo`/`lspci`,
  RAM (включая UMA frame buffer), архитектура, driver versions.
- **Логирование + env utils** (`lib/common.sh` → `agmind/log.py`, `agmind/_env.py`).
- **State store** (`lib/state.sh` → `agmind/state.py`) если installer
  переезжает в Python (OQ-3).
- **Phase engine** (`lib/phases.sh` → `agmind/install/phases.py`) — то же.
- **Doctor + health** (`lib/{doctor,health}.sh` → `agmind/diagnostics/`).
- **CLI** (`scripts/agmind.sh` → `agmind/cli/`).
- **Credentials** (`lib/creds.sh` → `agmind/secrets.py`).
- **Config builder** (`lib/config.sh` → `agmind/config/env.py`).
- **i18n** (`lib/i18n.sh` → `agmind/i18n/` через gettext или fluent).
- **Compose builder** (`lib/compose.sh` → `agmind/deploy/compose.py`) если
  стек сохраняется (OQ-1).
- **Service registry** (`lib/_registry.indexed.sh` → `agmind/services/registry.py`)
  если стек сохраняется (OQ-1).

### 2.2 Что выбрасываем без замены (COLD)

- `lib/peer.sh`, `lib/ssh_trust.sh`, `lib/cluster_mode.sh` — dual-Spark
  QSFP 200G NAT, Spark-specific.
- Driver 580 pin в `lib/security.sh` — GB10 UMA-специфика.
- FlashInfer FP8 workaround (TRITON_ATTN) — SM_121 specific.
- `tests/unit/test_versions_env_arm64_holds.sh` — arm64 manifest sentinel.
- `tests/compose/test_image_tags_exist.sh` — arm64 manifest verify.
- NVIDIA части `scripts/check-upstream.sh` (NGC drift).
- `lib/airgapped.sh`, `lib/bundle.sh` — offline transfer, P3 backlog.

### 2.3 ЗАВИСИТ ОТ OQ-1 (стек или минимализм)

- `lib/compose.sh`, `lib/service-map.sh`, `lib/wizard.sh`,
  `lib/backup.sh`, `lib/restore.sh`, `lib/models.sh`, `lib/openwebui.sh`,
  `lib/authelia.sh`.

---

## 3. Dependency graph

Полный граф в `.planning/research/x86-migration/A4-dependency-graph.md`.

### 3.1 Архитектура текущей Bash-кодовой базы

```
install.sh                              ← entrypoint #1 (sudo bash install.sh)
└── source 23 × lib/*.sh
    ├── lib/common.sh (utilities)      ← используется 14 модулями
    ├── lib/detect.sh (HW detect)      ← используется 4 модулями
    ├── lib/phases.sh (phase engine)
    ├── lib/wizard.sh (interactive)
    │   └── lib/cluster_mode.sh, lib/detect.sh, lib/tui.sh
    └── ... ещё ~20 модулей

scripts/agmind.sh                       ← entrypoint #2 (agmind CLI)
└── source 14 × scripts/*.sh           ← runtime copies of lib/*.sh
                                          (lib/_copy_runtime_files)
```

### 3.2 Implications для переезда

1. **Можно переезжать целыми директориями** `lib/`, `scripts/`,
   `install.sh` — внутренние пути через `${INSTALLER_DIR}/lib/*` остаются
   валидными после `git mv`.
2. **`templates/` тоже целиком** — `lib/_copy_runtime_files` и
   `lib/wizard.sh` ожидают конкретные пути.
3. **PR-разбивка фазы B** = по директориям, не по файлам.

---

## 4. Phasing — разбивка на PR-ы

Спека Part 2 определяет 7 фаз A→G. Этот раздел разбивает их на конкретные
PR-ы с явным scope.

### Фаза A — Inventory & Plan (этот документ) — DRAFT

**Outcome:** `docs/MIGRATION_PLAN.md` в репо + апрув пользователя.

**DoD:**
- `[x]` A1: `scripts/audit_forbidden.py` создан.
- `[x]` A2: `baseline-audit.json` собран.
- `[x]` A3-A5: группировка / dependency graph / hot path → файлы в
  `.planning/research/x86-migration/`.
- `[x]` A6: этот документ создан.
- `[ ]` A7: апрув пользователя получен.
- `[ ]` `git init` + initial commit (после апрува).

**Артефакты:**
- `scripts/audit_forbidden.py`
- `.planning/research/x86-migration/baseline-audit.json`
- `.planning/research/x86-migration/A3-findings-analysis.md`
- `.planning/research/x86-migration/A4-dependency-graph.md`
- `.planning/research/x86-migration/A5-hot-path.md`
- `.planning/research/x86-migration/R0-autonomous-workflow.md`
- `.planning/research/x86-migration/R-karpathy-method.md`
- `docs/MIGRATION_PLAN.md` (этот файл)
- `.planning/sessions/2026-05-19-overnight.md`

### Фаза B — Legacy quarantine (7 PR-ов)

**Outcome:** весь GB10/CUDA/aarch64 код в `legacy/gb10/`, основное
дерево — пустое (только `AGMIND_MIGRATION_SPEC.md`, `scripts/audit_forbidden.py`,
`LICENSE`, новый `CLAUDE.md`, новый `.gitignore`, новые ADR-0001/0002).

**DoD:**
- `make audit` → 0 находок в основном дереве (исключая
  `# audit: allow`-аннотированные строки в `audit_forbidden.py`).
- `legacy/gb10/README.md` объясняет: что лежит, почему deprecated, как
  откатить (до 2027-Q1).
- В начале каждого перенесённого файла — deprecation-комментарий
  (спека Part 1.5 #1).

**PR-разбивка:**

| PR | Scope | Удаляется находок |
|----|-------|------------------:|
| **PR-B1** | `git mv .planning/ → legacy/gb10/.planning/` + добавить EXCLUDED_DIR в audit | ~1080 |
| **PR-B2** | `git mv documentation/ → legacy/gb10/documentation/` | ~103 |
| **PR-B3** | `git mv pipelines/ plugins/ dify-workflows/ workflows/ → legacy/gb10/` | ~5 |
| **PR-B4** | `git mv monitoring/ benchmarks/ → legacy/gb10/` | ~15 |
| **PR-B5** | `git mv tests/ → legacy/gb10/tests/` | ~81 |
| **PR-B6** | `git mv templates/ → legacy/gb10/templates/` | ~39 |
| **PR-B7** | `git mv lib/ scripts/{<всё кроме audit_forbidden.py>} install.sh Makefile → legacy/gb10/` + переписать корневые README/CLAUDE/CHANGELOG/SECURITY/SPEC + удалить старые `.github/workflows/` | ~360 |

Каждый PR-B:
1. Запустить `make audit --json before-PRBN.json`
2. `git mv` бульком
3. Добавить deprecation header в перенесённые файлы (sed-скрипт)
4. Запустить `make audit --json after-PRBN.json`
5. Проверить delta — соответствует ожиданию.
6. Commit с сообщением `phase-B: move <category> to legacy/gb10/`.

### Фаза C — Compute abstraction skeleton (1 PR на компонент = 6-8 PR-ов)

**Outcome:** `agmind/compute/` ABC + CPU backend, contract tests
зелёные на CPU.

**DoD (из спеки):**
- `pytest tests/compute -m backend_cpu` green.
- `pytest tests/compute -m backend_any` green.
- `AGMIND_BACKEND=cpu python -c "from agmind.compute import get_backend; print(get_backend().device_info())"` works.

**PR-разбивка:**

| PR | Scope |
|----|-------|
| **PR-C0** | `pyproject.toml` + `Makefile` + `.gitignore` (Python skeleton) + initial CI workflow |
| **PR-C1** | `agmind/compute/base.py` (ABC по Part 5.3) |
| **PR-C2** | `agmind/compute/detect.py` (vulkaninfo/rocminfo/lspci wrappers) |
| **PR-C3** | `agmind/compute/config.py` (env var reader) |
| **PR-C4** | `agmind/compute/backends/cpu.py` (llama-cpp-python + onnxruntime CPU) |
| **PR-C5** | `agmind/compute/backends/npu_stub.py` (NotImplementedError) |
| **PR-C6** | `agmind/compute/__init__.py` (get_backend() с auto-select) |
| **PR-C7** | `tests/compute/test_contract.py` + `test_detect.py` (параметризованные по бэкенду) |

### Фаза D — Backend implementations (2 PR-а)

**Outcome:** Vulkan + ROCm backends реализованы, contract tests зелёные
на реальном Strix Halo.

**DoD:**
- Все три backends (cpu, vulkan, rocm) проходят contract tests.
- `device_info()` корректно отдаёт metadata на Strix Halo (gfx1151,
  Radeon 8060S, 40 CU).
- `docs/BENCHMARKS.md` имеет baseline-числа.

**PR-разбивка:**

| PR | Scope |
|----|-------|
| **PR-D1** | `agmind/compute/backends/vulkan.py` (llama-cpp-python GGML_VULKAN, AMD_VULKAN_ICD=RADV) + tests |
| **PR-D2** | `agmind/compute/backends/rocm.py` (torch+ROCm для embed, llama-cpp HIP для inference, HSA_OVERRIDE_GFX_VERSION=11.5.1) + tests |
| **PR-D3** | `docs/BENCHMARKS.md` baseline + update auto-select в `get_backend()` (`tg→vulkan`, `pp→rocm`) |

### Фаза E — Call-sites migration (5-8 PR-ов)

**Outcome:** все hot path reimplemented в Python (см. §2.1).

**DoD:**
- `make audit --fail` → exit 0.
- `pytest` зелёный полностью.
- Smoke-тест end-to-end: inference + embed + rerank на Strix Halo.

**PR-разбивка** (зависит от OQ-1/2/3):

| PR | Scope |
|----|-------|
| **PR-E1** | `agmind/log.py` + `agmind/_env.py` + `agmind/secrets.py` |
| **PR-E2** | `agmind/config/` (.env reader, placeholder substitution) |
| **PR-E3** | `agmind/diagnostics/` (doctor + health) |
| **PR-E4** | `agmind/cli/` (typer-based, commands: install, status, doctor, version) |
| **PR-E5** | `agmind/state.py` + `agmind/migrations.py` (если installer переезжает) |
| **PR-E6** | `agmind/i18n/` (gettext skeleton, EN + RU) |
| **PR-E7** | `agmind/deploy/compose.py` + `agmind/services/registry.py` (если OQ-1 = стек) |
| **PR-E8** | `agmind/install/phases.py` (фазированный installer, если OQ-3) |

### Фаза F — Docker & CI (3-4 PR-а)

**Outcome:** 4 Dockerfile собираются, CI green, self-hosted runner на
Strix Halo для nightly бенчей.

**DoD:**
- Все 4 Docker-builds (`base`, `cpu`, `vulkan`, `rocm`) green в CI.
- Self-hosted runner подключён, проходит manual `workflow_dispatch`.

**PR-разбивка:**

| PR | Scope |
|----|-------|
| **PR-F1** | `docker/Dockerfile.base` + `Dockerfile.cpu` (Part 5.6, 5.9) |
| **PR-F2** | `docker/Dockerfile.vulkan` (Part 5.7) |
| **PR-F3** | `docker/Dockerfile.rocm` (Part 5.8) |
| **PR-F4** | `.github/workflows/ci.yml` (Part 5.4) + `.pre-commit-config.yaml` (Part 5.5) + self-hosted runner config |

### Фаза G — Benchmarks & docs (2 PR-а)

**Outcome:** `docs/BENCHMARKS.md` с числами, README обновлён, ADR закрыты.

**DoD:**
- `benchmarks/` suite на pytest-benchmark.
- Числа на всех релевантных бэкендах в `docs/BENCHMARKS.md`.
- Сравнение со старыми GB10-числами (если есть).
- README отражает текущее состояние.

**PR-разбивка:**

| PR | Scope |
|----|-------|
| **PR-G1** | `benchmarks/` suite (LLM tg/pp, embeddings, memory) + запуск на Strix Halo |
| **PR-G2** | README + ADR закрытие + дата ревью в спеке |

---

## 5. Risks & mitigations

| Risk | Severity | Mitigation |
|------|---------:|------------|
| **vllm-rocm не поддерживает gfx1151** | HIGH | Recon R4 нужен до D2. Fallback: llama.cpp HIP. |
| **Docling без CUDA медленнее в N раз** | MEDIUM | Recon R7. Если ОK — CPU. Если нет — рассмотреть Docling ROCm fork или альтернативу (Marker, Unstructured). |
| **TEI ROCm experimental → unstable** | MEDIUM | Recon R5. Fallback на CPU TEI или llama.cpp embed. |
| **Strix Halo BIOS UMA frame buffer = 32GB по умолчанию, мало для 26B модели** | HIGH | Recon R10. В docs/INSTALL.md явная инструкция по BIOS-тюнингу до 96GB. |
| **AMD_VULKAN_ICD=RADV конфликтует с AMDVLK** | MEDIUM | В Dockerfile.vulkan явное удаление AMDVLK ICD (Part 5.7). |
| **HSA_OVERRIDE_GFX_VERSION=11.5.1 ломается с обновлением ROCm** | MEDIUM | Pin rocm/dev-ubuntu-24.04:7.0-complete по digest. Recon R1 раз в квартал. |
| **Полный rewrite Python займёт 2-3 месяца** | HIGH | Принят пользователем. План разбит на 30+ PR-ов для инкремента. |
| **Старый installer для отката должен работать** | LOW | legacy/gb10/ сохраняет полную копию, deprecation до 2027-Q1. |
| **Audit-скрипт сам себя ловит (self-reference)** | LOW | Добавить `# audit: allow` к RULES в `audit_forbidden.py` (§7.3). |
| **Контекст между сессиями теряется** | MEDIUM | Persistent state в `migration_progress.json` (R1 из R-karpathy), session-startup checklist (R2). |
| **LLM-агент "улучшает" tests/audit** | MEDIUM | Frozen files с SHA256 в `migration_progress.json` (R7). |
| **Scope creep между фазами** | MEDIUM | Out-of-scope → `progress.json::deferred[]` (R9). |

---

## 6. Предлагаемые расширения спеки (R1-R12)

Из ресерча Karpathy / Anthropic best-practices (`R-karpathy-method.md`).
**Требуют апрува** (OQ-7).

- **R1.** `migration_progress.json` как persistent state (JSON, не Markdown).
- **R2.** Session-startup checklist (pwd / git / progress.json / audit / smoke).
- **R3.** Dual-agent split: `init.md` (setup) + `worker.md` (incremental).
- **R4.** Каждая фаза = `Intent` + `DoD` (executable) + `Out-of-scope`.
- **R5.** Audit расширить проверкой `touched_files \ allowed_files = ∅`.
- **R6.** Test-as-DoD: `make dod-phase-N` returns 0 — фаза закрыта.
- **R7.** Frozen files с SHA256 (audit script, DoD scripts, spec).
- **R8.** One phase per session.
- **R9.** Out-of-scope → `progress.json::deferred[]`.
- **R10.** Hypothesis logging перед нетривиальной правкой.
- **R11.** Fixed determinism: digests, lock-files, `PYTHONHASHSEED`, TZ.
- **R12.** Overfit-one-batch: новый backend → один сервис end-to-end.

Если апрувятся — добавляются как новый раздел в `AGMIND_MIGRATION_SPEC.md`
(PR с обоснованием, спека Part 6 финал).

---

## 7. Rollback strategy

### 7.1 Per phase

| Phase | Rollback action |
|-------|------------------|
| A | `rm docs/MIGRATION_PLAN.md scripts/audit_forbidden.py` — ничего не сломано. |
| B | `git revert <PR-B*>` — все `git mv` обратимы, deprecation-комментарии тоже. |
| C-E | `git revert <PR>` — каждый PR независим. |
| F | `git revert <PR-F*>` — Docker и CI откатываются без блока. |
| G | `git revert <PR-G*>` — документация. |

### 7.2 Кросс-фазовый

Если миграция в целом не зашла — `cd legacy/gb10 && bash install.sh`
работает как раньше (до 2027-Q1). Это **главный safety net**.

### 7.3 Audit-script self-reference

`scripts/audit_forbidden.py` сам себя ловит на 13 находок (RULES
содержат паттерны как строки). Решение — две опции:

**A. `# audit: allow` к каждой re.compile-строке** (clean, по спеке):
```python
re.compile(
    r"\b(cudaMalloc|cudaMemcpy|...)\b"  # audit: allow rule-self-reference
),
```

**B. Special-case `AGMIND_MIGRATION_SPEC.md` и `scripts/audit_forbidden.py`
в EXCLUDED_DIRS / специальном whitelist** — короче, но hack.

**Рекомендую A** — соответствует спеке Part 1.3 «Опт-аут для конкретной
строки: комментарий # audit: allow (только с обоснованием рядом)».

Применить в PR-A8 (мини-фикс после апрува плана).

---

## 8. Ресерчи перед D-E

| ID | Тема | Перед фазой | Статус |
|----|------|-------------|--------|
| **R0** | Claude Code autonomous workflow | A (overnight) | ✅ done |
| **R-karpathy** | Метод Карпатого / agentic engineering | A (overnight) | ✅ done |
| **R1** | PyTorch ROCm wheels + onnxruntime + Docker AMD | A (overnight) | ✅ done |
| **R2** | Vulkan RADV vs AMDVLK на Strix Halo | A (overnight) | ✅ done |
| **R3** | llama.cpp Vulkan + HIP build на gfx1151 | A (overnight) | ✅ done |
| **R4** | vLLM ROCm / SGLang / MLC-LLM matrix | A (overnight) | ✅ done |
| **R5** | HF TEI + embed/rerank engines | A (overnight) | ✅ done |
| **R7** | Docling без CUDA альтернативы | E | in progress |
| **R10** | Strix Halo BIOS UMA frame buffer | A (overnight) | ✅ done |
| **R11** | Альтернативы RAGFlow без CUDA | A→B | in progress |
| R6 | Ollama / LM Studio detail | C | deferred (covered in R3/R4) |
| R8 | MLC-LLM Vulkan на gfx1151 | D | deferred (covered in R4: no bench) |
| R9 | Docker GPU passthrough для AMD | F | deferred (covered in R1) |
| R12 | XDNA 2 NPU статус | C (для npu_stub) | deferred (RyzenAI-SW#366 confirmed) |

## 12. Proposed spec updates (новый раздел, требует апрува OQ-7+)

После recons R1-R5 + R10 накоплены конкретные расхождения с текущей
спекой. Все changes — **proposed**, требуют апрува пользователя.

### 12.1 `AGMIND_MIGRATION_SPEC.md` Part 1.2 — Compute backends

**Текущая формулировка:** «ROCm/HIP 7.x — опциональный, для prompt-heavy
и batch ... `rocm/dev-ubuntu-24.04:7.0-complete`».

**Предлагается:**
- Минимальная версия ROCm = **7.2.x**, не 7.0 (ROCm 7.0.2 crashes на
  gfx1151 — ROCm/issues/5534).
- Base image для Dockerfile.rocm: `rocm/dev-ubuntu-24.04:7.2-runtime`
  (не `:7.0-complete`; runtime образ ≤10 GB vs 20-30 GB complete).
- Минимальный kernel: **6.18.4 mainline / 6.17.0-19 HWE** (R10).
- Minimum Mesa для Vulkan: **25.2.8** (R2).
- Minimum llama-cpp-python: **0.3.23** (PyPI 2026-05-11).
- Minimum llama.cpp upstream: **b8765** (PR #19625 Wave32 FA + #20551
  graphics queue).
- linux-firmware: **20260110+** (R3).

### 12.2 Part 1.3 — Hard rules update

Добавить новые запреты на gfx1151 (R3, R4, R5):
```
| **Broken на gfx1151** | TEI ROCm direct (PR #860 stalled); SGLang; MLC-LLM
прод; TGI; Ollama as production engine (vendored stale 56%); FP8 anything;
GPTQ/Marlin quants; MXFP4; AITER+MoE на RDNA; fastembed-gpu;
onnxruntime-rocm prod; bitsandbytes 4/8-bit; stock PyPI torch wheels |
| **Crashes** | `PYTORCH_HIP_ALLOC_CONF=backend:malloc`; AMDVLK ICD
(`amd_icd64.json`); `HSA_OVERRIDE_GFX_VERSION=11.5.1` с native gfx1151
wheels (gives subtle bugs) |
```

### 12.3 Part 1.3 — Разрешено / предпочтительно (новые добавления)

```
- llama-cpp-python собранный с GGML_VULKAN=ON (PRIMARY backend gfx1151)
- llama-cpp-python собранный с GGML_HIP=ON (secondary backend gfx1151 для
  GDN-моделей, batch ≥4, long context pp-bound)
- `llama-server` (тот же binary) с --embeddings/--pooling cls для embed
- `llama-server` с --reranking для rerank
- vLLM community fork с патчами kyuz0/hec-ovi (M2 upgrade для tool calling)
- Infinity ROCm (M2 upgrade для production embed/rerank batching)
- PyTorch ROCm через AMD nightly index gfx1151 ИЛИ repo.radeon.com stable
  (НЕ stock PyPI/pytorch.org wheels)
```

### 12.4 Part 5.7 (Dockerfile.vulkan)

- Explicit RM AMDVLK ICD files (`/etc/vulkan/icd.d/amd_icd64.json`,
  implicit_layer.d/) на образ-этапе.
- Mesa 26+ через `ppa:kisak/kisak-mesa`.
- `VK_DRIVER_FILES` env (надёжнее `AMD_VULKAN_ICD`).
- Healthcheck Python пробник (см. R2).

### 12.5 Part 5.8 (Dockerfile.rocm)

- Base image: `rocm/dev-ubuntu-24.04:7.2-runtime` (не `:7.0-complete`).
- PyTorch install: `pip install --index-url https://rocm.nightlies.amd.com/v2/gfx1151/ --pre torch torchaudio torchvision` (не
  `--index-url https://download.pytorch.org/whl/rocm6.3`).
- Env vars:
  ```
  ENV PYTORCH_ROCM_ARCH=gfx1151
  ENV PYTORCH_ALLOC_CONF=expandable_segments:True
  ENV TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
  ENV ROCBLAS_USE_HIPBLASLT=1
  ENV MIOPEN_LOG_LEVEL=3
  ENV HSA_ENABLE_SDMA=0
  ENV HIP_PLATFORM=amd
  ```
  Убрать `HSA_OVERRIDE_GFX_VERSION=11.5.1` (с AMD nightly не нужен,
  может вызывать subtle bugs).
- Удалить `HSA_ENABLE_SDMA=0` если AMD nightly wheels уже исправляют SDMA.
- llama-cpp-python build args обновить (см. R3).

### 12.6 Part 5.4 (CI workflow) — Strix Halo runner

CI test step `test-strix-halo`:
- Smoke benchmark Llama 2 7B Q4_0 на Vulkan: tg ≥45 t/s, pp ≥800 t/s
- Smoke benchmark на HIP: tg ≥45 t/s, pp ≥350 t/s
- Embed bench bge-m3 Q8_0: ≥100 embed/sec @ 1024 tokens
- Rerank bench bge-reranker-v2-m3: p99 ≤200ms

### 12.7 Part 1.2 add «Selection rules» (новый раздел)

Decision matrix для auto-select (R3 + R4):

| Сценарий | Backend → Engine |
|----------|-------------------|
| Single-user chat short ctx | vulkan/llama_cpp |
| Single-user chat long ctx ≥130K | vulkan/llama_cpp |
| Long context pp-bound (RAG long prompt) | rocm/llama_cpp + ROCWMMA_FATTN |
| Concurrent batch ≥16 | rocm/llama_cpp -np 8/16 |
| Embeddings batch ≥4 | rocm/infinity (M2) |
| Embeddings batch < 4 | vulkan/llama_cpp --pooling cls |
| Rerank | vulkan/llama_cpp --reranking |
| GDN-моделям (Qwen3-Next family) | rocm/llama_cpp (Vulkan shader missing #20354) |
| Tool calling / structured outputs | rocm/vllm-patched (M2, accept penalty) |
| Speculative decoding | rocm/vllm-patched (M2) |
| No AMD GPU | cpu/llama_cpp (Zen5 16C, ~120-200 embed/sec) |

### 12.8 Новый ADR-0003 — Memory budgeting на Strix Halo

Proposed: `docs/adr/0003-memory-budgeting-strix-halo.md`:
- Memory pool source = `/sys/class/drm/cardN/device/mem_info_gtt_total`
  (НЕ `mem_info_vram_total`)
- BIOS UMA = 512 MB на Linux, GTT через `ttm.pages_limit`
- 121 GiB GB10 pool ≈ `ttm.pages_limit=31719424` на Strix Halo (паритет)
- Pre-suspend hook unload models
- `agmind/profiles/estimate.py`: pickle budget = runtime detected, не
  захардкожен 121 GiB

### 12.9 Новый ADR-0004 — Engine selection within backend

Proposed: `docs/adr/0004-engine-selection-within-backend.md`:
- Внутри vulkan/rocm backend выбор engine через `AGMIND_ENGINE` env
- Auto-select по profile (tg/pp/mixed) + workload + model_family
- Pluggable interface: LlamaCppEngine / VLLMEngine / InfinityEngine
- M1 = только LlamaCpp; M2 = +VLLM + Infinity

---

## 9. Acceptance criteria (что значит «фаза N закрыта»)

| Phase | Acceptance |
|-------|------------|
| A | `docs/MIGRATION_PLAN.md` в репо, апрув пользователем, `git init` + initial commit. |
| B | `make audit` → 0, `legacy/gb10/README.md` объясняет состояние, smoke-тест `cd legacy/gb10 && bash install.sh --dry-run` работает (если применимо). |
| C | `pytest -m backend_cpu` green; `pytest -m backend_any` green; `AGMIND_BACKEND=cpu python -c "..."` works. |
| D | Все 3 backends зелёные на Strix Halo (CPU/Vulkan/ROCm); `device_info()` правильно; `docs/BENCHMARKS.md` baseline. |
| E | `make audit --fail` exit 0; `pytest` green; smoke-тест inference+embed end-to-end. |
| F | Все 4 Docker-builds в CI green; self-hosted runner отвечает. |
| G | `docs/BENCHMARKS.md` с числами; README актуальный; ADR закрыты; дата в спеке обновлена. |

---

## 10. Timeline (оценка)

| Phase | Estimate | Параллельно? |
|-------|----------|---------------|
| A | 1 ночь (этот документ) | — |
| B | 1-2 дня | Нет (последовательные mv) |
| C | 3-5 дней | Частично (C1-C8 независимы) |
| D | 5-7 дней | Vulkan и ROCm параллельно (worktrees) |
| E | 7-14 дней | Большая часть PR независимы |
| F | 2-3 дня | Параллельно с E |
| G | 2-3 дня | После всего |
| **Итого** | **3-6 недель (1.5 человеко-месяцев)** | при условии 4-8ч/день |

При полностью автономном loop (Karpathy/Anthropic best-practices) с
человеком только на review — можно сжать до 2-3 недель календарного.

---

## 11. Что нужно от пользователя для апрува

1. Прочитать этот документ.
2. Ответить на OQ-1 ... OQ-7 (раздел 0.3).
3. Подтвердить готовность к фазе B (legacy quarantine).
4. (опционально) Прочитать `R-karpathy-method.md` и `R0-autonomous-workflow.md`
   для контекста по R1-R12 расширениям спеки.
5. Дать команду на `git init` + initial commit.

**После апрува** Phase B стартует с PR-B1 (`.planning/` → legacy).

---

## Артефакты этой фазы

```
/home/beelinknode/AGmindx86/
├── AGMIND_MIGRATION_SPEC.md                    (источник правды, не правится)
├── scripts/
│   └── audit_forbidden.py                       ← NEW (Part 4 спеки)
├── docs/
│   └── MIGRATION_PLAN.md                        ← NEW (этот документ)
└── .planning/
    ├── research/x86-migration/
    │   ├── baseline-audit.json                  ← NEW (A2 output)
    │   ├── A3-findings-analysis.md              ← NEW
    │   ├── A4-dependency-graph.md               ← NEW
    │   ├── A5-hot-path.md                       ← NEW
    │   ├── R0-autonomous-workflow.md            ← NEW (R0 recon)
    │   └── R-karpathy-method.md                 ← NEW (R recon)
    └── sessions/
        └── 2026-05-19-overnight.md              ← NEW (journal)
```

**Конец draft v1.0. Жду апрува.**
