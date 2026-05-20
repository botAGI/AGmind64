# R14 — Backup/Restore gaps после Phase L.E

- **Date:** 2026-05-20
- **Status:** post-implementation recon (L.E shipped as config-only)
- **Driver:** user reminder "не забывай ресерчить" — фиксируем что не покрыто

## Что shipped в L.E

`agmind backup --output <file>.tar.gz` archives:

- `/opt/agmind/docker-compose.yml`
- `/opt/agmind/.env`
- `/opt/agmind/templates/services/*.yaml`
- `~/.local/share/agmind/setup-state.json`
- `~/.local/share/agmind/schema.json` (Phase L.D)
- `/var/lib/agmind/snapshots/` (deploy snapshots from Phase L.B)

Plus `agmind-backup.json` metadata header (format_version=1, ISO timestamp,
included/missing labels).

`agmind restore <file>.tar.gz` extracts back to original paths (или override
via `destinations` kwarg в библиотечном API).

## Что НЕ покрыто (явные gap'ы)

### 1. Docker volume data (highest impact)

Сервисы держат состояние в named volumes:

| Service     | Volume                  | Что внутри            | Размер на full prod |
|-------------|-------------------------|-----------------------|---------------------|
| qdrant      | qdrant_storage          | vector collections    | 1–50 GiB            |
| grafana     | grafana_data            | dashboards, users     | <50 MiB             |
| prometheus  | prometheus_data         | TSDB                  | 1–10 GiB            |
| loki        | loki_data               | log chunks            | 5–50 GiB            |
| alertmanager| alertmanager_data       | silences              | <10 MiB             |
| ragflow     | (если включён) volumes  | docs index            | varies              |

Текущий backup восстановит compose + descriptors, но volumes останутся пустые —
сервисы стартанут с initial state. Это **частичный restore**, не full DR.

**Best practice (researched, не реализовано):**
- Stop containers (`docker compose stop`) для consistency
- `docker run --rm -v <vol>:/source -v $PWD:/dest alpine tar czf /dest/<vol>.tar.gz /source`
- Restart (`docker compose start`)
- Restore: реверс через `tar xzf` в новый volume + `docker compose up`

**Альтернативы для production:**
- **restic** — encrypted incremental backups в S3/Backblaze, dedup. Pattern:
  `restic backup /var/lib/docker/volumes/...` с pre/post stop-start hooks.
- **borgbackup** — locally-mounted encrypted repo, deduplication. Similar flow.
- **btrfs/zfs snapshots** — copy-on-write filesystem snapshot до backup, archive
  read-only mount. Самый быстрый, но требует BTRFS/ZFS на host.

**Decision:** не делать в L.E базовой. Volume backup — отдельная feature
`agmind backup --include-volumes`, ~150-300 LOC + сложный recovery flow.
Записать как L.E.2 backlog item.

### 2. CF DNS API token

`~/.local/share/agmind/cf_dns_api_token` — secret в chmod 600.
**Сознательно НЕ в backup** — secrets вообще не должны лежать в tarball
который можно случайно отправить в Slack / GitHub. User копирует руками.

Документировать в restore output:
```
NOTE: cf_dns_api_token не восстановлен (secret). Восстанови вручную:
  echo "$TOKEN" > ~/.local/share/agmind/cf_dns_api_token && chmod 600
```

**Decision:** добавить hint в `cmd_restore` output. Минимальная задача (~5 LOC).

### 3. Backup encryption

Текущий tarball — plain gzip. Если backup попадает в untrusted storage
(шара, облако, USB) — `.env` с DOMAIN, schema.json с history → утечка
metadata. Никаких credentials, но всё равно нежелательно.

**Best practice:** age (modern replacement for GPG) — `age -p backup.tar.gz`,
или встроенный `restic` который шифрует by default.

**Decision:** опциональный `--encrypt --passphrase-file PATH` через age binary
если установлен. ~30 LOC + 5 tests. Записать в L.E.3.

### 4. Models (GGUF files)

`/var/lib/agmind/models/*.gguf` — модели по 4–40 GiB. Backup'ить
бессмысленно (можно скачать заново через `agmind models pull`), но restore
flow должен warn'ить если models отсутствуют после restore:

```
Restore complete. WARNING: /var/lib/agmind/models is empty —
run `agmind models pull <model>` to populate.
```

**Decision:** добавить warning в `cmd_restore` если каталог моделей пуст.
~10 LOC. Записать в L.E.4.

### 5. Multi-node backup

Cluster из 2–3 нод имеет независимый state на каждом host. Sync — TBD в
Phase M (cluster ops). Сейчас `agmind backup` — per-host.

**Decision:** explicit в README — "single host backup, Phase M for
multi-node". Никаких code changes.

### 6. Restore idempotency

Сейчас restore просто перезаписывает файлы. Если deployment running —
compose файл изменится под ногами у docker. **Рекомендация:** restore
должен:

1. Detect если deployment running (`docker compose ps` returns non-empty)
2. Prompt "stop deployment first? [Y/n]" если running
3. После extract — suggest `agmind deploy --apply` чтобы применить
   restored compose

**Decision:** L.E.5 — добавить stop-detect в `cmd_restore` (~20 LOC).

## Verification scope of shipped L.E

What's actually proven:

- 23 unit tests cover happy path + 6 error paths (no compose, no docker,
  rc!=0, timeout, invalid yaml, missing file)
- Restore roundtrip verified (write → wipe → restore → byte-compare)
- Directory extraction verified (templates/services/*.yaml)
- Metadata validation verified (raises ValueError on non-agmind archive)

What's NOT verified:

- Real docker compose behavior (subprocess mocked)
- Cross-host restore (tested only within tmp_path tree)
- Behavior на симлинках внутри snapshots dir
- Backup correctness под concurrent writes (race с running deployment)

## Priority recommendation

| Item | Effort | When |
|------|--------|------|
| L.E.1 hint о cf_dns_api_token | 5 LOC | next session |
| L.E.4 warn если models пуст | 10 LOC | next session |
| L.E.5 stop-detect перед restore | 20 LOC | next session |
| L.E.2 `--include-volumes` flag | 200 LOC | new phase |
| L.E.3 age encryption optional | 50 LOC | new phase |

Items 1+4+5 — closure для basic L.E (~35 LOC). 2+3 — отдельный PR / feature.

## Sources consulted (post-hoc)

- Docker docs: https://docs.docker.com/storage/volumes/#back-up-restore-or-migrate-data-volumes
- restic: https://restic.readthedocs.io/en/stable/060_forget.html — incremental + retention
- age encryption: https://age-encryption.org/v1
- ZFS snapshot pattern: read-only mount + tar; cheaper than `docker stop`
