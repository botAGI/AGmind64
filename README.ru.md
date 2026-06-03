# AGmind

[English](README.md) | Русская версия

[![CI](https://github.com/botAGI/AGmind64/actions/workflows/ci.yml/badge.svg)](https://github.com/botAGI/AGmind64/actions/workflows/ci.yml)

Приватная LLM/RAG-платформа для AMD Strix Halo и обычных x86_64-хостов.
Основной путь - Docker Compose на Ubuntu; Proxmox VM provisioning и Kubernetes
оставлены за явными контрактами deploy-target.

## Что Такое AGmind

AGmind устанавливает и обслуживает локальный AI-стек: llama.cpp-сервисы для LLM,
embedding и rerank; RAG/storage-сервисы; опциональную n8n-автоматизацию
workflow; observability; и проверки deploy-governance. Проект настроен под AMD
Ryzen AI Max+ "Strix Halo" с Radeon 8060S/gfx1151, но сохраняет CPU fallback
для обычного x86_64 Linux.

Основные команды идут через CLI `agmind`:

```bash
agmind setup
agmind verify install --domain lab.example.com
agmind doctor
agmind status
agmind render topology --profile core,rag,observability --json
agmind cluster inspect --timeout 10
```

## Текущий Срез Готовности

Последняя локальная проверка готовности: 2026-05-26.

- Deploy target для чистой установки по умолчанию: `ubuntu-compose`.
- Контракты deploy target: `ubuntu-compose`, `proxmox-vm-compose` и `k3s`
  зарегистрированы и валидируются.
- Setup wizard ведет выбор сервисов по операторским отделам: foundation,
  RAG/agents/automation, data, model runtime, monitoring и security.
- `agmind setup` - основной one-command TUI flow: config wizard, bootstrap,
  runtime `.env`, точная валидация Docker Compose config перед реальным pull
  images, model pulls, Compose deploy, health checks, rollback-aware failure
  handling и финальная подсказка пути к credentials.
- `agmind verify install` - non-destructive gate для fresh install: расширяет
  выбранные в setup сервисы, пишет временные runtime env, гоняет deploy dry-run,
  валидирует Docker Compose config и image pull dry-run для ключевых стеков,
  ставит нужные Ansible collections в игнорируемый локальный cache и делает
  syntax-check bootstrap playbook.
- Мониторинг из коробки - профиль `observability`: Prometheus, Grafana, Loki,
  Alloy, Alertmanager и node exporter входят в default service selection.
- n8n принят как opt-in профиль `automation`: pinned image, persistent
  `/var/lib/agmind/n8n`, выключенные diagnostics и включенные Prometheus
  metrics.
- Проверки version governance проходят для constraints, components, deploy
  targets и tool candidates.
- `scripts/checks/version_check.py` пишет 32 записи компонентов. Сейчас ручного
  просмотра требуют major-кандидаты RagFlow и MySQL, ожидаемые holds для
  выбранных pinned-сервисов и несколько registry probes без remote version.
- Deploy-facing mutable image tags и unbounded Ansible package upgrade state
  убраны из текущих deploy/docs поверхностей.
- Runtime-секреты Compose обязательны уже на `config`; `agmind install` пишет
  `/opt/agmind/.env` с mode `0600` и сохраняет generated values при повторном
  запуске. Финальные summary setup/install показывают оператору путь к этому
  файлу, но не печатают значения credentials.
- `agmind install` также пишет `/opt/agmind/version.env` с mode `0644`. В нем
  фиксируются версия AGmind и image tag/digest выбранных runtime-сервисов для
  drift review, backup и rollback notes.
- Видимый в репозитории пример лежит в `templates/runtime/version.env.example`
  и отслеживает pinned descriptors для Uptime Kuma, Homarr, Watchtower, Dozzle
  и Netdata.
- Compose- и Kubernetes-render поддерживают повторяемые флаги `--service/-s` для
  фокусных runtime-проверок, например `agmind render compose --service n8n
  --service dozzle`. Explicit service render падает рано, если не выбраны hard
  `depends_on` сервисы.
- Compose-render теперь использует health-aware dependency gates: сервисы ждут
  healthy Postgres/Redis/MySQL/MinIO/etc., если у dependency descriptor есть
  healthcheck. Это снижает startup races на fresh deploy.
- Профиль `full` снова рендерится после cleanup альтернативных edge-proxy, но
  для fresh install безопаснее staged rollout: сначала `core,observability`,
  затем `rag`/другие профили после проверки моделей и секретов.
- Локальный cluster status видит эту ноду как `beelinknode-GTR-Pro` на
  `192.168.1.151`; `agmind cluster inspect` теперь показывает и AGmind mDNS
  peers, и сырые LAN neighbor candidates из локальной neighbor table.
- Текущие LAN probes не видят AGmind mDNS peers. Среди neighbor candidates есть
  `192.168.1.58` и `192.168.1.78`; TCP `41423` у них не открыт. Считаем вторую
  ноду физически видимой в LAN, но пока не рекламирующей AGmind.

## Быстрый Старт

```bash
git clone https://github.com/botAGI/AGmind64 agmind
cd agmind
uv venv
uv pip install -e ".[dev]"

# Опциональная проверка, если Docker Compose 2.24+ уже доступен.
agmind verify install --domain lab.example.com

# TUI-установка одной командой; bootstrap поставит или починит Docker Engine при необходимости.
agmind setup
```

Non-interactive установка Strix Halo:

```bash
agmind install --no-tui \
  --domain lab.example.com \
  --cf-token-file token.txt \
  --model-id qwen36-a3b-q4km \
  --ctx-size 16384 \
  --kv-cache q8_0
```

Каталог моделей:

```bash
agmind install --list-models
```

## План Проверки Свежего Деплоя

Перед изменением `/opt/agmind` на чистом хосте запусти:

Начинай с `core,observability` или `core,rag,observability`.

```bash
agmind doctor --json
agmind cluster inspect --timeout 10
agmind verify install --domain lab.example.com
agmind setup
```

Полезные фокусные варианты:

```bash
agmind verify install --domain lab.example.com --scenario explicit-dify-ragflow-milvus
agmind verify install --domain lab.example.com --skip-ansible
agmind verify install --domain lab.example.com --json
```

Проверки репозитория для fresh deploy branch:

```bash
python scripts/checks/constraints_check.py
python scripts/checks/component_check.py
python scripts/checks/deploy_target_check.py
python scripts/checks/tool_candidate_check.py
python scripts/checks/version_check.py \
  --json /tmp/agmind-version-report.json \
  --output /tmp/agmind-version-report.md
python scripts/checks/audit_forbidden.py --fail
python scripts/checks/governance_check.py
```

Фокусные тестовые модули:

```bash
pytest -q tests/cluster/test_cluster_detect.py tests/cluster/test_cluster_inspect.py tests/cluster/test_cluster_inventory.py
pytest -q tests/deploy/test_deploy_targets.py tests/components/test_deploy_conflicts.py tests/services/test_service_selection.py tests/services/test_deployment_topology.py
```

`agmind doctor` может завершиться с code `1`, если есть warnings. Code `2`
означает blocking failure.

## Детект Кластера Из Двух Нод

AGmind находит peers через mDNS service `_agmind._tcp.local.`. Устройство в той
же LAN не появится, пока оно не рекламирует AGmind или совместимый service
record.

На второй ноде:

```bash
cd ~/agmind
uv venv
uv pip install -e ".[dev]"
agmind cluster advertise --duration 600
```

На первой ноде:

```bash
agmind cluster detect --timeout 10
agmind cluster status --timeout 10
agmind cluster inspect --timeout 10
```

Если discovery пустой:

- Проверь, что обе ноды в одном subnet/VLAN.
- Запусти или установи `avahi-daemon` на Linux-хостах.
- Разреши UDP 5353/mDNS в firewall.
- Проверь, что в Python environment установлен `zeroconf`.
- Проверь `agmind cluster inspect --timeout 10`: `LAN neighbors` означает, что
  устройство видно на L2/ARP, даже если AGmind mDNS еще не рекламируется.
- На другой ноде запусти `agmind cluster advertise --duration 600` и убедись,
  что TCP `41423` доступен с этой ноды.
- Проверь reachability через `ip neigh show`, `ping <node-ip>` и targeted TCP
  probes для SSH или ожидаемого service port.

## Включение Proxmox

В AGmind есть два Proxmox-пути: Compose runtime может скрейпить существующий
Proxmox VE cluster через `proxmox-exporter`, а экспериментальный
`proxmox-vm-compose` target может provision Ubuntu VM shells до того, как
Ansible и Compose продолжат установку.

Включение Proxmox exporter для существующего Compose-хоста:

```bash
sudo install -d -m 0750 /etc/agmind/proxmox-exporter
sudo cp templates/observability/proxmox-exporter/pve.yml.example \
  /etc/agmind/proxmox-exporter/pve.yml
sudoedit /etc/agmind/proxmox-exporter/pve.yml
python -m agmind.deploy.proxmox_exporter \
  --config /etc/agmind/proxmox-exporter/pve.yml
agmind render compose \
  --profile core,observability,proxmox \
  --domain lab.example.com \
  --output /tmp/agmind-proxmox.yml
docker compose \
  --env-file /opt/agmind/.env \
  -f /tmp/agmind-proxmox.yml \
  config --quiet
```

Эквивалентные Ansible variables:

```yaml
agmind_proxmox_exporter_existing_config: false
agmind_proxmox_exporter_verify_ssl: true
agmind_proxmox_exporter_user: "prometheus@pve"
agmind_proxmox_exporter_token_name: "agmind"
agmind_proxmox_exporter_token_value: "REDACTED"
```

Provision Proxmox VM shells через OpenTofu:

```bash
cd infra/proxmox/vm-compose
cp terraform.tfvars.example terraform.tfvars
$EDITOR terraform.tfvars
tofu init
tofu plan
tofu apply
tofu output -json > /tmp/agmind-proxmox-output.json
python ../../../scripts/ops/proxmox_inventory.py \
  --input /tmp/agmind-proxmox-output.json \
  --output ../../../ansible/inventory/proxmox.generated.yml
```

Держи `terraform.tfvars`, state files и plan files локально; модуль специально
игнорирует их.

## Политика Версий И Пинов

- Не использовать mutable floating image tags для runtime или deploy examples.
- Service image pins лежат в `templates/services/*.yaml`.
- Python dependency planes лежат в `constraints/*.txt`.
- Намеренные version holds лежат в `templates/version_holds.yaml`.
- Используй `scripts/checks/version_check.py`, чтобы смотреть patch, minor и major
  candidates.
- Major candidates требуют ручного review перед изменением pins.

Текущие manual-review items из локального отчета:

- У RagFlow есть major candidate с `v0.25.5` на `v1.0`.
- У MySQL есть major candidate с `8.0.46-oraclelinux9` на `9.7.0`.
- Часть сервисов намеренно held, включая Elasticsearch, llama.cpp, Dify API,
  Dify plugin daemon, PostgreSQL и Redis.

## Day-2 Операции

```bash
agmind doctor
agmind status
agmind endpoints                 # published services: URL + state
agmind open grafana              # print a service URL (SSH-pipeable)
agmind creds show                # logins + passwords (root-only, masked)
agmind cluster inspect --timeout 10
agmind status --tui
agmind logs llama-llm -f
agmind shell traefik --cmd "/bin/sh"
agmind backup --ask-sudo-password --output ~/agmind-backup.tar.gz
agmind restore --ask-sudo-password ~/agmind-backup.tar.gz
agmind ops smoke backup-root-owned --dry-run
agmind migrate status
agmind migrate up
make audit
```

`agmind backup` сохраняет rendered Compose, runtime `.env`, runtime
`version.env`, setup state, snapshot descriptor-ов и deploy snapshots. Он не
архивирует model files и Docker volume data; для них нужны отдельные storage
snapshots.

## Карта Архитектуры

```text
agmind/                Python package and CLI
agmind/core/           Shared logging, env, and secret helpers
agmind/compute/        Runtime backend detection and selection
agmind/cluster/        mDNS discovery, inventory, and target inspection
agmind/deploy/         Dry-run, apply, rollback, targets, Proxmox helpers
agmind/install/        Fresh install planning, steps, and verification
agmind/ops/            Backup, restore, logs, shell, and smoke helpers
agmind/services/       Service descriptors, topology, Compose/Kubernetes render
scripts/checks/        CI, pre-commit, and governance checks
templates/services/    Pinned service descriptors
templates/deploy-targets/  ubuntu-compose, proxmox-vm-compose, k3s
constraints/           Python dependency planes
ansible/               Host bootstrap and service configuration
infra/proxmox/         OpenTofu Proxmox VM skeleton
docker/                Backend Dockerfiles
tests/                 Domain-mirrored test layout
docs/                  Operations notes, benchmarks, codebase map, ADRs
```

## Документация

- [`docs/HARDWARE.md`](docs/HARDWARE.md) - настройка Strix Halo host.
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) - methodology и результаты benchmark.
- [`docs/CLUSTER.md`](docs/CLUSTER.md) - заметки по cluster и inventory.
- [`docs/CODEBASE.md`](docs/CODEBASE.md) - карта ответственности codebase.
- [`docs/operations/incident-response.md`](docs/operations/incident-response.md) -
  runbook по triage и восстановлению при инцидентах.
- [`docs/DR.md`](docs/DR.md) - disaster recovery (RPO/RTO + сценарии + drill).
- [`infra/proxmox/vm-compose/README.md`](infra/proxmox/vm-compose/README.md) -
  Proxmox VM provisioning target.
- [`docs/adr/`](docs/adr/) - architecture decision records.

## Лицензия

Apache-2.0. См. [LICENSE](LICENSE).
