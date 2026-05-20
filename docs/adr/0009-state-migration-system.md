# ADR-0009: State Schema Migration System

- **Status:** accepted
- **Date:** 2026-05-20
- **Authors:** @beelinknode (with Claude Opus 4.7)
- **Related:** AGMIND_MIGRATION_SPEC §1.4 (line 351-352), ADR-0005 (ServiceDescriptor), ADR-0008 (entry_points), Phase L.B (snapshot/rollback), Phase L.D

## Контекст

AGmind хранит persistent state в нескольких локациях:

- `~/.local/share/agmind/setup-state.json` — TUI wizard answers (SetupState dataclass)
- `~/.local/share/agmind/cf_dns_api_token` — CF API token (secret)
- `/var/lib/agmind/snapshots/{ISO}/meta.json` — deploy snapshot meta (Phase L.B)
- `/var/lib/agmind/models/` — GGUF model catalog (Phase G)
- `/var/lib/agmind/secrets/` — Ansible-managed runtime secrets

Spec §1.4 layout уже зарезервировал `agmind/migrations.py` под "state schema
migrations", но реализация откладывалась до Phase L. Каждый из state файлов
выше имеет implicit schema; когда формат меняется (например, `SetupState`
получает новое поле, `meta.json` snapshot'а — новый ключ), сейчас нет
системного способа:

1. **Узнать какая версия формата сейчас на диске.** Pydantic v2 пропустит
   неизвестные ключи, но обратно — потеряет defaults на новых полях.
2. **Безопасно мигрировать существующий state вперёд** при обновлении AGmind.
   Сейчас приходится либо переписывать руками, либо ломать backward compat.
3. **Откатить state на предыдущую версию** при downgrade (parity с
   `agmind rollback` для compose state — но для config).

Phase L.D добавляет первую "схему" version=1 как baseline для текущего
формата, и фреймворк для будущих v2+ миграций.

## Рассмотренные варианты

### A: Alembic-style (внешняя зависимость)
- ➕ Battle-tested, многим знаком
- ➖ Тянет SQLAlchemy как dep, наш state — JSON/YAML, не БД
- ➖ Overkill для одного user-state файла + snapshot meta

### B: yoyo-migrations / dbmate / sqitch
- ➕ Меньше чем Alembic
- ➖ Всё ещё DB-ориентированы; миграции SQL-only либо raw scripts
- ➖ Нет нативной интеграции с Python dataclass migration logic

### C: Самописная Migration ABC + pkgutil discovery (выбран)
- ➕ Zero external deps (всё уже импортированное: pkgutil, dataclasses, json)
- ➕ Migrations пишутся как обычные Python модули — могут трогать любой тип state
  (JSON, YAML, файлы, директории, secrets), не только SQL
- ➕ Discovery через `agmind.migrations.versions.*` параллельна паттерну
  Phase H'.E (entry_points для backends): namespace package, авто-discovery
- ➕ Тестируется на 100% без I/O (передача migrations напрямую runner'у)
- ➕ Совместима с future entry_points group `agmind.migrations` для третьих сторон
- ➖ Нет SQL-specific features (DDL diff, autogenerate) — но они нам не нужны
- ➖ Развелась бы в DSL если бы миграции стали сложнее; для текущего scope OK

## Решение

Вариант **C**. Реализация в `agmind/migrations/` package (не в одном файле
`migrations.py` как в spec §1.4 line 352), чтобы каждая migration жила в
отдельном `versions/v{NNN}_{slug}.py`. Это deviation от spec layout
обоснован тем же паттерном что и `agmind/services/` (package, не module) —
single-file быстро ломается на 10+ миграциях.

### Структура

```
agmind/
  migrations/
    __init__.py        # re-exports Migration, MigrationRunner, ...
    base.py            # Migration ABC + MigrationContext (dataclass)
    state.py           # SchemaState + AppliedMigration + load/save
    runner.py          # MigrationRunner (discover/up/down/pending/applied)
    versions/
      __init__.py
      v001_initial.py  # baseline (schema_version=1)
  cli/
    migrate_cmd.py     # cmd_status / cmd_list / cmd_up / cmd_down
  cli/__init__.py      # регистрация `agmind migrate` subapp
docs/adr/
  0009-state-migration-system.md   # этот файл
tests/
  test_migrations.py   # 26 tests (state + runner + v001 + CLI smoke)
```

### Контракт Migration

```python
class Migration(ABC):
    version: int = 0           # > 0, unique
    description: str = ""

    @abstractmethod
    def up(self, ctx: MigrationContext) -> None: ...

    @abstractmethod
    def down(self, ctx: MigrationContext) -> None: ...

    @property
    def name(self) -> str:
        return f"v{self.version:03d}_{type(self).__name__}"

@dataclass(frozen=True)
class MigrationContext:
    user_state_dir: Path   # ~/.local/share/agmind/
    system_state_dir: Path # /var/lib/agmind/
    log: Logger
```

### Persisted state

`~/.local/share/agmind/schema.json`:

```json
{
  "schema_version": 1,
  "applied": [
    {"version": 1, "name": "v001_V001Initial", "applied_at": "2026-05-20T11:00:41+00:00"}
  ]
}
```

`schema_version` всегда равен `max(applied[*].version)`. После полного
rollback — `schema_version=0` и пустой `applied[]`.

### CLI

```
agmind migrate status [--json]
agmind migrate list   [--json]
agmind migrate up     [--target N]                # apply pending до N (inclusive)
agmind migrate down   [--steps N | --target N]    # rollback N последних или всё выше target
```

### Discovery

`MigrationRunner.discover()` импортирует `agmind.migrations.versions` и через
`pkgutil.iter_modules` находит все subclass'ы `Migration` (фильтр
`obj.__module__ == mod_name` чтобы не подбирать re-exports). Сортирует по
`version`. Дубликаты version → `ValueError` на этапе construction.

### Идемпотентность

`SchemaState.record()` no-op если version уже в `applied`. `Runner.up()`
пропускает migrations с уже-applied version. `V001Initial.up()`:
`mkdir(exist_ok=True)`. Безопасно запускать `agmind migrate up` многократно.

### Безопасность down()

`V001Initial.down()` — **no-op**. Удалять `~/.local/share/agmind/` нельзя:
там лежат `setup-state.json` + `cf_dns_api_token` (потерянные secrets). Это
паттерн "baseline migration is irreversible" — `applied` запись удалится,
но user data сохранится. Future миграции которые ДЕЙСТВИТЕЛЬНО меняют формат
обязаны иметь обратимый `down()`.

## Последствия

### Положительные

- **Foundation готов** для всех будущих breaking changes формата setup-state /
  snapshot meta / future state файлов.
- **Audit-friendly:** `agmind migrate status --json` показывает текущую schema
  в machine-readable form — Ansible/CI могут проверить перед deploy.
- **Zero deps** — pkgutil + json + dataclasses, ничего нового в pyproject.toml.
- **Pattern-consistent** с ADR-0008 (plugin system): future third-party can
  publish own migrations через entry_points group `agmind.migrations`
  (не реализовано в L.D, но Runner.discover() легко расширить).

### Отрицательные / технический долг

- **Single user, single host.** Multi-node клaстер из 3-х машин будет иметь 3
  независимых `~/.local/share/agmind/schema.json` — sync между нодами TBD
  (Phase M: cluster ops).
- **Нет автогенерации.** Каждая v002+ пишется руками; нет diff'а от текущего
  Pydantic schema. Для частоты "1 миграция в квартал" это acceptable.
- **Down не идеальна.** Если v002 написана без обратной операции (irreversible
  data transform), `agmind migrate down` для неё — no-op с warning. Конвенция:
  irreversible помечаются в description.

### Что нужно сделать

- [x] L.D.1: `agmind/migrations/` package (base/state/runner/versions)
- [x] L.D.2: `agmind/cli/migrate_cmd.py` + `agmind migrate` subapp
- [x] L.D.3: V001Initial — baseline migration
- [x] L.D.4: 26 unit tests (state/runner/v001/CLI)
- [x] L.D.5: ADR-0009 (этот документ)
- [ ] L.D.6 (future): entry_points group `agmind.migrations` для third-party
- [ ] L.D.7 (future): integration с `agmind doctor` — fail если pending migrations

## Бенчмарки

| Действие | До Phase L.D | После |
|---|---|---|
| Узнать version state | ничего, читать код | `agmind migrate status` |
| Добавить новое поле SetupState | break old installs | v002 с up()/down() |
| Rollback formatting change | manual git revert + hope | `agmind migrate down` |
| CI/CD проверка schema | нет | `agmind migrate status --json` |

## Откат

L.D не трогает существующие state файлы — V001 baseline только создаёт
`~/.local/share/agmind/` (idempotent) и пишет `schema.json`. Откат:

1. `agmind migrate down --target 0` — schema_version=0, applied=[]
2. `rm ~/.local/share/agmind/schema.json` — стирает state migration entirely
3. `git revert <L.D commit>` — удаляет код

Никакие пользовательские данные (setup-state, cf_dns_api_token, snapshots) при
этом не теряются.
