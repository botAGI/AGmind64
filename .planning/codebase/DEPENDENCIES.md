# AGmind Dependency Map

Last updated: 2026-05-23.

## Internal Tiers

### Tier 0 - foundations

These modules should not import high-level project modules:

- `agmind.log`
- `agmind._env`
- `agmind.i18n`
- `agmind.config.env`
- `agmind.compute.base`
- `agmind.compute.config`
- `agmind.schemas.service`

### Tier 1 - detection and primitives

- `agmind.compute.detect` depends on logging and host/system probes.
- `agmind.secrets` depends on logging and filesystem permissions.
- `agmind.models` depends on service YAML parsing and compute detection.
- `agmind.services.capability_bindings` is table-like and should remain light.

### Tier 2 - domain services

- `agmind.compute._registry` discovers backend entry points and imports backend
  classes lazily.
- `agmind.compute.backends.*` implement `Backend` and may import heavy runtime
  engines lazily from `_engines/`.
- `agmind.compute.clients.llama_server` is the HTTP client for running
  llama-server containers.
- `agmind.services.registry`, `renderer`, `compatibility` own service graph
  loading and compose generation.
- `agmind.install.*`, `deploy.*`, `ops.*`, `cluster.*`, `diagnostics.*` are
  workflow/domain modules.

### Tier 3 - user surface

- `agmind.cli.*` and `agmind.cli.tui.*` are leaf modules. They may import
  domain services lazily, but domain modules should not import CLI/TUI modules.
- `agmind.__main__` only delegates to `agmind.cli.app`.

## Heavy Dependency Rules

- `llama_cpp` imports stay inside engine methods.
- Torch imports stay in backend/runtime lanes, not import-time package paths.
- PyYAML is allowed when installed; `services.registry` keeps a tiny fallback
  parser for narrow legacy compatibility.
- Textual imports stay inside TUI modules.
- Ansible is invoked by install steps as a subprocess; Ansible modules should
  not become Python runtime dependencies.

## External Dependencies

### Core install

From `pyproject.toml`:

- `numpy`
- `typer`
- `rich`
- `questionary`
- `platformdirs`
- `pydantic`
- `ansible-core`
- `structlog`
- `textual`
- `pyfiglet`
- `PyYAML`
- `huggingface_hub`
- `zeroconf`

### Optional extras

- `cpu`: `torch`, `onnxruntime`, `llama-cpp-python`, `scipy`
- `vulkan`: `llama-cpp-python`, `scipy`
- `rocm`: `torch`, `onnxruntime`, `llama-cpp-python`, `scipy`
- `dev`: `pytest`, `pytest-asyncio`, `pytest-cov`,
  `pytest-benchmark`, `ruff`, `mypy`, `pre-commit`, type stubs

## CI Dependencies

The self-hosted CI assumes:

- system `python3` is Python 3.12 compatible
- `$HOME/.local/bin/uv`
- `$HOME/.local/bin/uvx`
- Docker daemon with access to `/dev/dri` and `/dev/kfd` on Strix Halo
- GitHub runner labels: `self-hosted`, `linux`, `x64`, `strix-halo`

The workflow intentionally does not use `actions/setup-python` for normal CI
jobs. The runner already has the needed Python, and setup-python toolcache
downloads stalled repeatedly.

## Import Cycle Policy

Keep the graph pointed upward:

```text
foundation -> domain -> orchestration -> CLI/TUI
```

Disallowed directions:

- domain module importing CLI/TUI
- schema/catalog module importing deploy/install
- backend registry importing heavy engine modules at import time
- test-only helpers imported by runtime code

## Update Checklist

When adding a dependency:

1. Decide whether it is core, optional extra, or dev-only.
2. Add a test that proves import without optional heavy deps still works.
3. Update `pyproject.toml`.
4. Update this file if the dependency changes a boundary.
5. Run `pre-commit`, `mypy`, and the relevant pytest marker set.
