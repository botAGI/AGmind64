# AGmind

[English](README.md) | Русская версия

Приватная LLM/RAG-платформа для AMD Strix Halo и обычных x86_64-хостов.
Основной путь - Docker Compose на Ubuntu; Proxmox VM provisioning и Kubernetes
оставлены за явными контрактами deploy-target.

## Что Такое AGmind

AGmind устанавливает и обслуживает локальный AI-стек: llama.cpp-сервисы для LLM,
embedding и rerank; RAG/storage-сервисы; observability; и проверки
deploy-governance. Проект настроен под AMD Ryzen AI Max+ "Strix Halo" с Radeon
8060S/gfx1151, но сохраняет CPU fallback для обычного x86_64 Linux.

Основные команды идут через CLI `agmind`:

```bash
agmind install
agmind doctor
agmind status
agmind render topology --profile core,rag,observability --json
agmind deploy --profile core,rag,observability --domain lab.example.com --no-prompt
agmind cluster inspect --timeout 10
```

## Текущий Срез Готовности

Последняя локальная проверка готовности: 2026-05-25.

- Deploy target для чистой установки по умолчанию: `ubuntu-compose`.
- Контракты deploy target: `ubuntu-compose`, `proxmox-vm-compose` и `k3s`
  зарегистрированы и валидируются.
- Проверки version governance проходят для constraints, components, deploy
  targets и tool candidates.
- `scripts/version_check.py` пишет 31 запись компонентов. Сейчас ручного
  просмотра требуют major-кандидаты RagFlow и MySQL, ожидаемые holds для
  выбранных pinned-сервисов и несколько registry probes без remote version.
- Deploy-facing mutable image tags и unbounded Ansible package upgrade state
  убраны из текущих deploy/docs поверхностей.
- Runtime-секреты Compose обязательны уже на `config`; `agmind install` пишет
  `/opt/agmind/.env` с mode `0600` и сохраняет generated values при повторном
  запуске.
- Профиль `full` намеренно блокируется deploy conflict checks, пока
  альтернативные edge-proxy не разделены; сначала используй `core,observability`,
  затем добавляй `rag`, когда готовы модели и секреты.
- Локальный cluster status видит эту ноду как `beelinknode-GTR-Pro` на
  `192.168.1.151`; mDNS peer discovery сейчас не возвращает AGmind peers.
- В LAN neighbor был виден адрес `192.168.1.58`, но ping и TCP probes на `22`,
  `41423`, `8080`, `8081`, `8082` и `8006` не ответили. Считаем вторую ноду
  подключенной к LAN, но пока не рекламирующей AGmind.

## Быстрый Старт

```bash
git clone https://github.com/botAGI/AGmind64 agmind
cd agmind
uv venv
uv pip install -e ".[dev]"
agmind install
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

Начинай с `core,observability` или `core,rag,observability`. Не используй
`--profile full` для первого теста хоста: он выбирает Caddy, Nginx и Traefik
вместе, а deploy теперь останавливает этот конфликт host ports 80/443 до Docker.

```bash
agmind doctor --json
agmind cluster inspect --timeout 10
agmind render topology --profile core,rag,observability --json
cat > /tmp/agmind-compose-check.env <<'EOF'
POSTGRES_PASSWORD=check-postgres-password
GRAFANA_PASSWORD=check-grafana-password
MYSQL_ROOT_PASSWORD=check-mysql-root-password
MINIO_ROOT_USER=check-minio
MINIO_ROOT_PASSWORD=check-minio-password
REDIS_PASSWORD=check-redis-password
EOF
agmind render compose \
  --profile core,rag,observability \
  --domain lab.example.com \
  --output /tmp/agmind-fresh-deploy-check.yml
docker compose \
  --env-file /tmp/agmind-compose-check.env \
  -f /tmp/agmind-fresh-deploy-check.yml \
  config --quiet
agmind deploy --profile core,rag,observability \
  --install-dir /tmp/agmind-fresh-deploy-check \
  --domain lab.example.com \
  --no-prompt
```

Проверки репозитория для fresh deploy branch:

```bash
python scripts/constraints_check.py
python scripts/component_check.py
python scripts/deploy_target_check.py
python scripts/tool_candidate_check.py
python scripts/version_check.py \
  --json /tmp/agmind-version-report.json \
  --output /tmp/agmind-version-report.md
python scripts/audit_forbidden.py --fail
python scripts/governance_check.py
```

Фокусные тестовые модули:

```bash
pytest -q tests/test_cluster_detect.py tests/test_cluster_inspect.py tests/test_cluster_inventory.py
pytest -q tests/test_deploy_targets.py tests/test_deploy_conflicts.py tests/test_service_selection.py tests/test_deployment_topology.py
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
python ../../../scripts/proxmox_inventory.py \
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
- Используй `scripts/version_check.py`, чтобы смотреть patch, minor и major
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
agmind cluster inspect --timeout 10
agmind status --tui
agmind logs llama-llm -f
agmind shell traefik --cmd "/bin/sh"
agmind backup --output ~/agmind-backup.tar.gz
agmind restore ~/agmind-backup.tar.gz
agmind migrate status
agmind migrate up
make audit
```

## Карта Архитектуры

```text
agmind/                Python package and CLI
agmind/compute/        Runtime backend detection and selection
agmind/cluster/        mDNS discovery, inventory, and target inspection
agmind/deploy/         Dry-run, apply, rollback, targets, Proxmox helpers
agmind/services/       Service descriptors, topology, Compose/Kubernetes render
templates/services/    Pinned service descriptors
templates/deploy-targets/  ubuntu-compose, proxmox-vm-compose, k3s
constraints/           Python dependency planes
ansible/               Host bootstrap and service configuration
infra/proxmox/         OpenTofu Proxmox VM skeleton
docker/                Backend Dockerfiles
docs/                  Operations notes, benchmarks, plans, ADRs
```

## Документация

- [`docs/HARDWARE.md`](docs/HARDWARE.md) - настройка Strix Halo host.
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) - methodology и результаты benchmark.
- [`docs/CLUSTER.md`](docs/CLUSTER.md) - заметки по cluster и inventory.
- [`infra/proxmox/vm-compose/README.md`](infra/proxmox/vm-compose/README.md) -
  Proxmox VM provisioning target.
- [`docs/adr/`](docs/adr/) - architecture decision records.

## Лицензия

Apache-2.0. См. [LICENSE](LICENSE).
