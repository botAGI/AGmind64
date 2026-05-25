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
Use `agmind ci status` as the first local check before changing workflow
concurrency or runner labels; it reports recent Actions runs and runner
online/busy state through the local `gh` CLI.

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

### P17. Proxmox exporter remote scrape needs relabeling

`prometheus-pve-exporter` exposes Proxmox metrics on `/pve`. When it is not
running directly on the Proxmox host, Prometheus must set `__param_target`
through relabeling. The opt-in descriptor provides labels for local scrape
shape, but remote Proxmox deployments still need the example scrape job until
managed remote scrape provisioning exists.

The exporter also bind-mounts `/etc/agmind/proxmox-exporter/pve.yml`. If that
file is missing, Compose/Docker can turn the source into a directory and the
container will fail in a confusing way. Keep the services role guard in place:
token vars or `agmind_proxmox_exporter_existing_config=true` are required
before compose up. Keep `scripts/proxmox_exporter_check.py` in the pre-compose
path so bad placeholders, password auth, and malformed YAML fail before
containers are touched.

Do not mark a tool candidate `accepted` as a documentation-only act. Accepted
service-profile candidates are runtime artifacts and must satisfy
`scripts/tool_candidate_check.py` and `agmind tools validate`
descriptor/owner/pin/profile/port checks. Keep the CI
`tool-candidate-validate` job and the broad pre-commit trigger, since service
or component edits can break accepted candidates without touching
`templates/tool-candidates/`.

### P18. Deploy target paths drift quietly

Deployment target contracts are easy to make stale because they point at
provisioner modules and playbooks outside their own directory. Keep
`agmind targets validate` and `scripts/deploy_target_check.py` in local/CI
checks for supported and experimental lanes. It already caught the obsolete
`ansible/playbooks/site.yml` reference; the current playbook source of truth is
`ansible/install.yml`.

### P19. Aggregate governance must not hide failures

`agmind governance validate` is for convenience. Keep individual check output
visible in the aggregate report and keep separate CI jobs for visibility. A
single opaque "governance failed" result makes the operator debug loop worse.
The pre-commit aggregate hook should stay narrow to aggregate wrapper files;
the broad hooks for components, deploy targets, tools, and constraints already
cover catalog drift.

## Process Traps

### P20. Historical Claude notes are not live instructions

Old session/research notes can mention Claude. Live config belongs in
`.planning/` and current repo docs, not `.claude/`.

### P21. Green local tests are not enough for this project

For CI-affecting work, collect self-hosted evidence:

- standard gates
- Docker backend matrix
- Strix runtime smoke

### P22. Do not fix the runner by hiding failures

If a job fails because the workflow shape is wrong, fix the workflow. Avoid
marking required jobs as optional unless there is a clear operator decision.
