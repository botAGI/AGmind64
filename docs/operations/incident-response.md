# Incident response runbook

Operational playbook for an AGmind deployment: how to triage, decide, and
recover when something breaks. This is scoped to a **single-host or small
self-hosted cluster** (homelab / lab), not an enterprise SLA — the response
times below are pragmatic targets, not contractual.

Fixes are intentionally **not duplicated** here. Decision-tree leaves link to the
matching section of [`docs/TROUBLESHOOTING.md`](../TROUBLESHOOTING.md), which
stays the single source of step-by-step fixes.

---

## 1. Severity matrix

AGmind alert rules carry exactly two `severity` labels — `critical` and
`warning` (Alertmanager routes `critical` to its dedicated receiver). Map them
onto four operator severities:

| Sev | Meaning | Target response | Signals (alerts / symptoms) |
|-----|---------|-----------------|------------------------------|
| **P1** | Total outage / data risk | ASAP | `HostOomKilled`, `AmdGpuTempHigh`, `LlamaServerDown`, host unreachable |
| **P2** | Key service degraded, not down | ~1h | `DiskSpaceLow` (trending), edge 502 (Traefik), a backend (postgres/redis) unreachable* |
| **P3** | Degraded performance / nearing a limit | same day | `ContainerRestartLoop`, `HighMemoryPressure`, `AmdGttUsageHigh`, `AmdGpuClockStuck`, `LlamaKvCacheNearFull`, `LlamaQueueBuildup`, `LlamaThroughputDropped` |
| **P4** | Cosmetic / advisory | when convenient | `agmind doctor` warnings (e.g. `kernel-version`, `gtt-pool` sub-optimal) |

\* **Coverage gap (be honest):** there is no `PostgresDown` / `RedisDown` /
`DifyDown` Prometheus rule today — only the `Llama*`, `Amd*`, and host/container
rules above. Database and edge-service P2 incidents are detected by **symptom**
(`agmind deploy status`, `agmind logs <svc>`), not a named alert. Adding those
rules is a worthwhile follow-up.

---

## 2. First response — triage

Run these first, in order. They are read-only.

```bash
agmind doctor --json        # host/GPU/Docker/kernel health, fix_hints
agmind status               # selected backend + device
agmind deploy status        # docker compose ps — what is up / restarting
agmind logs <service>       # recent logs for a suspect service
agmind estimate --profile <p>   # is the box over-committed? (mem cap vs RAM/GTT)
```

`agmind endpoints` lists per-service URLs once a stack is installed. Note: with
no live install (no `.env`) it currently errors rather than printing an empty
table — only use it against a deployed host.

---

## 3. Decision trees

### Container down or crash-looping (`ContainerRestartLoop`)

```text
agmind deploy status shows <svc> Restarting/Exited?
 ├─ agmind logs <svc>  → read the crash reason
 │   ├─ permission / EROFS / chown error  → host bind-mount perms
 │   │      → see TROUBLESHOOTING "Section 4: Docker / Compose"
 │   │      → (descriptor authors: the Правила Карпатого checklist)
 │   ├─ missing config / env              → re-run install to re-stage config
 │   └─ image / command error             → TROUBLESHOOTING "Section 4"
 └─ recovered after a restart?            → agmind deploy restart <svc>
```

### LLM / inference down (`LlamaServerDown`) or slow (`Llama*` warnings)

```text
llama-server unreachable or /health failing?
 ├─ DeviceLost / GPU fallback to CPU / Vulkan error
 │      → TROUBLESHOOTING "Section 5: LLM inference"
 │      → GPU not detected: "Section 1: Vulkan / GPU detection"
 ├─ LlamaKvCacheNearFull / LlamaQueueBuildup / LlamaThroughputDropped
 │      → context/batch pressure — "Section 5"; check GTT headroom below
 └─ still down → agmind deploy restart llama-llm ; agmind logs llama-llm
```

### GPU thermal / clock / GTT (`AmdGpuTempHigh`, `AmdGpuClockStuck`, `AmdGttUsageHigh`)

```text
AmdGpuTempHigh (P1)   → reduce load / improve cooling immediately
AmdGpuClockStuck      → TROUBLESHOOTING "Section 2: ROCm / HIP"
AmdGttUsageHigh       → unified-memory pressure
       → agmind estimate --profile <p>  (cap sum vs GTT pool)
       → TROUBLESHOOTING "Section 3: GTT memory"
```

### Disk filling up (`DiskSpaceLow`)

```text
DiskSpaceLow firing?
 ├─ reclaim space (this IS the alert's fix_hint):
 │      agmind gc --dry-run            # preview
 │      agmind gc --aggressive         # prune stopped containers + dangling
 │      agmind gc --include-models     # also drop unused model files
 └─ still low → check /var/lib/agmind (volumes, models, backups)
```

### Failed deploy / bad update

```text
A deploy failed its post-apply healthcheck?
 ├─ AGmind auto-rolls back to the pre-deploy snapshot on healthcheck failure.
 └─ to roll back manually:
        agmind snapshots list          # newest first
        agmind rollback [snapshot]     # default: latest pre-deploy snapshot
```

---

## 4. Recovery procedures

**Roll back a bad change**

```bash
agmind snapshots list
agmind rollback            # or: agmind rollback <snapshot-id>
```

**Restore from a backup** (after data loss / corruption)

```bash
agmind backup-verify <archive.tar.gz>   # integrity pre-check (sha256 + opens)
agmind restore <archive.tar.gz>         # config; --include-data was set at backup time
```

**Full reinstall** (host rebuilt / clean slate) — re-run `agmind install` with
the same profile/domain; prestaged models in `/var/lib/agmind/models` are
re-detected and not re-downloaded.

---

## 5. Post-incident

1. **Verify** the fix held: `agmind verify install --domain <d>`, then confirm
   the firing alert cleared and `agmind deploy status` is clean.
2. **Document** the timeline and root cause.
3. **Prevent** recurrence: where the gap was a missing signal, add a Prometheus
   alert rule (see `templates/observability/prometheus/rules/`) so next time it
   pages instead of being found by hand — especially the DB/edge-service gap
   noted in the severity matrix.
4. **Test** the recovery path you used so it is known-good before the next event.
