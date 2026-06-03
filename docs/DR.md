# Disaster recovery (RPO / RTO + scenarios)

How to recover an AGmind deployment, and how to prove the recovery works *before*
you need it.

- **RPO (recovery point objective)** — how much data you can afford to lose. It
  equals your backup cadence. Config-only `agmind backup` is cheap (run it before
  every change); `agmind backup --include-data` (DB dumps + volumes) is larger —
  schedule it to match your tolerance (e.g. daily → RPO ≤ 24h).
- **RTO (recovery time objective)** — how long recovery takes. Measure it with
  `agmind ops dr-drill` (below) so the number is real, not a guess.

## Drill it (no real disaster needed)

```bash
agmind ops dr-drill --install-dir /opt/agmind
```

Runs backup → integrity-verify → **sandbox** restore (into a throwaway dir, never
the live install) and reports per-step status + measured RTO. It aborts if the
backup is corrupt — catching an unusable backup *before* a real outage. On a
disposable host you can also exercise the live path:

```bash
agmind ops dr-drill --no-skip-restore   # also live-restore + health (mutates the stack)
```

## Scenarios

| # | Scenario | RPO source | Recover with |
|---|----------|------------|--------------|
| 1 | Bad deploy / config regression | pre-deploy snapshot | `agmind snapshots list` → `agmind rollback [snap]` (auto-rolls back on failed healthcheck) |
| 2 | Lost / corrupted config (.env, compose) | last `agmind backup` | `agmind backup-verify <a>` → `agmind restore <a>` (or `--label env`) |
| 3 | Database / volume data loss | last `--include-data` backup | `agmind backup-verify <a>` → `agmind restore <a>` (data tier replays pg_dump/volume tars) |
| 4 | Full host loss / rebuild | backup archive (off-host) | reinstall (`agmind install`) → `agmind restore <a>`; prestaged GGUFs in `/var/lib/agmind/models` are reused |
| 5 | Secret compromise | n/a (rotate, don't restore) | `agmind ops rotate-secrets` (rotatable bucket; `--include` for init-only + in-DB reset) |

### Notes per scenario

1. **Bad deploy** — the deploy path snapshots before applying and auto-rolls back
   if the post-apply healthcheck fails. To roll back manually, pick a snapshot
   from `agmind snapshots list`.
2. **Config loss** — always `agmind backup-verify` first (sha256 + opens); a
   selective `agmind restore --label env` restores just the runtime secrets.
3. **Data loss** — only a `--include-data` backup carries DB dumps + volumes;
   confirm with `backup-verify` (per-dump sha256), then restore. Granular:
   `agmind restore --label <category>`.
4. **Host loss** — keep the backup archive OFF the host. After a clean
   `agmind install`, restore config + data; the model files are large and live
   outside the archive (re-pull or copy separately).
5. **Secret compromise** — do NOT restore an old .env (it has the compromised
   secret). Rotate: the `rotatable` bucket is safe by default; `init_only`
   (postgres/minio) needs `--include` plus the in-DB password reset; encrypt-at-rest
   keys are refused unless `--force-destructive` (rotating them destroys data).

## Post-recovery

Verify the stack: `agmind verify install --domain <d>`, then `agmind deploy status`
should be clean and any firing alert should clear. Re-run `agmind ops dr-drill`
periodically so the backup + RTO stay known-good.
