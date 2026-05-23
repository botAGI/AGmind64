# AGmind Pitfalls

Last updated: 2026-05-23.

## Recent CI / Migration Traps

### P1. Self-hosted `actions/setup-python` can stall

Observed on 2026-05-22: setup-python/toolcache/cache restore repeatedly stalled
around 94 percent on the self-hosted runner. The CI workflow now uses system
`python3` and preinstalled `$HOME/.local/bin/uv`/`uvx`.

### P2. Host `test-cpu` must not install `.[cpu]`

The `cpu` extra pulls `llama-cpp-python`, which may trigger native CMake builds
on the host runner. Keep host CI on `.[dev]`; backend-native builds are covered
by Docker image lanes.

### P3. Docker buildx cannot see daemon-local base tags

The old buildx action could not see `agmind-base:ci`. CI now uses daemon
`docker build` for the backend matrix.

### P4. Runtime images are not dev images

`agmind-vulkan:ci` and `agmind-rocm:ci` have entrypoint
`python3 -m agmind` and do not include pytest. Strix smoke must override
entrypoint and run a runtime backend check.

### P5. Git file mode matters

`scripts/amdgpu_textfile.sh` was executable locally but tracked as `100644`.
GitHub checkout lost the executable bit and tests failed. Use
`git update-index --chmod=+x <file>` when needed.

### P6. Broad `.gitignore` patterns can hide real source

`models/` ignored `ansible/roles/models/`. It is now `/models/`. Keep ignore
patterns rooted when they are intended for top-level artifacts.

### P7. Dependabot/release-drafter can steal the runner

Dependabot PR runs and `release-drafter` jobs repeatedly occupied the only
self-hosted runner while the develop CI was queued. The next workflow cleanup
should keep non-critical PR-target automation away from the Strix runner.

### P8. Ubuntu package names drift

`shaderc` is not a valid Ubuntu 24.04 package name in this context; use
`libshaderc-dev` while keeping `glslc`/shader tooling installed.

## Hardware / Runtime Traps

### P9. AMDVLK silently breaks large Vulkan models

RADV is required. AMDVLK has memory behavior that breaks AGmind's target
workloads.

### P10. ROCm wheels must match gfx1151 reality

Stock ROCm wheels can fail or silently underperform on Strix Halo. Use the
documented ROCm nightly/gfx1151 path in Docker builds.

### P11. Kernel and GTT pool affect usable memory

`agmind doctor` currently reports host tuning warnings for kernel/GTT pool.
Treat these as deployment-readiness work, not unit-test failures.

### P12. BIOS UMA should not be oversized on Linux

Linux can manage GTT; large static UMA reduces CPU-usable RAM without solving
the real allocation problem.

### P13. Rootless Docker is not a ROCm path

ROCm device access needs rootful Docker/cgroups support on this host class.

## Service / Product Traps

### P14. Service graph drift is easy

When adding a service, update descriptor fields and capability bindings
together. Schema validation only proves shape, not compatibility intent.

### P15. Heavy ML should be sidecars, not huge Dify plugins

Research shows Dify plugin package limits and runtime constraints make heavy
ML plugins fragile. Prefer sidecar services plus a thin tool plugin.

### P16. Dashboard config is not dashboard provision

Prometheus/Loki/Grafana config exists, but real Grafana dashboard JSON is still
backlog work.

## Process Traps

### P17. Historical Claude notes are not live instructions

Old session/research notes can mention Claude. Live config belongs in
`.planning/` and current repo docs, not `.claude/`.

### P18. Green local tests are not enough for this project

For CI-affecting work, collect self-hosted evidence:

- standard gates
- Docker backend matrix
- Strix runtime smoke

### P19. Do not fix the runner by hiding failures

If a job fails because the workflow shape is wrong, fix the workflow. Avoid
marking required jobs as optional unless there is a clear operator decision.
