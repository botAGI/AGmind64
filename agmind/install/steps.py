"""Phase N: concrete install steps.

Каждый step стримит stdout/stderr субпроцесса через callback (LOG events),
обновляет progress percent если можно вычислить, и возвращает
InstallStepResult с success / message / elapsed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path

from agmind.install.orchestrator import (
    DEFAULT_REPO_ROOT,
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressKind,
)


# ---------- helpers ----------


def _stream_subprocess(
    cmd: list[str],
    callback: ProgressCallback,
    step_id: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin_payload: bytes | None = None,
    extra_emit: callable | None = None,
) -> tuple[int, list[str]]:
    """Run subprocess, stream stdout+stderr line-by-line via callback.

    Returns (returncode, captured_lines). `extra_emit` callable получает
    каждую строку и может emit'ить дополнительные ProgressEvent (e.g.
    парсить progress %). Все строки также эмитятся как ProgressKind.LOG.
    """
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        text=True,
        bufsize=1,
    )
    if stdin_payload is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_payload.decode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass

    captured: list[str] = []
    if proc.stdout is not None:
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            captured.append(line)
            callback(
                _make_event(step_id, ProgressKind.LOG, line)
            )
            if extra_emit is not None:
                try:
                    extra_emit(line)
                except Exception:  # noqa: BLE001
                    pass
    rc = proc.wait()
    return rc, captured


def _make_event(step_id: str, kind: ProgressKind, text: str, pct: int | None = None) -> object:
    """Local import to avoid circular: создать ProgressEvent без import outside."""
    from agmind.install.orchestrator import ProgressEvent
    return ProgressEvent(step_id=step_id, kind=kind, text=text, progress_pct=pct)


# ---------- Step 1: doctor ----------


class DoctorStep(InstallStep):
    """Preflight — `agmind doctor`. Hard fail if any check returns 'fail'."""

    step_id = "doctor"
    label = "Preflight diagnostics"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        from agmind.diagnostics.doctor import run_preflight

        try:
            report = run_preflight()
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"doctor crashed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        for check in report.checks:
            glyph = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}.get(check.status, "?")
            callback(_make_event(
                self.step_id, ProgressKind.LOG,
                f"  {glyph}  {check.name:<22} {check.message}",
            ))

        elapsed = timedelta(seconds=time.monotonic() - start)
        ok_n = sum(1 for c in report.checks if c.status == "ok")
        warn_n = sum(1 for c in report.checks if c.status == "warn")
        if report.has_failures:
            failed = [c.name for c in report.checks if c.status == "fail"]
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"hard fail in checks: {', '.join(failed)}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message=f"{ok_n} ok / {warn_n} warn / 0 fail",
            elapsed=elapsed,
        )


# ---------- Step 2: bootstrap (Ansible, sudo) ----------


class BootstrapStep(InstallStep):
    """Run `ansible-playbook install.yml --tags bootstrap` с sudo password.

    Sudo password передаётся через `--become-password-file=<fd>` — anonymous
    pipe (fd=4 в child process). Это безопаснее чем temp file: pw в
    kernel buffer, не на disk.
    """

    step_id = "bootstrap"
    label = "System bootstrap (apt, groups, dirs)"

    PLAYBOOK_RELATIVE = "ansible/install.yml"
    ANSIBLE_TAGS = "bootstrap"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if config.sudo_password is None:
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message="sudo password not provided (cannot run apt/usermod)",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        playbook = DEFAULT_REPO_ROOT / self.PLAYBOOK_RELATIVE
        if not playbook.exists():
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"playbook not found: {playbook}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        # Write password to anonymous pipe → pass fd as become-password-file.
        rfd, wfd = os.pipe()
        try:
            os.write(wfd, config.sudo_password.encode("utf-8") + b"\n")
            os.close(wfd)

            extra_vars = (
                f"agmind_domain={config.domain} "
                f"agmind_cf_api_token={config.cf_api_token}"
            )
            cmd = [
                "ansible-playbook",
                str(playbook),
                "--become-password-file", f"/dev/fd/{rfd}",
                "--tags", self.ANSIBLE_TAGS,
                "--extra-vars", extra_vars,
                "-i", "localhost,",
                "--connection", "local",
            ]

            # We need rfd to survive into child. subprocess closes fds by default
            # с close_fds=True, но pass_fds= перечисляет которые оставить.
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                pass_fds=(rfd,),
            )
            ansible_re = re.compile(r"^(TASK|PLAY|ok:|changed:|failed:|fatal:|skipping:)")
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = raw.rstrip()
                    if not line:
                        continue
                    # Sanitize stdout — на всякий случай пароль не должен попасть
                    if config.sudo_password and config.sudo_password in line:
                        line = line.replace(config.sudo_password, "***")
                    callback(_make_event(self.step_id, ProgressKind.LOG, line))
            rc = proc.wait()
        finally:
            try:
                os.close(rfd)
            except OSError:
                pass

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"ansible-playbook failed with rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message="apt prereqs + groups + dirs ready",
            elapsed=elapsed,
        )


# ---------- Step 3: docker image pull ----------


class ImagePullStep(InstallStep):
    """`docker compose pull` после bootstrap (user уже в docker group).

    Требует чтобы compose был отрендерен — обычно делается deploy step,
    но pull раньше = lower TTR (time to running). Используем temporary
    render через `agmind render compose` без write.
    """

    step_id = "image_pull"
    label = "Docker image pull"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        # Lazy import чтобы не тянуть тяжёлый renderer на загрузке модуля.
        from agmind.services.renderer import render_to_string

        # Render compose в temp dir чтобы вызвать `docker compose pull`.
        import tempfile

        try:
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"compose render failed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        with tempfile.TemporaryDirectory(prefix="agmind-pull-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
            cmd = ["docker", "compose", "pull", "--policy", "missing", "--quiet"]
            # docker compose pull stderr содержит per-layer progress; stream его.
            rc, _ = _stream_subprocess(cmd, callback, self.step_id, cwd=tmpdir)

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"docker compose pull rc={rc}", elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message="images pulled", elapsed=elapsed,
        )


# ---------- Step 4: model download ----------


class ModelDownloadStep(InstallStep):
    """Download model from HF (если model_repo + model_file заданы).

    Detect logic (skip re-download если модель уже скачана):
      1. `{models_dir}/{model_file}` (default /var/lib/agmind/models/) → reuse
      2. User fallback `~/.local/share/agmind/models/{model_file}` → move в models_dir
      3. None of above → curl download с resume support

    Минимальный размер чтобы считать "real model" = 100 MiB (filter stubs).
    """

    step_id = "model_pull"
    label = "Model download"

    PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    MIN_VALID_SIZE = 100 * 1024 * 1024  # 100 MiB — filter empty placeholders / partial

    @staticmethod
    def _fallback_dirs(config: InstallConfig) -> list[Path]:
        """Other locations to check for already-downloaded model."""
        from os.path import expanduser
        candidates = [
            Path(expanduser("~/.local/share/agmind/models")),  # XDG user fallback
            # Future: Hugging Face HOME cache directory if user has model there.
        ]
        # Drop duplicates / models_dir itself
        seen = {config.models_dir.resolve()}
        out: list[Path] = []
        for c in candidates:
            r = c.resolve() if c.exists() else c
            if r in seen:
                continue
            seen.add(r)
            out.append(c)
        return out

    def _detect_existing(self, config: InstallConfig) -> tuple[Path | None, str]:
        """Return (path, status_msg) — где модель уже есть. None если nowhere."""
        target = config.models_dir / config.model_file
        if target.exists() and target.stat().st_size >= self.MIN_VALID_SIZE:
            return target, f"already present в {target.parent}"
        for fb in self._fallback_dirs(config):
            cand = fb / config.model_file
            if cand.exists() and cand.stat().st_size >= self.MIN_VALID_SIZE:
                return cand, f"found in fallback {fb}"
        return None, "not present anywhere"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.model_repo or not config.model_file:
            return InstallStepResult(
                step_id=self.step_id, success=True,
                message="no model selected — skipped",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        target = config.models_dir / config.model_file
        config.models_dir.mkdir(parents=True, exist_ok=True)

        # Detect existing — default location или fallback
        existing, status = self._detect_existing(config)
        if existing is not None:
            size_mb = existing.stat().st_size // (1024 * 1024)
            if existing == target:
                callback(_make_event(
                    self.step_id, ProgressKind.LOG,
                    f"skip download: {existing} ({size_mb} MiB) — {status}",
                ))
                return InstallStepResult(
                    step_id=self.step_id, success=True,
                    message=f"reused {size_mb} MiB at {existing}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            # Found в fallback — move в правильное место чтобы compose volume mount работал
            callback(_make_event(
                self.step_id, ProgressKind.LOG,
                f"moving {existing} → {target} (saves re-download {size_mb} MiB)",
            ))
            import shutil
            try:
                shutil.move(str(existing), str(target))
            except OSError as exc:
                # Cross-device move на разных FS → copy + remove
                try:
                    shutil.copy2(str(existing), str(target))
                    existing.unlink()
                except OSError as exc2:
                    return InstallStepResult(
                        step_id=self.step_id, success=False,
                        message=f"cannot relocate model: {exc2} (initial: {exc})",
                        elapsed=timedelta(seconds=time.monotonic() - start),
                    )
            return InstallStepResult(
                step_id=self.step_id, success=True,
                message=f"relocated {size_mb} MiB from fallback → {target.parent}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        # No existing — download через curl с resume support.
        url = f"https://huggingface.co/{config.model_repo}/resolve/main/{config.model_file}"
        cmd = ["curl", "-fL", "-C", "-", "-o", str(target),
               "--progress-bar", "--retry", "3", url]

        def parse_curl_pct(line: str) -> None:
            m = self.PROGRESS_RE.search(line)
            if m:
                try:
                    callback(_make_event(
                        self.step_id, ProgressKind.PROGRESS, line,
                        pct=int(float(m.group(1))),
                    ))
                except (ValueError, IndexError):
                    pass

        rc, _ = _stream_subprocess(cmd, callback, self.step_id, extra_emit=parse_curl_pct)
        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"curl rc={rc} (model download failed)", elapsed=elapsed,
            )
        size_mb = target.stat().st_size // (1024 * 1024)
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message=f"downloaded {size_mb} MiB → {target.name}", elapsed=elapsed,
        )


# ---------- Step 5: compose deploy ----------


class DeployStep(InstallStep):
    """Run `agmind deploy --apply` (reuse Phase L.B runner)."""

    step_id = "deploy"
    label = "Deploy compose stack + healthcheck"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        from agmind.deploy.runner import deploy as _deploy

        def deploy_progress(step: str, msg: str) -> None:
            callback(_make_event(self.step_id, ProgressKind.LOG, f"[{step}] {msg}"))

        try:
            result = _deploy(
                profiles=[],  # render по services list, см. config
                install_dir=config.install_dir,
                domain=config.domain,
                apply=True,
                no_prompt=True,
                progress=deploy_progress,
            )
        except Exception as exc:  # noqa: BLE001
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"deploy crashed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if not result.success:
            extra = " (rolled back)" if result.rollback_performed else ""
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"{result.message}{extra}", elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message=result.message, elapsed=elapsed,
        )


# ---------- step list factory ----------


class EnvWriteStep(InstallStep):
    """Write `/opt/agmind/.env` с runtime settings (model file, ctx, KV cache).

    `templates/services/llama-llm.yaml` ссылается на эти env vars через
    docker compose ${VAR} substitution.
    """

    step_id = "env_write"
    label = "Write runtime .env"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        env_path = config.install_dir / ".env"
        config.install_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            "# AGmind runtime env — written by `agmind install` Phase N.G.",
            "# Hand-edit allowed, but `agmind install` rerun перепишет.",
            f"AGMIND_DOMAIN={config.domain}",
            f"AGMIND_MODEL_FILE={config.model_file or ''}",
            f"AGMIND_CTX_SIZE={config.ctx_size}",
            f"AGMIND_KV_CACHE={config.kv_cache_type}",
            f"AGMIND_THREADS={config.threads}",
            f"AGMIND_PARALLEL={config.parallel_slots}",
        ]
        try:
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            env_path.chmod(0o644)
        except OSError as exc:
            return InstallStepResult(
                step_id=self.step_id, success=False,
                message=f"cannot write {env_path}: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        callback(_make_event(
            self.step_id, ProgressKind.LOG,
            f"wrote {env_path} with model={config.model_file} ctx={config.ctx_size}",
        ))
        return InstallStepResult(
            step_id=self.step_id, success=True,
            message=f".env written ({len(lines)} vars)",
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


def default_steps() -> list[InstallStep]:
    """Stock install pipeline. Order matters."""
    return [
        DoctorStep(),
        BootstrapStep(),
        ImagePullStep(),
        ModelDownloadStep(),
        EnvWriteStep(),  # before DeployStep — compose читает .env
        DeployStep(),
    ]


__all__ = [
    "BootstrapStep",
    "DeployStep",
    "DoctorStep",
    "EnvWriteStep",
    "ImagePullStep",
    "ModelDownloadStep",
    "default_steps",
]
