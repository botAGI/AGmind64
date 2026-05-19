---
phase: A
step: A3
date: 2026-05-19
status: completed
---

# A3 — Group findings: rule × dir, code-vs-docs, top files

## Сводка

- **Файлов проверено:** 10,759
- **Всего находок:** 1,811
- **Файлов с находками:** ~150-200 (часть в .planning/, остальное код/конфиги)
- **Покрытие правил:** 7/7 правил спеки сработали

## Распределение по правилам

| Rule | Findings | % | Что это |
|------|---------:|--:|---------|
| `arm_aarch64` | 1041 | 57% | Упоминания aarch64/arm64/--platform=linux/arm64 |
| `nvidia_hw` | 681 | 38% | GB10/Grace/Blackwell/Hopper/H100/Jetson/DGX/TensorRT-LLM |
| `cuda_python` | 39 | 2% | torch.cuda.\* / .cuda() / device="cuda" |
| `cuda_arch_flags` | 22 | 1% | sm_XX / compute_XX / CUDA_ARCHITECTURES |
| `cuda_paths` | 15 | <1% | nvcr.io/.., /usr/local/cuda, nvidia/cuda: |
| `cuda_runtime` | 7 | <1% | cublas/cudnn/nccl/cudaMalloc |
| `native_march` | 6 | <1% | -march=native в shippable артефактах |

## Распределение по top-level dirs

