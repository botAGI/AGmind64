# AGmind Cluster Guide

Multi-node AGmind deployment: 1 master + N workers. Master координирует
RAG (Dify, Qdrant, etc), workers добавляют inference capacity.

## When to use cluster

- **Single Strix Halo (default)** — все на одном узле. Простой, fast.
- **Cluster** — нужно если:
  - Concurrent inference > одного узла tg capacity
  - Шардинг моделей на multi-host (TBA — не в M1)
  - Разделение workloads (interactive chat на master, batch embed на workers)

## Topology

```
┌─ Master Node ─────────────────────────┐
│                                       │
│  Profiles: core + rag + observability │
│                                       │
│  - llama-llm (Vulkan, primary)        │
│  - llama-embed                        │
│  - llama-rerank                       │
│  - qdrant (vector store)              │
│  - Dify + postgres + redis            │
│  - docling-serve-cpu                  │
│  - Traefik (edge entrypoint)          │
│  - Prometheus / Grafana / Loki        │
│                                       │
│  Cluster coordinator:                 │
│    /etc/agmind/cluster.yaml           │
│    agmind.cluster.{router,peer}       │
│                                       │
└──┬────────────────────────────────────┘
   │
   ├─ mDNS / static DNS / IP routing
   │
   ▼
┌─ Worker Node 01 ──────────────────────┐
│                                       │
│  Profiles: core                       │
│                                       │
│  - llama-llm (Vulkan, additional)     │
│                                       │
│  Optional:                            │
│  - node-exporter (для Prometheus)    │
│                                       │
└───────────────────────────────────────┘

┌─ Worker Node 02 ──────────────────────┐
│  Same as worker-01                    │
└───────────────────────────────────────┘
```

## Prerequisites

1. **LAN connectivity** между master + workers (Gigabit+ recommended;
   QSFP/2.5G/10G better)
2. **SSH key-based access** master → workers (для Ansible)
3. **Same kernel version** на всех узлах (≥ 6.17 HWE)
4. **mDNS** работает (avahi-daemon на всех), либо static DNS / `/etc/hosts`
5. **Firewall** allows TCP 8080-8082 (llama-server) между узлами

## Setup

### 1. Inventory

Скопируй `ansible/inventory/cluster.yml` и заполни worker IPs:

```yaml
# ansible/inventory/cluster.yml (edit in-place)
all:
  children:
    agmind_master:
      hosts:
        agmind-master:
          ansible_host: 192.168.1.10
          agmind_role: master
          agmind_profiles: [core, rag, observability]

    agmind_workers:
      hosts:
        agmind-worker-01:
          ansible_host: 192.168.1.11
          agmind_role: worker
          agmind_profiles: [core]
          agmind_worker_endpoint: "http://agmind-worker-01.local:8080"
        agmind-worker-02:
          ansible_host: 192.168.1.12
          agmind_role: worker
          agmind_profiles: [core]
          agmind_worker_endpoint: "http://agmind-worker-02.local:8080"
```

### 2. SSH setup

```bash
# На master:
ssh-keygen -t ed25519 -N "" -f ~/.ssh/agmind_cluster
ssh-copy-id -i ~/.ssh/agmind_cluster.pub user@agmind-worker-01
ssh-copy-id -i ~/.ssh/agmind_cluster.pub user@agmind-worker-02

# Verify:
ansible -i ansible/inventory/cluster.yml agmind_workers -m ping
```

### 3. Install

```bash
sudo ansible-playbook -i ansible/inventory/cluster.yml ansible/install.yml
```

Это выполнит:
- На каждом узле: bootstrap + strix_halo + docker
- На master: + services + observability + cluster role
- На workers: + только core services (llama-llm)

### 4. Verify

На master:
```bash
agmind status
# Available: ['vulkan', 'cpu']   (или rocm если ROCm установлен)
# Selected:  vulkan / llama_cpp

cat /etc/agmind/cluster.yaml
# Should show role: master, peers: [worker-01, worker-02]
```

На worker:
```bash
agmind status
# Available: ['vulkan', 'cpu']
# Selected:  vulkan / llama_cpp

curl http://localhost:8080/health
# {"status": "ok"}
```

## Routing strategies

`/etc/agmind/cluster.yaml::routing.strategy`:

| Strategy | When to use | Behavior |
|----------|-------------|----------|
| `round-robin` (default) | Uniform load | Циклически по списку alive peers |
| `least-loaded` | Heterogeneous workloads | Peer с минимальным inflight counter |
| `sticky-session` | KV cache reuse (chat sessions) | hash(session_id) → consistent peer |
| `random` | Load testing | Uniform random |

Change:
```bash
sudo sed -i 's/strategy: round-robin/strategy: least-loaded/' /etc/agmind/cluster.yaml
# CLI helpers read cluster.yaml on each run. Restart any long-running consumer
# process explicitly if you wire one to agmind.cluster routing.
```

## Health checks

Cluster проверяет workers periodically (default 30s interval):

```bash
python3 -c "
from agmind.cluster import load_cluster_config
from agmind.cluster.peer import probe_all

cfg = load_cluster_config()
healths = probe_all(cfg.peers, timeout=5.0)
for h in healths:
    icon = '✓' if h.is_alive else '✗'
    print(f'{icon} {h.peer.name or h.peer.url}: alive={h.is_alive} inflight={h.inflight}')
    if not h.is_alive:
        print(f'    last error: {h.last_error}')
"
```

## Adding a new worker

1. Add к inventory:
   ```yaml
   agmind-worker-03:
     ansible_host: 192.168.1.13
     agmind_role: worker
     agmind_profiles: [core]
     agmind_worker_endpoint: "http://agmind-worker-03.local:8080"
   ```

2. SSH access:
   ```bash
   ssh-copy-id user@192.168.1.13
   ```

3. Bootstrap новый worker:
   ```bash
   sudo ansible-playbook -i ansible/inventory/cluster.yml \
       ansible/install.yml --limit agmind-worker-03
   ```

4. Update master cluster config:
   ```bash
   sudo ansible-playbook -i ansible/inventory/cluster.yml \
       ansible/install.yml -t services --limit agmind_master
   ```

5. Verify:
   ```bash
   cat /etc/agmind/cluster.yaml | grep agmind-worker-03
   ```

## Removing a worker

1. Remove из inventory.
2. Re-run cluster role на master:
   ```bash
   sudo ansible-playbook -i ansible/inventory/cluster.yml \
       ansible/install.yml -t services --limit agmind_master
   ```

3. Optionally — uninstall на worker:
   ```bash
   ssh user@agmind-worker-XX 'agmind deploy down --volumes'
   ```

## Limitations (M1)

- **Sharded inference** (multi-host LLM partitioning) — не реализовано в M1
- **Auto-rebalancing** — workers не сбрасывают KV cache между nodes
- **Multi-master HA** — нет; single master cluster
- **mTLS между nodes** — не настроено (LAN trust); добавится в M2 если
  нужно cross-segment deployment

## Future enhancements (M2/M3)

- Tensor-parallel sharding через llama.cpp `--rpc` (multi-node sharded
  inference)
- KV cache replication для seamless failover
- Auto-discovery новых workers через mDNS без Ansible re-run
- mTLS между master and workers
