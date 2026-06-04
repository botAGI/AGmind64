# ADR-0010: End-to-End Installer (`agmind install`)

- **Status:** accepted
- **Date:** 2026-05-20
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** ADR-0006 (Python renderer), ADR-0008 (entry_points), Phase J
  (TUI wizard), Phase L.B (deploy runner), Phase N
- **Driver:** user explicitly asked for legacy-AGmind UX ("выбрал что надо
  → установка пошла"), но реализованное на новом Python+TUI+Ansible
  стеке. Никакого 1700-LOC `install.sh`.

## Контекст

К концу Phase L у нас были **готовые куски**:
- `agmind doctor` — preflight (Phase H'.D)
- `agmind setup` — TUI wizard собирает domain/CF token/services (Phase J)
- `ansible/install.yml` — playbook для apt/groups/dirs (Phase H'.A)
- `agmind deploy --apply` — idempotent compose deploy с snapshot/rollback
  (Phase L.B)

Чего **не было**: единого UX. Пользователь должен был:

1. `agmind doctor` — проверить
2. `agmind setup` — TUI собрать config
3. `sudo mkdir -p /var/lib/agmind /opt/agmind` (ручной sudo)
4. `mv ~/models/*.gguf /var/lib/agmind/models/` (ручной)
5. `ansible-playbook install.yml --ask-become-pass` (или забыть и
   столкнуться с broken deploy)
6. `agmind deploy --apply ...` (с правильными флагами)
7. `agmind status --tui` (watch)

7 шагов, 2 sudo prompt'а, в разных программах. Legacy AGmind делал то
же самое в `sudo bash install.sh` — **одна команда, один TUI**. Phase
N — это возврат к такому UX без возврата к bash.

## Рассмотренные варианты

### A: `install.sh` (bash bootstrap, ~150 LOC)
- ➕ Curl-pipe friendly: `curl -fsSL .../install.sh | bash`
- ➖ Возвращает то, от чего мы только что ушли — bash скрипт с
  idempotency руками + parse'ом argparse в bash
- ➖ User отверг это явно: "мы основательно ушли от sh"

### B: Python `agmind install` команда + TUI screen (выбран)
- ➕ Никакого bash. Логика на Python, secrets handling на Python.
- ➕ Reuse: TUI wizard, deploy runner, doctor — всё уже есть.
- ➕ Ansible вызывается только из одного step (bootstrap) — изоляция
  sudo blast radius.
- ➕ TUI screen с live прогрессом каждого этапа (RichLog + ProgressBar +
  steps list) — лучший UX чем bash echo'ы.
- ➖ Не curl-pipe-friendly (требует `pip install agmind`). Митигируется:
  один раз `pip install agmind && agmind install`, дальше всё в одной
  программе.

### C: один большой Ansible playbook + drop-in TUI wrapper
- ➕ Декларативный
- ➖ Ansible не умеет live progress в RichLog нативно
- ➖ Tight coupling Python UI ↔ Ansible state machine

## Решение

Вариант **B**. Сделано в Phase N:

### Архитектура

```
agmind install
    │
    ├─ getpass.getpass("Sudo password:")    # один раз
    │
    ├─ run_setup_wizard()                   # Phase J TUI
    │     (или skip если --domain + --cf-token-file даны)
    │
    └─ AgmindShell.push_screen(InstallProgressScreen)
            │
            └─ InstallOrchestrator.run()    # worker thread
                  │
                  ├─ DoctorStep            # preflight (no sudo)
                  ├─ BootstrapStep         # ansible-playbook --become (sudo)
                  ├─ ImagePullStep         # docker compose pull
                  ├─ ModelDownloadStep     # curl с resume
                  └─ DeployStep            # reuse agmind deploy --apply
```

### Sudo handling

Один prompt в самом начале, до запуска TUI:

```python
sudo_pw = getpass.getpass("Sudo password: ")
```

Password передаётся в `BootstrapStep` через **anonymous pipe**:

```python
rfd, wfd = os.pipe()
os.write(wfd, password.encode() + b"\n")
os.close(wfd)
subprocess.Popen([
    "ansible-playbook", "install.yml",
    "--become-password-file", f"/dev/fd/{rfd}",
    ...
], pass_fds=(rfd,))
```

Pros vs alternatives:
- **vs temp file**: pipe не trash'ит disk; auto-cleanup на close
- **vs --ask-become-pass + stdin push**: pipe сразу даёт named path
  через `/dev/fd/N` — Ansible умеет это
- **vs SUDO_ASKPASS GUI**: не требует X11/Wayland; работает в SSH

После `BootstrapStep` orchestrator вызывает `config.sudo_password = None`
(drop reference, best-effort). После всего install — `wipe_secrets()` сбрасывает
ссылки на `cf_api_token` и `sudo_password` (rebind to ""/None — НЕ zeroization
памяти: Python strings immutable, байты могут жить до GC).

### Step contract (одинаковый для всех)

```python
class InstallStep(ABC):
    step_id: str
    label: str

    @abstractmethod
    def run(self, callback: ProgressCallback,
            config: InstallConfig) -> InstallStepResult:
        ...
```

`ProgressCallback` — единый интерфейс для UI subscription:

```python
class ProgressKind(Enum):
    STEP_START = "step_start"   # widget: mark step as 🔄
    STEP_DONE = "step_done"     # widget: mark step as ✓
    STEP_ERROR = "step_error"   # widget: mark step as ✗, red status
    LOG = "log"                 # widget: append line to RichLog
    PROGRESS = "progress"       # widget: update ProgressBar (0-100)
```

TUI screen вызывает orchestrator из `@work(thread=True)` worker и
эмиттит `call_from_thread()` для каждого update. CLI (`--no-tui`)
просто print'ит линию для каждого event.

### Failure handling

- Step → fails → orchestrator останавливается, emit `step_error`, не
  запускает следующие steps. `InstallResult.failed_step` показывает
  какой именно упал.
- Unhandled exception → caught в orchestrator.run(), оборачивается в
  InstallStepResult(success=False, message=str(exc)).
- `DeployStep` сам делает rollback через Phase L.B SnapshotManager если
  compose up или healthcheck не прошли.
- ProgressCallback raising → проглатывается с `log.debug` чтобы UI bug
  не валил install.

### Что НЕ в install flow

- **Models не выбираются wizard-ом** в первой версии — model_repo /
  model_file приходят через CLI flag (`--model-repo` / `--model-file`).
  Default: `0xSero/Qwen3.6-35B-A3B-GGUF-Strix:Q4_K_M.gguf` (Phase H
  verified baseline). Расширить wizard на dropdown — N.G follow-up.
- **CF DNS records** проверка — не автоматизирована. Если user не
  настроил `*.domain.com → host IP`, Traefik не выдаст серт и
  healthcheck timeout. Это видно в RichLog но не predicted.
- **Multi-node** — install single-host. Phase M cluster ops отдельно.
- **Encryption** sudo password at rest — best-effort zero out в Python,
  не secure memory. На многоюзерном хосте теоретически читается через
  ptrace до wipe. Acceptable для single-user dev/lab box.

## Последствия

### Положительные

- **Один UX entry point**: `agmind install` → готовый стек. Без ручных
  sudo шагов, без переключения между программами.
- **Видимый прогресс**: live RichLog показывает все ansible task'и,
  docker pull layers, curl percent, compose service status. Failure
  diagnosable из логов, не silent.
- **Reused компоненты**: orchestrator переиспользует Phase H'.D doctor,
  Phase J wizard, Phase L.B deploy runner — никакой дубликации.
- **Тестируемый**: 18 unit tests против orchestrator + step contract
  (fake steps), без зависимости от docker/sudo/ansible.
- **CI-friendly**: `--no-tui` + `--dry-run` + `--domain --cf-token-file`
  → headless install для automation.

### Отрицательные / технический долг

- **18 tests cover happy path и error paths orchestrator-а** — реальный
  E2E install (BootstrapStep с настоящим Ansible) не запускается в
  pytest. Smoke test ручной, на реальной machine.
- **Sudo через pipe** работает только на Linux. На macOS/BSD `/dev/fd/N`
  работает, но Ansible become-password-file через FD не везде
  поддерживается. Не блокер: целевая платформа Strix Halo = Linux.
- **Models dir hardcoded** на `/var/lib/agmind/models` через BootstrapStep
  (создаст dir + chown). Если user хочет другой path — нужен extra
  config field. Откладываем до запроса.
- **Cancel mid-step** не interruptable: orchestrator не передаёт
  cancellation token в steps. Cancel button dismisses screen, но
  background work (`docker pull`, `curl`) продолжает. Acceptable:
  cancel редко нужен, force-kill через Ctrl+C на терминал работает.

### Что нужно сделать

- [x] N.A: `agmind/install/orchestrator.py` (InstallOrchestrator + types)
- [x] N.B: `agmind/install/steps.py` (5 concrete steps)
- [x] N.C: `agmind/cli/tui/install_screen.py` (Textual screen)
- [x] N.D: `agmind/cli/__init__.py` — `agmind install` command
- [x] N.E: 18 unit tests
- [x] N.F: ADR-0010 (this document)
- [ ] N.G: model selector в wizard (dropdown с recommended GGUFs)
- [ ] N.H: README раздел "Install" + screenshot SVG
- [ ] N.I: real-hardware smoke test на чистой Ubuntu 24.04

## Откат

Если Phase N приведёт к проблемам:

1. `agmind install` команда — изолирована. `git revert <commit>` убирает
   точку входа, ничего другого не ломает.
2. `agmind/install/` package — не импортируется ничем кроме CLI и
   `install_screen.py`. Удаление безболезненно.
3. Уже выполненный install с `agmind install` остаётся работать через
   `agmind deploy --apply` / `agmind status` — N не меняет deploy
   semantics, только добавляет orchestration layer.
4. State files (`~/.local/share/agmind/`, `/var/lib/agmind/`,
   `/opt/agmind/`) — те же что и раньше, не migrate'я.
