# AGmindx86

1. The single source of truth is `AGMIND_MIGRATION_SPEC.md`. Read it
   before any non-trivial action. The spec is informational/working —
   updates with recon-backed justification are encouraged.
2. Run preflight first: `python -m agmind doctor` or
   `make audit && python -m agmind status`. Stop on `fail`.
3. Surface assumptions; do not pick silently between interpretations.
4. Minimum code that solves the current task. Nothing speculative.
5. Touch only files in scope. Out-of-scope → `migration_progress.json::deferred`.
6. DoD is the only ground truth for "done". `make dod-phase-X` must return 0.
7. Do not edit (frozen): `scripts/audit_forbidden.py`,
   `migration_progress.json`. Spec may be edited with explicit recon
   trail.
8. Old AGmind installer (Bash+Docker-Compose for DGX Spark) lives under
   `legacy/gb10/` when git mv is available — currently `EXCLUDED_DIRS` in
   `scripts/audit_forbidden.py` covers the virtual quarantine.

Reply in Russian unless the user explicitly switches to English.

См. также:
- `docs/MIGRATION_PLAN.md` — план миграции и DoD по фазам
- `docs/HARDWARE.md` — host setup для AMD Strix Halo
- `.planning/research/x86-migration/` — recon-отчёты R0..R11
- `.planning/sessions/` — журналы рабочих сессий
