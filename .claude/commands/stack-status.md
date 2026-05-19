---
description: Health audit работающего стека — контейнеры, GPU, endpoints, БД, mDNS
---

# /stack-status — полный health audit

Собирает состояние живого AGmind стека и выдаёт compact-отчёт.
Использовать после деплоя или при отладке "что-то не работает".

## Что проверять

### 1. Контейнеры

```bash
sudo docker ps --format '{{.Names}} {{.Status}}' | sort
sudo docker inspect --format '{{.Name}} {{.RestartCount}}' $(sudo docker ps -q) | grep -v ' 0$'
```

Flag: RestartCount > 0, Status не Up/healthy.

### 2. GPU (только на DGX/GPU-хостах)

```bash
nvidia-smi --query-gpu=memory.used,temperature.gpu,utilization.gpu --format=csv,noheader
nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader
```

### 3. vLLM inference smoke test

```bash
sudo docker exec agmind-vllm curl -sf http://localhost:8000/v1/models | head -c 200
sudo docker exec agmind-vllm-embed curl -sf http://localhost:8000/v1/models | head -c 200
```

### 4. Dify API + plugin-daemon

```bash
curl -sf http://localhost/console/api/setup
sudo docker exec agmind-db psql -U postgres -d dify_plugin -c "\dt" | wc -l
# ожидается >= 17 строк (13 таблиц + header/footer)
```

### 5. mDNS

```bash
systemctl is-active agmind-mdns avahi-daemon
avahi-resolve -n agmind-dify.local 2>&1
```

### 6. Endpoints

```bash
for h in dify chat dbgpt notebook search crawl; do
  curl -so /dev/null -w "${h}.local: %{http_code} %{time_total}s\n" \
    -H "Host: agmind-${h}.local" http://localhost/
done
```

## Формат отчёта

Compact таблица в конце:

```
Containers: 28/28 running, 26/28 healthy (loki/portainer no healthcheck)
GPU:        92 GB used, 42°C, 0% util idle
vLLM:       gemma-4-26B-A4B-it loaded, embed/rerank ok
Dify API:   healthy, plugin_daemon 13 tables
mDNS:       active, agmind-dify.local → 192.168.1.45
Endpoints:  6/6 responding
```

Красным — всё что требует внимания.
