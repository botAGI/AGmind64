# AGmind Extension Points

Где добавлять новые engines / services / tiers / strategies — карта
для contributors.

## E.1 Add new compute backend

**Когда:** новое железо (ARM x86_64 emulation? Intel iGPU?).

**Files to touch:**
1. Create `agmind/compute/backends/<name>.py` extending `Backend`
2. Add to `agmind/compute/_registry.py::_load_backends()` (~5 lines)
3. Add to `_BACKEND_PRIORITY` tuple
4. Update `agmind/compute/config.py::_ALLOWED_BACKENDS`
5. Add Dockerfile if needed: `docker/Dockerfile.<name>`
6. Add to CI matrix `.github/workflows/ci.yml::docker-build.strategy.matrix.backend`
7. Tests: `tests/compute/test_contract.py` (add marker `backend_<name>`),
   `pyproject.toml::[tool.pytest.ini_options]::markers`

**Example:** Intel Arc GPU support (когда станет relevant):
```python
class IntelArcBackend(Backend):
    name = "intel"
    @classmethod
    def available(cls) -> bool:
        return Path("/sys/class/drm/card*/device/vendor").exists() and ...
```

## E.2 Add new engine inside existing backend

**Когда:** new inference engine (SGLang ROCm, MLC-LLM Vulkan, LightLLM).

**Files to touch:**
1. Create `agmind/compute/backends/_engines/<engine>_<backend>.py`
2. Update `agmind/compute/backends/<backend>.py::_M2_ENGINES` или
   `_SUPPORTED_ENGINES`
3. Update `agmind/compute/config.py::_ALLOWED_ENGINES`
4. Update routing matrix в `AGMIND_MIGRATION_SPEC.md §1.2.6`
5. ADR: `docs/adr/NNNN-engine-<name>.md`

**Example:** vLLM ROCm M2 engine:
```python
# agmind/compute/backends/_engines/vllm_rocm.py
class VLLMROCmEngine:
    def load(self, model_path, **kwargs) -> LLMHandle: ...
    def embed(self, texts, model) -> list[list[float]]: ...
    def rerank(self, query, docs) -> list[float]: ...
```

В `rocm.py`:
```python
_M2_ENGINES = frozenset({"vllm", "infinity"})
# Сейчас raises NotImplementedError; заменить на real impl.
```

## E.3 Add new service в stack

**Когда:** new container (новый vector store, new monitoring agent).

**Files to touch:**
1. `templates/services.yaml` — add entry под correct `profiles:`
2. Pin image:tag + digest (verify amd64 manifest!)
3. Update `docs/adr/0002-compute-backend-abstraction.md` если architecture-relevant
4. Tests `tests/services/test_registry.py::test_each_service_*` auto-cover

**Example:** add Caddy as alternative proxy:
```yaml
caddy:
  image: caddy:2.11.3-alpine
  digest: 86deaf5...
  profiles: [core-caddy]
  purpose: Auto-HTTPS reverse proxy alternative
```

User selects через `agmind_proxy: caddy` в Ansible group_vars.

## E.4 Add new LLM tier / model

**Когда:** new tier (XXXL для 256+ GB?), new model (Llama 5 release).

**Files to touch:**
1. `templates/models.yaml::llm_tiers` — add tier OR new model в existing
2. Update `agmind/models.py::_TIER_RAM_THRESHOLDS_GB` if new tier
3. Update Ansible `roles/models/tasks/main.yml::Auto-select model tier per RAM`
4. Update `agmind/compute/detect.py::detect_host` если new GPU class
5. `tests/test_models.py` auto-covers tier matrix

**Antipattern check:** add to `models.yaml::antipatterns` если known issue.

## E.5 Add new routing strategy

**Files to touch:**
1. `agmind/cluster/router.py::RoutingStrategy` enum — new value
2. `agmind/cluster/router.py::choose_peer()` — add branch
3. `tests/cluster/test_router.py` — new test
4. `docs/CLUSTER.md` — document strategy

**Example:** "least-latency" — track p99 latency per peer:
```python
class RoutingStrategy:
    LEAST_LATENCY = "least-latency"

def choose_peer(...):
    if strategy == LEAST_LATENCY:
        return min(alive, key=lambda h: h.last_latency_p99).peer
```

