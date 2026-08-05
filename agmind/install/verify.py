"""Fresh-install verification gate for AGmind.

This module turns the manual clean-host proof into a reusable API:
runtime env generation, setup-time service expansion, deploy dry-run,
Docker Compose config validation, and Ansible bootstrap syntax checks.
It is intentionally non-destructive: no apt changes, no image pulls, no model
downloads, and no Compose apply.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agmind.components import load_component_contracts
from agmind.core.env import parse_env_file
from agmind.deploy.runner import deploy
from agmind.install.ansible_tools import resolve_ansible_command
from agmind.install.orchestrator import DEFAULT_REPO_ROOT, InstallConfig
from agmind.install.secrets_audit import find_weak_secret_envs
from agmind.install.steps import EnvWriteStep, _image_digest, _image_tag, _version_key
from agmind.services.renderer import load_descriptors, render_to_string
from agmind.services.selection import resolve_service_selection

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

REQUIRED_RUNTIME_ENV_KEYS = (
    "POSTGRES_PASSWORD",
    "GRAFANA_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "N8N_ENCRYPTION_KEY",
    "N8N_TIMEZONE",
    "HOMARR_SECRET_ENCRYPTION_KEY",
    "DIFY_PLUGIN_DAEMON_KEY",
    "DIFY_PLUGIN_INNER_API_KEY",
)


@dataclass(frozen=True)
class InstallVerifyScenario:
    """One setup-time service selection to prove as deployable."""

    name: str
    services: tuple[str, ...]


DEFAULT_SCENARIOS: tuple[InstallVerifyScenario, ...] = (
    # Mirrors setup_wizard._DEFAULT_SERVICES: LOCAL by default (ratified 2026-07-17,
    # P0.3 / 15-04) — no traefik, no public routes.
    InstallVerifyScenario(
        "setup-default",
        (
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "grafana",
            "prometheus",
            "loki",
            "alloy",
            "node-exporter",
            "alertmanager",
            "uptime-kuma",
            "homarr",
            "watchtower",
            "dozzle",
            "netdata",
        ),
    ),
    # The traefik (public edge) scenarios below carry authelia + redis: chain-llm /
    # chain-internal routes forwardAuth to authelia and the renderer fail-closes a traefik
    # render without it (P0.3 / 15-04). They are the public-posture fixtures; setup-default
    # above is the local one.
    InstallVerifyScenario(
        "core-rag",
        (
            "dify-api",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    InstallVerifyScenario(
        "core-ragflow",
        (
            "ragflow",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    InstallVerifyScenario(
        "core-rag-ragflow",
        (
            "dify-api",
            "ragflow",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    InstallVerifyScenario(
        "automation-observability",
        (
            "n8n",
            "llama-llm",
            "qdrant",
            "grafana",
            "prometheus",
            "loki",
            "alloy",
            "node-exporter",
            "alertmanager",
            "uptime-kuma",
            "homarr",
            "watchtower",
            "dozzle",
            "netdata",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    InstallVerifyScenario(
        "explicit-dify-ragflow-milvus",
        (
            "dify-api",
            "ragflow",
            "milvus",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
)


@dataclass(frozen=True)
class InstallVerifyCheck:
    """Global check result outside a single service-selection scenario."""

    name: str
    ok: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "message": self.message,
            "details": self.details,
        }


@dataclass(frozen=True)
class InstallVerifyScenarioResult:
    """Verification result for one setup scenario."""

    name: str
    ok: bool
    services: int
    env_key_count: int
    deploy_changes: int
    message: str
    selected_services: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "services": self.services,
            "env_key_count": self.env_key_count,
            "deploy_changes": self.deploy_changes,
            "message": self.message,
            "selected_services": list(self.selected_services),
        }


@dataclass(frozen=True)
class InstallVerifyReport:
    """Aggregate fresh-install verification result."""

    checks: tuple[InstallVerifyCheck, ...]
    scenarios: tuple[InstallVerifyScenarioResult, ...]
    work_dir: str

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks) and all(
            scenario.ok for scenario in self.scenarios
        )

    @property
    def summary(self) -> dict[str, int]:
        failed = sum(1 for check in self.checks if not check.ok) + sum(
            1 for scenario in self.scenarios if not scenario.ok
        )
        return {
            "check_count": len(self.checks),
            "scenario_count": len(self.scenarios),
            "failed_count": failed,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "work_dir": self.work_dir,
            "checks": [check.to_json() for check in self.checks],
            "scenarios": [scenario.to_json() for scenario in self.scenarios],
        }


def verify_install(
    *,
    domain: str = "lab.example.com",
    scenarios: Sequence[InstallVerifyScenario] = DEFAULT_SCENARIOS,
    include_ansible: bool = True,
    include_compose: bool = True,
    install_collections: bool = True,
    timeout_seconds: int = 240,
    work_dir: Path | None = None,
    run: CommandRunner = subprocess.run,
) -> InstallVerifyReport:
    """Run non-destructive checks that prove a clean install can be rendered.

    The runner may create ignored local artifacts under `ansible/.galaxy` when
    `install_collections=True`, matching what `agmind setup` does before
    running the bootstrap playbook.
    """

    if work_dir is None:
        with tempfile.TemporaryDirectory(prefix="agmind-install-verify-") as tmp:
            return _verify_install_in_dir(
                Path(tmp),
                domain=domain,
                scenarios=scenarios,
                include_ansible=include_ansible,
                include_compose=include_compose,
                install_collections=install_collections,
                timeout_seconds=timeout_seconds,
                run=run,
            )

    work_dir.mkdir(parents=True, exist_ok=True)
    return _verify_install_in_dir(
        work_dir,
        domain=domain,
        scenarios=scenarios,
        include_ansible=include_ansible,
        include_compose=include_compose,
        install_collections=install_collections,
        timeout_seconds=timeout_seconds,
        run=run,
    )


def _verify_install_in_dir(
    work_dir: Path,
    *,
    domain: str,
    scenarios: Sequence[InstallVerifyScenario],
    include_ansible: bool,
    include_compose: bool,
    install_collections: bool,
    timeout_seconds: int,
    run: CommandRunner,
) -> InstallVerifyReport:
    checks: list[InstallVerifyCheck] = []
    scenario_results: list[InstallVerifyScenarioResult] = []

    descriptors = load_descriptors()
    contracts = load_component_contracts()
    if not descriptors:
        checks.append(InstallVerifyCheck("service-descriptors", False, "no service descriptors"))
    else:
        checks.append(
            InstallVerifyCheck(
                "service-descriptors",
                True,
                f"{len(descriptors)} descriptors loaded",
                {"count": len(descriptors)},
            )
        )

    if include_ansible:
        checks.extend(
            _run_ansible_checks(
                domain=domain,
                install_collections=install_collections,
                timeout_seconds=timeout_seconds,
                run=run,
            )
        )

    for scenario in scenarios:
        scenario_results.append(
            _verify_scenario(
                scenario,
                domain=domain,
                root_dir=work_dir,
                descriptors=descriptors,
                contracts=contracts,
                include_compose=include_compose,
                timeout_seconds=timeout_seconds,
                run=run,
            )
        )

    return InstallVerifyReport(
        checks=tuple(checks),
        scenarios=tuple(scenario_results),
        work_dir=str(work_dir),
    )


def _run_ansible_checks(
    *,
    domain: str,
    install_collections: bool,
    timeout_seconds: int,
    run: CommandRunner,
) -> list[InstallVerifyCheck]:
    ansible_dir = DEFAULT_REPO_ROOT / "ansible"
    checks: list[InstallVerifyCheck] = []

    if install_collections:
        requirements = ansible_dir / "requirements.yml"
        galaxy_dir = ansible_dir / ".galaxy"
        completed = _run_command(
            [
                resolve_ansible_command("ansible-galaxy"),
                "collection",
                "install",
                "-r",
                str(requirements),
                "-p",
                str(galaxy_dir),
            ],
            cwd=ansible_dir,
            timeout_seconds=timeout_seconds,
            run=run,
        )
        checks.append(
            _completed_to_check(
                "ansible-collections",
                completed,
                success_message="collections ready",
            )
        )
        if completed.returncode != 0:
            return checks

    env = os.environ.copy()
    env["ANSIBLE_CONFIG"] = "ansible.cfg"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix="agmind-verify-vars-",
        suffix=".json",
    ) as vars_file:
        json.dump(
            {
                "agmind_domain": domain,
                "agmind_cf_api_token": "a" * 40,
            },
            vars_file,
        )
        vars_file.write("\n")
        vars_file.flush()
        os.chmod(vars_file.name, 0o600)
        completed = _run_command(
            [
                resolve_ansible_command("ansible-playbook"),
                "-i",
                "inventory/hosts.yml",
                "install.yml",
                "--syntax-check",
                "-e",
                f"@{vars_file.name}",
            ],
            cwd=ansible_dir,
            timeout_seconds=timeout_seconds,
            run=run,
            env=env,
        )
    checks.append(
        _completed_to_check(
            "ansible-syntax",
            completed,
            success_message="install.yml syntax OK",
        )
    )
    return checks


def _verify_scenario(
    scenario: InstallVerifyScenario,
    *,
    domain: str,
    root_dir: Path,
    descriptors: dict[str, Any],
    contracts: dict[str, Any],
    include_compose: bool,
    timeout_seconds: int,
    run: CommandRunner,
) -> InstallVerifyScenarioResult:
    selected = resolve_service_selection(
        descriptors,
        services=scenario.services,
        component_contracts=contracts,
    )
    selected_names = tuple(sorted(selected))
    install_dir = root_dir / scenario.name / "install"
    cfg = InstallConfig(
        domain=domain,
        cf_api_token="a" * 40,
        services=list(selected_names),
        install_dir=install_dir,
        models_dir=root_dir / scenario.name / "models",
        config_dir=root_dir / scenario.name / "config",
        model_file="model.gguf",
        embed_file="bge-m3-Q8_0.gguf",
        rerank_file="bge-reranker-v2-m3-Q8_0.gguf",
    )

    env_result = EnvWriteStep().run(lambda _event: None, cfg)
    if not env_result.success:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=0,
            deploy_changes=0,
            message=f"env write failed: {env_result.message}",
            selected_services=selected_names,
        )

    env_file = install_dir / ".env"
    try:
        duplicate_env_keys = _duplicate_env_keys(env_file)
        parsed = parse_env_file(env_file)
        env_mode = env_file.stat().st_mode & 0o777
    except OSError as exc:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=0,
            deploy_changes=0,
            message=f"runtime .env access failed: {exc}",
            selected_services=selected_names,
        )
    if duplicate_env_keys:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message=f"duplicate runtime env keys: {', '.join(duplicate_env_keys)}",
            selected_services=selected_names,
        )
    missing = [key for key in REQUIRED_RUNTIME_ENV_KEYS if not parsed.get(key)]
    if missing:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message=f"missing runtime env keys: {', '.join(missing)}",
            selected_services=selected_names,
        )
    runtime_value_errors = _validate_runtime_env_values(parsed, cfg)
    if runtime_value_errors:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message="; ".join(runtime_value_errors),
            selected_services=selected_names,
        )

    selected_descriptors = {n: descriptors[n] for n in selected_names if n in descriptors}
    weak_secret_errors = find_weak_secret_envs(selected_descriptors, parsed)
    if weak_secret_errors:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message="weak/default secrets: " + "; ".join(weak_secret_errors),
            selected_services=selected_names,
        )

    if env_mode != 0o600:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message=f".env mode must be 0600, got {env_mode:o}",
            selected_services=selected_names,
        )

    runtime_secret_errors = _validate_runtime_secret_files(cfg, selected_names, parsed)
    if runtime_secret_errors:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message="; ".join(runtime_secret_errors),
            selected_services=selected_names,
        )

    runtime_config_errors = _validate_runtime_config_files(cfg, selected_names)
    if runtime_config_errors:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message="; ".join(runtime_config_errors),
            selected_services=selected_names,
        )

    version_env_errors = _validate_version_manifest(
        install_dir / "version.env",
        selected_names,
        descriptors,
    )
    if version_env_errors:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message="; ".join(version_env_errors),
            selected_services=selected_names,
        )

    deploy_logger = logging.getLogger("agmind.deploy.runner")
    previous_level = deploy_logger.level
    deploy_logger.setLevel(logging.WARNING)
    try:
        deploy_result = deploy(
            profiles=[],
            install_dir=install_dir,
            domain=domain,
            apply=False,
            no_prompt=True,
            services=list(selected_names),
        )
    finally:
        deploy_logger.setLevel(previous_level)
    if not deploy_result.success:
        return InstallVerifyScenarioResult(
            name=scenario.name,
            ok=False,
            services=len(selected_names),
            env_key_count=len(parsed),
            deploy_changes=0,
            message=f"deploy dry-run failed: {deploy_result.message}",
            selected_services=selected_names,
        )

    changes = deploy_result.diff.total_changes if deploy_result.diff is not None else 0
    if include_compose:
        # traefik_enabled selection-derived (P0.3 / 15-04): a local scenario (no traefik)
        # renders without routing labels; a public one (traefik selected) requires authelia.
        compose_text = render_to_string(
            services=list(selected_names),
            domain=domain,
        )
        compose_file = install_dir / "docker-compose.yml"
        try:
            compose_file.write_text(compose_text, encoding="utf-8")
        except OSError as exc:
            return InstallVerifyScenarioResult(
                name=scenario.name,
                ok=False,
                services=len(selected_names),
                env_key_count=len(parsed),
                deploy_changes=changes,
                message=f"compose render write failed: {exc}",
                selected_services=selected_names,
            )
        completed = _run_command(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "config",
                "--quiet",
            ],
            cwd=install_dir,
            timeout_seconds=timeout_seconds,
            run=run,
        )
        if completed.returncode != 0:
            return InstallVerifyScenarioResult(
                name=scenario.name,
                ok=False,
                services=len(selected_names),
                env_key_count=len(parsed),
                deploy_changes=changes,
                message=_stderr_or_stdout(completed) or "docker compose config failed",
                selected_services=selected_names,
            )
        completed = _run_command(
            [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose_file),
                "pull",
                "--dry-run",
                "--policy",
                "missing",
                "--quiet",
            ],
            cwd=install_dir,
            timeout_seconds=timeout_seconds,
            run=run,
        )
        if completed.returncode != 0:
            return InstallVerifyScenarioResult(
                name=scenario.name,
                ok=False,
                services=len(selected_names),
                env_key_count=len(parsed),
                deploy_changes=changes,
                message=_stderr_or_stdout(completed) or "docker compose pull dry-run failed",
                selected_services=selected_names,
            )

    return InstallVerifyScenarioResult(
        name=scenario.name,
        ok=True,
        services=len(selected_names),
        env_key_count=len(parsed),
        deploy_changes=changes,
        message="env, dry-run, compose config, and image pull dry-run OK"
        if include_compose
        else "env and dry-run OK",
        selected_services=selected_names,
    )


def _validate_runtime_env_values(parsed: dict[str, str], config: InstallConfig) -> list[str]:
    expected = {
        "AGMIND_DOMAIN": config.domain,
        "AGMIND_MODEL_FILE": config.model_file or "",
        "AGMIND_LLM_CTX_SIZE": str(config.ctx_size),
        "AGMIND_LLM_KV_CACHE": config.kv_cache_type,
        "AGMIND_LLM_THREADS": str(config.threads),
        "AGMIND_LLM_PARALLEL": str(config.parallel_slots),
        "AGMIND_CTX_SIZE": str(config.ctx_size),
        "AGMIND_KV_CACHE": config.kv_cache_type,
        "AGMIND_THREADS": str(config.threads),
        "AGMIND_PARALLEL": str(config.parallel_slots),
        "AGMIND_EMBED_FILE": config.embed_file or "",
        "AGMIND_EMBED_CTX_SIZE": str(config.embed_ctx_size),
        "AGMIND_EMBED_KV_CACHE": config.embed_kv_cache,
        "AGMIND_EMBED_PARALLEL": str(config.embed_parallel),
        "AGMIND_EMBED_BATCH": str(config.embed_batch),
        "AGMIND_RERANK_FILE": config.rerank_file or "",
        "AGMIND_RERANK_CTX_SIZE": str(config.rerank_ctx_size),
        "AGMIND_RERANK_BATCH": str(config.rerank_batch),
        "MINIO_ROOT_USER": "agmind",
        "N8N_TIMEZONE": "UTC",
    }

    errors: list[str] = []
    for key, expected_value in expected.items():
        actual_value = parsed.get(key)
        if actual_value != expected_value:
            errors.append(f"runtime env key {key} must be {expected_value!r}, got {actual_value!r}")
    return errors


def _authelia_secret_parity_errors(
    secret_dir: Path,
    selected_names: tuple[str, ...],
    parsed: dict[str, str],
) -> list[str]:
    """Flag any authelia ``_FILE`` secret that has drifted from its .env source (P1 secrets-authelia).

    authelia reads these secrets from 0600 FILES materialized from .env; only ``agmind install`` and
    ``agmind ops rotate-secrets`` re-materialize them. A ``deploy`` / manual ``docker compose up``
    after a .env change (especially REDIS_PASSWORD) leaves the file stale, so authelia keeps sending
    the old redis password → WRONGPASS → authelia FATAL → stack-wide auth outage. Verify each file
    still matches its .env value so the desync is caught by ``agmind verify install`` before a deploy.
    """
    if "authelia" not in selected_names:
        return []
    from agmind.install.secret_keys import AUTHELIA_SECRET_FILES

    errors: list[str] = []
    for _svc, filename, env_key in AUTHELIA_SECRET_FILES:
        want = parsed.get(env_key)
        if not want:
            continue  # unset in .env → the env-value check covers it; nothing to reconcile here
        sfile = secret_dir / filename
        try:
            got: str | None = sfile.read_text(encoding="utf-8") if sfile.is_file() else None
        except OSError:
            got = None
        if got is None:
            errors.append(f"authelia secret file {filename} missing or unreadable")
        elif got.rstrip("\n") != want.rstrip("\n"):
            errors.append(
                f"authelia secret file {filename} is out of sync with .env {env_key} — re-run "
                f"`agmind install` or `agmind ops rotate-secrets` to reconcile (a stale file causes "
                f"authelia WRONGPASS / stack-wide auth outage)"
            )
    return errors


def _validate_runtime_secret_files(
    config: InstallConfig,
    selected_names: tuple[str, ...],
    parsed: dict[str, str],
) -> list[str]:
    try:
        return _validate_runtime_secret_files_unchecked(config, selected_names, parsed)
    except OSError as exc:
        return [f"runtime secret access failed: {exc}"]


def _validate_runtime_secret_files_unchecked(
    config: InstallConfig,
    selected_names: tuple[str, ...],
    parsed: dict[str, str],
) -> list[str]:
    if "traefik" not in selected_names:
        return []

    secret_dir = config.models_dir.parent / "secrets"
    if not secret_dir.exists():
        return [f"runtime secret directory {secret_dir.name} missing"]

    errors: list[str] = []
    if secret_dir.is_symlink() or not secret_dir.is_dir():
        return [f"runtime secret directory {secret_dir.name} must be a real directory"]

    mode = secret_dir.stat().st_mode & 0o777
    if mode != 0o700:
        errors.append(f"runtime secret directory {secret_dir.name} mode must be 0700, got {mode:o}")

    errors.extend(_authelia_secret_parity_errors(secret_dir, selected_names, parsed))

    secret_file = secret_dir / "cf_dns_api_token"
    if not secret_file.exists():
        errors.append(f"runtime secret file {secret_file.name} missing")
        return errors
    if secret_file.is_symlink() or not secret_file.is_file():
        errors.append(f"runtime secret file {secret_file.name} must be a regular file")
        return errors

    mode = secret_file.stat().st_mode & 0o777
    if mode != 0o600:
        errors.append(f"runtime secret file {secret_file.name} mode must be 0600, got {mode:o}")
    if config.cf_api_token and secret_file.read_text(encoding="utf-8") != config.cf_api_token:
        errors.append(f"runtime secret file {secret_file.name} content mismatch")
    return errors


def _validate_runtime_config_files(
    config: InstallConfig,
    selected_names: tuple[str, ...],
) -> list[str]:
    try:
        return _validate_runtime_config_files_unchecked(config, selected_names)
    except OSError as exc:
        return [f"runtime config access failed: {exc}"]


def _validate_runtime_config_files_unchecked(
    config: InstallConfig,
    selected_names: tuple[str, ...],
) -> list[str]:
    data_dir = config.models_dir.parent
    selected = set(selected_names)
    required_paths = {
        "traefik": (
            (data_dir / "traefik" / "dynamic" / "middlewares.yml", "file"),
            (data_dir / "traefik" / "letsencrypt", "directory"),
        ),
        "prometheus": (
            (config.config_dir / "prometheus" / "prometheus.yml", "file"),
            (config.config_dir / "prometheus" / "rules", "directory"),
        ),
        "grafana": (
            (
                config.config_dir / "grafana" / "provisioning" / "datasources" / "agmind.yml",
                "file",
            ),
            (
                config.config_dir / "grafana" / "provisioning" / "dashboards" / "dashboards.yml",
                "file",
            ),
        ),
        "loki": ((config.config_dir / "loki" / "loki.yml", "file"),),
        "alloy": ((config.config_dir / "alloy" / "config.alloy", "file"),),
        "alertmanager": ((config.config_dir / "alertmanager" / "alertmanager.yml", "file"),),
    }

    errors: list[str] = []
    for service_name, paths in required_paths.items():
        if service_name not in selected:
            continue
        for path, path_kind in paths:
            if path_kind == "file" and not path.is_file():
                errors.append(f"runtime config file {_runtime_config_label(config, path)} missing")
            if path_kind == "directory" and not path.is_dir():
                errors.append(
                    f"runtime config directory {_runtime_config_label(config, path)} missing"
                )
    return errors


def _runtime_config_label(config: InstallConfig, path: Path) -> str:
    data_dir = config.models_dir.parent
    for root, prefix in ((config.config_dir, "config"), (data_dir, "data")):
        try:
            return f"{prefix}/{path.relative_to(root).as_posix()}"
        except ValueError:
            continue
    return path.name


def _validate_version_manifest(
    path: Path,
    selected_names: tuple[str, ...],
    descriptors: dict[str, Any],
) -> list[str]:
    from agmind import __version__

    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else None
        duplicate_keys = _duplicate_env_keys(path)
        parsed = parse_env_file(path)
    except OSError as exc:
        return [f"version.env access failed: {exc}"]
    if not parsed:
        return ["version.env missing or empty"]

    missing_keys: list[str] = []
    mismatched_keys: list[str] = []
    expected_runtime = {"AGMIND_VERSION": __version__}
    for env_key, expected_value in expected_runtime.items():
        actual_value = parsed.get(env_key)
        if actual_value is None:
            missing_keys.append(env_key)
        elif actual_value != expected_value:
            mismatched_keys.append(f"{env_key}={actual_value!r} expected {expected_value!r}")

    for service_name in selected_names:
        descriptor = descriptors[service_name]
        tag = _image_tag(descriptor.image)
        digest = (descriptor.digest or _image_digest(descriptor.image)).removeprefix("sha256:")
        if not tag and not digest:
            continue
        key = _version_key(service_name)
        expected = {
            key: tag,
            f"{key}_IMAGE": descriptor.image,
        }
        if digest:
            expected[f"{key}_DIGEST"] = f"sha256:{digest}"
        for env_key, expected_value in expected.items():
            actual_value = parsed.get(env_key)
            if actual_value is None:
                missing_keys.append(env_key)
            elif actual_value != expected_value:
                mismatched_keys.append(f"{env_key}={actual_value!r} expected {expected_value!r}")

    errors: list[str] = []
    if mode != 0o644:
        got_mode = "missing" if mode is None else f"{mode:o}"
        errors.append(f"version.env mode must be 0644, got {got_mode}")
    if duplicate_keys:
        errors.append(f"version.env duplicate runtime version keys: {', '.join(duplicate_keys)}")
    if missing_keys:
        errors.append(f"version.env missing runtime version keys: {', '.join(missing_keys)}")
    if mismatched_keys:
        errors.append(f"version.env mismatched runtime version keys: {', '.join(mismatched_keys)}")
    return errors


def _duplicate_env_keys(path: Path) -> list[str]:
    if not path.exists():
        return []

    seen: set[str] = set()
    duplicates: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if not key:
            continue
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    return sorted(duplicates)


def _run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    run: CommandRunner,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            cmd,
            cwd=cwd,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            cmd,
            124,
            stdout=str(exc.stdout or ""),
            stderr=f"command timed out after {timeout_seconds}s",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))


def _completed_to_check(
    name: str,
    completed: subprocess.CompletedProcess[str],
    *,
    success_message: str,
) -> InstallVerifyCheck:
    if completed.returncode == 0:
        return InstallVerifyCheck(
            name,
            True,
            success_message,
            {"returncode": completed.returncode},
        )
    return InstallVerifyCheck(
        name,
        False,
        _stderr_or_stdout(completed) or f"{name} failed with rc={completed.returncode}",
        {"returncode": completed.returncode},
    )


def _stderr_or_stdout(completed: subprocess.CompletedProcess[str]) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    return text[-500:]


def format_install_verify_report(report: InstallVerifyReport) -> str:
    """Format a concise human-readable verification report."""
    status = "OK" if report.ok else "FAILED"
    lines = [
        f"fresh-install verification {status}: "
        f"{report.summary['check_count']} checks, "
        f"{report.summary['scenario_count']} scenarios, "
        f"{report.summary['failed_count']} failed",
    ]
    if report.checks:
        lines.append("")
        lines.append("checks:")
        for check in report.checks:
            mark = "OK" if check.ok else "FAIL"
            lines.append(f"  [{mark}] {check.name}: {check.message}")

    if report.scenarios:
        lines.append("")
        lines.append("scenarios:")
        for scenario in report.scenarios:
            mark = "OK" if scenario.ok else "FAIL"
            lines.append(
                f"  [{mark}] {scenario.name}: services={scenario.services}, "
                f"env={scenario.env_key_count}, diff={scenario.deploy_changes} - "
                f"{scenario.message}"
            )

    return "\n".join(lines)


def scenario_names(
    scenarios: Sequence[InstallVerifyScenario] = DEFAULT_SCENARIOS,
) -> tuple[str, ...]:
    """Return known scenario names for CLI validation."""
    return tuple(scenario.name for scenario in scenarios)