| Dir | Findings | Природа |
|-----|---------:|---------|
| `.planning/` | 1,080 | Historical planning (GSD AGmind v3.0.1→v3.2.0). **Всё уходит в legacy/.** |
| `<root>` | 162 | README.md, CLAUDE.md, CHANGELOG.md, AGMIND_MIGRATION_SPEC.md (упоминает запреты как rules), install.sh |
| `docs/` | 119 | 13 ADR + matrices + ROADMAP-GO + intro. **Всё уходит в legacy/**, только новые ADR-0001/0002 остаются |
| `lib/` | 110 | Bash-модули (24 файла, ~22k LOC) — installer logic. **Всё в legacy/.** |
| `documentation/` | 103 | Articles + CONFIGURATION + intro. **Всё в legacy/.** |
| `scripts/` | 86 | Shell-скрипты utilities. **Всё в legacy/** кроме `audit_forbidden.py`. |
| `tests/` | 81 | Unit/integration/golden/lint tests. **Всё в legacy/** — переписываются с нуля под Python pytest. |
| `templates/` | 39 | docker-compose/env templates/services/registry. **Всё в legacy/.** |
| `benchmarks/` | 11 | Старые бенчи Spark/AEON-7. **Всё в legacy/.** |
| `.github/` | 8 | CI workflows. **Перепишутся** под Part 5.4 спеки. |
| `monitoring/` | 4 | Prometheus rules. В legacy/. |
| Прочие | <10 | dify-workflows, pipelines, plugins, .claude — в legacy/. |

## Cross-tab: правило × директория

```
                       arm_aarch64  nvidia_hw  cuda_py  cuda_arch  cuda_paths  cuda_rt  native
.planning              720          334        25       0          1           0        0
<root>                 66           73         4        5          6           4        4
docs                   70           46         2        0          1           0        0
lib                    26           80         3        1          0           0        0
documentation          25           62         0        12         4           0        0
scripts                56           22         1        2          1           2        2
tests                  39           39         1        1          0           1        0
templates              28           6          3        0          2           0        0
benchmarks             1            9          0        1          0           0        0
.github                7            1          0        0          0           0        0
```

## Топ-файлов кода (.sh/.py) — что переезжает в `legacy/gb10/`

Эти файлы — реальная бизнес-логика, не документация. 32 файла, 242 находки.

| Findings | File | Назначение | Аналог в новом дереве |
|---------:|------|-----------|------------------------|
| 35 | `lib/wizard.sh` | Interactive installer wizard | `agmind/cli/` (typer/click) |
| 27 | `scripts/check-upstream.sh` | Version drift check (NVIDIA NGC, GitHub) | новый Python checker без NVIDIA |
| 24 | `lib/doctor.sh` | Preflight + health diagnostics | `agmind/diagnostics/` |
| 21 | `scripts/generate-manifest.sh` | Release manifest generator | `agmind/release/` |
| 14 | `lib/detect.sh` | Hardware detect (CPU/GPU/RAM) | `agmind/compute/detect.py` (Part 5 спеки) |
| 14 | `scripts/detect.sh` | Standalone wrapper | merged into compute/detect.py |
| 13 | `tests/unit/test_versions_env_arm64_holds.sh` | Regression: arm64 image holds | удалить, заменить новыми тестами |
| 13 | `scripts/audit_forbidden.py` | **САМ АУДИТОР** | остаётся (rules упоминают запреты) — нужны `# audit: allow` |
| 12 | `install.sh` | Главный entrypoint Bash installer | `agmind/__main__.py` |
| 11 | `lib/i18n.sh` | RU/EN translations wizard | `agmind/i18n/` (gettext или fluent) |
| 10 | `tests/compose/test_image_tags_exist.sh` | arm64 manifest verify | новые pytest на amd64 manifests |
| 7 | `lib/status.sh` | `agmind status` CLI | `agmind status` Python CLI |
| 6 | `lib/security.sh` | Audit + fail2ban + UFW | `agmind/security/` (если нужно) |
| 6 | `lib/estimate.sh` | RAM/disk/GPU estimate per profile | `agmind/profiles/estimate.py` |
| 3 | `tests/unit/test_vllm_args_sanity.sh` | vLLM args validation | удалить (vLLM уходит) |
| 2 | `scripts/agmind.sh` | CLI dispatcher | `agmind` entry_point в pyproject.toml |
| 2 | `lib/compose.sh` | Compose profile builder | если Docker Compose сохраняется как deploy target — `agmind/deploy/compose.py` |
| 2 | `lib/peer.sh` | Dual-Spark cluster setup | удалить (QSFP/Spark-specific) |
| 2 | `lib/config.sh` | .env generation | `agmind/config/` |
| < 2 | прочие 12 файлов | мелкие тесты, health, gpu-metrics | в legacy/ |

## Топ конфигов (.yml/.json/Dockerfile) — что переезжает в `legacy/gb10/`

15 файлов, 79 находок.

| Findings | File | Назначение |
|---------:|------|-----------|
| 21 | `templates/release-manifest.json` | Pinned image:tag manifest с arm64 |
| 14 | `templates/docker-compose.yml` | Main compose (vLLM, Dify, etc) |
| 8 | `.github/workflows/test.yml` | CI matrix arm64+amd64 |
| 4 | `templates/docker-compose.worker.yml` | Peer Spark worker overlay |
| 3 | `Makefile` | `make registry-codegen` etc |
| 3 | `monitoring/alert_rules.yml` | Prometheus alerts (GPU) |
| 3×4 | `tests/golden/expected/*/monitoring/alert_rules.yml` | Golden fixtures |
| 3×2 | `tests/fixtures/config_validate/*/release-manifest*.json` | Fixtures |

## Файлы, которые **должны** содержать запреты (false positives)

Эти файлы упоминают паттерны как rules / описания / историю. Они либо
остаются в основном дереве с `# audit: allow` маркерами, либо переезжают
в legacy/ как archive.

| File | Зачем упоминание | Действие |
|------|-----------------|---------|
| `AGMIND_MIGRATION_SPEC.md` (41) | Сам определяет запреты | остаётся; добавить `# audit: allow` или исключить через `EXCLUDED_DIRS`/special name |
| `scripts/audit_forbidden.py` (13) | RULES содержат паттерны | остаётся; добавить `# audit: allow` к каждой re.compile-строке |
| `.planning/research/x86-migration/*.md` (новые мои отчёты) | Описывают что мы убираем | остаются (рабочие notes для миграции); добавить `# audit: allow` или папку в EXCLUDED |

## Выводы для MIGRATION_PLAN.md

1. **Фаза B упрощается до 5-7 PR-ов директорий**, не файл-за-файлом:
   - PR-B1: `git mv .planning/ legacy/gb10/.planning/` (1080 находок одним движением)
   - PR-B2: `git mv documentation/ legacy/gb10/documentation/`
   - PR-B3: `git mv docs/ legacy/gb10/docs/` (кроме новых ADR-0001/0002)
   - PR-B4: `git mv lib/ scripts/ install.sh legacy/gb10/` (вся installer-логика)
   - PR-B5: `git mv templates/ benchmarks/ monitoring/ pipelines/ plugins/ dify-workflows/ legacy/gb10/`
   - PR-B6: `git mv tests/ legacy/gb10/tests/` (старые тесты — переписываются под pytest)
   - PR-B7: README.md/CLAUDE.md/CHANGELOG.md → `legacy/gb10/` + новые однострочные
2. **После B audit на основном дереве должен быть 0** (кроме self-references в spec/audit).
3. **Audit-аудитор нужно дополнить**:
   - Добавить `# audit: allow` к строкам RULES в `audit_forbidden.py` (рекомендованная санация)
   - Либо добавить `AGMIND_MIGRATION_SPEC.md` и `scripts/audit_forbidden.py` в whitelist (но это hack — лучше первый вариант)
4. **Fresh CLAUDE.md** = одна строка, по Part 5.1 спеки.
5. **Phase C (agmind/compute/)** строится в пустом основном дереве — все
   call-sites из старого кода уйдут в legacy/, переписывать с нуля.

## Что НЕ обнаружено / зоны риска

- `cuda_runtime` = 7 находок — может означать что прямые CUDA C API не
  используются (только Python torch.cuda → ушло бы в cuda_python). Это
  **хорошая новость:** нет C++/CUDA модулей которые нужно компилировать.
- `native_march` = 6 находок — `-march=native` где-то есть в shippable
  артефактах (Dockerfile / build scripts). Проверить локацию точечно.
- `cuda_paths` = 15 — образы `nvcr.io/*` в templates/versions.env и
  упоминания путей `/opt/nvidia/*` в documentation. Все уходят в legacy/.

## Артефакты

- `/.planning/research/x86-migration/baseline-audit.json` — полный JSON
- Этот файл — human-readable summary
- Скрипт прогонять перед каждым крупным move во время фазы B.