PeerHealth должен tracking last_latency_p99 — add field.

## E.6 Add new Ansible role

**Когда:** new bootstrap step (e.g., GPU monitoring agent, K8s integration).

**Files to touch:**
1. Create `ansible/roles/<role>/{tasks,handlers,templates,defaults}/`
2. Add к `ansible/install.yml` с appropriate tags
3. Update `tests/test_ansible_layout.py::role` parametrize list
4. Update `docs/INSTALL.md` если user-facing

## E.7 Add new CLI command

**Files to touch:**
1. Create `agmind/cli/<command>_cmd.py` (по примеру `models_cmd.py`)
2. Wire в `agmind/cli/__init__.py::_make_app()` — add `@app.command()` либо
   sub-app
3. Tests `tests/test_cli.py` (with `typer.testing.CliRunner` если typer installed)
4. Docs: `docs/QUICKSTART.md` или `docs/INSTALL.md`

## E.8 Add new doctor check

**Files to touch:**
1. `agmind/diagnostics/doctor.py` — new `_check_X()` function returning `CheckResult`
2. Add к `_CHECKS` tuple
3. `tests/diagnostics/test_doctor.py::test_run_preflight_specific_checks_present`
   — add name к expected set

## E.9 Add new i18n language

1. Create `agmind/i18n/<lang>.json` с keys равными en.json
2. Update `agmind/i18n/__init__.py::_LANG_FILES`
3. Test fallback в `tests/test_i18n.py`

## E.10 Add new audit rule

**Files to touch:**
1. `scripts/audit_forbidden.py::RULES` — new (id, description, regex)
2. Add `# audit: allow rule-self-reference` к regex string
3. ADR if это new prohibited pattern (architectural decision)
4. `tests/test_audit_script.py` — positive detection test

## E.11 Add new service profile

**Когда:** logical grouping для opt-in (e.g., "code-server", "n8n").

**Files to touch:**
1. `agmind/services/registry.py::ServiceProfile` enum — new value
2. `templates/services.yaml` — assign services к new profile
3. `agmind_profiles` default в `ansible/group_vars/all.yml`
4. `agmind/services/registry.py::services_for_profile` auto-handles

## E.12 Add new recon report

**Когда:** новая фича / external dep / architectural decision.

**Files to touch:**
1. Create `.planning/research/x86-migration/R-<topic>.md` или `R<N>-<topic>.md`
2. Frontmatter (recon/date/status/source_agent/related)
3. TL;DR + sections + sources с URLs + verification markers (verified/unverified/inferred)
4. Update `docs/MIGRATION_PLAN.md::§8 Ресерчи` table

## E.13 Update `AGMIND_MIGRATION_SPEC.md`

**When:** finalized architecture decision, разрешено править (informational).

**Process:**
1. Edit spec sections
2. Append к Changelog block в top
3. ADR cross-reference
4. Update `.planning/STATE.md::Key decisions log`

## E.14 Hook integration points (для AI agents)

- `.claude/agents/` — Claude Code subagent definitions (legacy AGmind had
  deploy-verifier — можно создать аналог для x86)
- `.claude/commands/` — slash commands (legacy had shellcheck/stack-status/verify)
- MCP server integration — not yet, planned M3 (см. BACKLOG.md::N1)

## E.15 New tier defaults

Auto-tier detection thresholds в `agmind/models.py::_TIER_RAM_THRESHOLDS_GB`.
Если адjust — `tests/test_models.py::test_detect_tier_*` adjustments
required.

---

## Anti-extension points (DON'T extend без discussion)

- `AGMIND_MIGRATION_SPEC.md::§1.3` запреты — adding to "broken на gfx1151"
  list требует recon + ADR
- `scripts/audit_forbidden.py::RULES` — frozen invariant
- `agmind/compute/base.py::LLMHandle` — ABC additions = breaking changes
- `migration_progress.json::frozen_files` — SHA256 protected

## Tips для contributors

- Run `make audit` перед commit
- Run `pytest -m backend_any` после Python changes
- Run `ansible-playbook install.yml --check` после Ansible changes
- ADR-first для architectural decisions
- Recon-first для new external deps
- Tests-first (TDD) для new public API
