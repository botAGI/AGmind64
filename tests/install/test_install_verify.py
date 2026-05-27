from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any


def test_resolve_ansible_command_prefers_current_python_bin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.ansible_tools import resolve_ansible_command

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text("", encoding="utf-8")
    galaxy = bin_dir / "ansible-galaxy"
    galaxy.write_text("#!/bin/sh\n", encoding="utf-8")
    galaxy.chmod(0o755)

    monkeypatch.setattr(sys, "executable", str(python))
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert resolve_ansible_command("ansible-galaxy") == str(galaxy)


def test_resolve_ansible_command_handles_python_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.ansible_tools import resolve_ansible_command

    real_bin = tmp_path / "real"
    real_bin.mkdir()
    real_python = real_bin / "python3.12"
    real_python.write_text("", encoding="utf-8")

    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python_link = venv_bin / "python"
    python_link.symlink_to(real_python)
    playbook = venv_bin / "ansible-playbook"
    playbook.write_text("#!/bin/sh\n", encoding="utf-8")
    playbook.chmod(0o755)

    monkeypatch.setattr(sys, "executable", str(python_link))
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert resolve_ansible_command("ansible-playbook") == str(playbook)


def test_verify_install_runs_ansible_and_compose(tmp_path: Path) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    calls: list[tuple[list[str], Path | None]] = []

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, Path(cwd) if cwd is not None else None))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        run=fake_run,
    )

    assert report.ok is True
    assert report.summary["scenario_count"] == 1
    assert report.scenarios[0].name == "mini-core"
    assert report.scenarios[0].services >= 3
    assert report.scenarios[0].env_key_count >= 8
    assert (tmp_path / "mini-core" / "install" / "version.env").exists()
    assert (tmp_path / "mini-core" / "secrets" / "cf_dns_api_token").exists()
    assert any(
        Path(cmd[0]).name == "ansible-galaxy" and cmd[1:4] == ["collection", "install", "-r"]
        for cmd, _ in calls
    )
    assert any(cmd and Path(cmd[0]).name == "ansible-playbook" for cmd, _ in calls)
    assert any(cmd[:2] == ["docker", "compose"] and "config" in cmd for cmd, _ in calls)
    assert any(
        cmd[:2] == ["docker", "compose"]
        and cmd[-5:] == ["pull", "--dry-run", "--policy", "missing", "--quiet"]
        for cmd, _ in calls
    )
    ansible_cmd = next(cmd for cmd, _ in calls if cmd and Path(cmd[0]).name == "ansible-playbook")
    ansible_argv = "\0".join(ansible_cmd)
    assert "agmind_cf_api_token=" not in ansible_argv
    assert any(arg.startswith("@") for arg in ansible_cmd)


def test_verify_install_reports_compose_config_failure(tmp_path: Path) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "compose"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad compose")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "bad compose" in report.scenarios[0].message


def test_verify_install_reports_compose_file_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    original_write_text = Path.write_text

    def fake_write_text(self: Path, *args: object, **kwargs: object) -> int:
        if self.name == "docker-compose.yml":
            raise PermissionError("compose write denied")
        return original_write_text(self, *args, **kwargs)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(Path, "write_text", fake_write_text)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "compose render write failed" in report.scenarios[0].message
    assert "compose write denied" in report.scenarios[0].message


def test_verify_install_reports_duplicate_runtime_env_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class DuplicatingEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                env_file = config.install_dir / ".env"  # type: ignore[attr-defined]
                text = env_file.read_text(encoding="utf-8")
                duplicate = next(
                    line for line in text.splitlines() if line.startswith("POSTGRES_PASSWORD=")
                )
                env_file.write_text(f"{text}{duplicate}\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", DuplicatingEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "duplicate runtime env keys: POSTGRES_PASSWORD" in report.scenarios[0].message


def test_verify_install_reports_unreadable_runtime_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == ".env":
            raise PermissionError("runtime env denied")
        return original_read_text(self, *args, **kwargs)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime .env access failed" in report.scenarios[0].message
    assert "runtime env denied" in report.scenarios[0].message


def test_verify_install_reports_cloudflare_secret_mode_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class RestrictingSecretEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                secret_file = config.models_dir.parent / "secrets" / "cf_dns_api_token"  # type: ignore[attr-defined]
                secret_file.chmod(0o644)
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", RestrictingSecretEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime secret file cf_dns_api_token mode must be 0600, got 644" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_cloudflare_secret_content_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class CorruptingSecretEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                secret_file = config.models_dir.parent / "secrets" / "cf_dns_api_token"  # type: ignore[attr-defined]
                secret_file.write_text("wrong-cloudflare-token", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", CorruptingSecretEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime secret file cf_dns_api_token content mismatch" in (report.scenarios[0].message)
    assert "wrong-cloudflare-token" not in report.scenarios[0].message


def test_verify_install_reports_unreadable_cloudflare_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "cf_dns_api_token":
            raise PermissionError("secret denied")
        return original_read_text(self, *args, **kwargs)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime secret access failed" in report.scenarios[0].message
    assert "secret denied" in report.scenarios[0].message


def test_verify_install_reports_cloudflare_secret_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class SymlinkingSecretEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                secret_file = config.models_dir.parent / "secrets" / "cf_dns_api_token"  # type: ignore[attr-defined]
                target_file = secret_file.with_name("cf_dns_api_token.target")
                target_file.write_text(config.cf_api_token, encoding="utf-8")  # type: ignore[attr-defined]
                target_file.chmod(0o600)
                secret_file.unlink()
                secret_file.symlink_to(target_file)
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", SymlinkingSecretEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime secret file cf_dns_api_token must be a regular file" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_runtime_secret_directory_mode_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class RestrictingSecretDirEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                secret_dir = config.models_dir.parent / "secrets"  # type: ignore[attr-defined]
                secret_dir.chmod(0o755)
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", RestrictingSecretDirEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime secret directory secrets mode must be 0700, got 755" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_runtime_secret_directory_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class SymlinkingSecretDirEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                secret_dir = config.models_dir.parent / "secrets"  # type: ignore[attr-defined]
                target_dir = secret_dir.with_name("secrets.target")
                target_dir.mkdir()
                target_file = target_dir / "cf_dns_api_token"
                target_file.write_text(config.cf_api_token, encoding="utf-8")  # type: ignore[attr-defined]
                target_file.chmod(0o600)
                target_dir.chmod(0o700)
                shutil.rmtree(secret_dir)
                secret_dir.symlink_to(target_dir, target_is_directory=True)
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", SymlinkingSecretDirEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime secret directory secrets must be a real directory" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_missing_materialized_runtime_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class RemovingPrometheusConfigEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                prometheus_config = config.config_dir / "prometheus" / "prometheus.yml"  # type: ignore[attr-defined]
                prometheus_config.unlink()
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", RemovingPrometheusConfigEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-observability", ("prometheus", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime config file config/prometheus/prometheus.yml missing" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_runtime_config_access_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    original_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if self.name == "prometheus.yml":
            raise PermissionError("config denied")
        return original_is_file(self)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-observability", ("prometheus", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime config access failed" in report.scenarios[0].message
    assert "config denied" in report.scenarios[0].message


def test_verify_install_reports_runtime_domain_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class CorruptingDomainEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                env_file = config.install_dir / ".env"  # type: ignore[attr-defined]
                lines = [
                    "AGMIND_DOMAIN=wrong.example.com" if line.startswith("AGMIND_DOMAIN=") else line
                    for line in env_file.read_text(encoding="utf-8").splitlines()
                ]
                env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", CorruptingDomainEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime env key AGMIND_DOMAIN must be 'lab.example.com', got 'wrong.example.com'" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_runtime_model_file_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class CorruptingModelEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                env_file = config.install_dir / ".env"  # type: ignore[attr-defined]
                lines = [
                    "AGMIND_MODEL_FILE=old-model.gguf"
                    if line.startswith("AGMIND_MODEL_FILE=")
                    else line
                    for line in env_file.read_text(encoding="utf-8").splitlines()
                ]
                env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", CorruptingModelEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime env key AGMIND_MODEL_FILE must be 'model.gguf', got 'old-model.gguf'" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_runtime_minio_user_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class CorruptingMinioUserEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                env_file = config.install_dir / ".env"  # type: ignore[attr-defined]
                lines = [
                    "MINIO_ROOT_USER=root" if line.startswith("MINIO_ROOT_USER=") else line
                    for line in env_file.read_text(encoding="utf-8").splitlines()
                ]
                env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", CorruptingMinioUserEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "runtime env key MINIO_ROOT_USER must be 'agmind', got 'root'" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_missing_version_manifest_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class CorruptingEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                version_env = config.install_dir / "version.env"  # type: ignore[attr-defined]
                lines = [
                    line
                    for line in version_env.read_text(encoding="utf-8").splitlines()
                    if not line.startswith("LLAMA_LLM_VERSION_IMAGE=")
                ]
                version_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", CorruptingEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "version.env missing runtime version keys: LLAMA_LLM_VERSION_IMAGE" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_unreadable_version_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install.verify import InstallVerifyScenario, verify_install

    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "version.env":
            raise PermissionError("version.env denied")
        return original_read_text(self, *args, **kwargs)

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "version.env access failed" in report.scenarios[0].message
    assert "version.env denied" in report.scenarios[0].message


def test_verify_install_reports_version_manifest_agmind_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class CorruptingVersionEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                version_env = config.install_dir / "version.env"  # type: ignore[attr-defined]
                lines = [
                    "AGMIND_VERSION=0.0.0-old" if line.startswith("AGMIND_VERSION=") else line
                    for line in version_env.read_text(encoding="utf-8").splitlines()
                ]
                version_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", CorruptingVersionEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "version.env mismatched runtime version keys: AGMIND_VERSION='0.0.0-old' expected" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_duplicate_version_manifest_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class DuplicatingEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                version_env = config.install_dir / "version.env"  # type: ignore[attr-defined]
                text = version_env.read_text(encoding="utf-8")
                duplicate = next(
                    line
                    for line in text.splitlines()
                    if line.startswith("LLAMA_LLM_VERSION_IMAGE=")
                )
                version_env.write_text(f"{text}{duplicate}\n", encoding="utf-8")
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", DuplicatingEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "version.env duplicate runtime version keys: LLAMA_LLM_VERSION_IMAGE" in (
        report.scenarios[0].message
    )


def test_verify_install_reports_version_manifest_mode_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.install import verify
    from agmind.install.steps import EnvWriteStep as RealEnvWriteStep
    from agmind.install.verify import InstallVerifyScenario, verify_install

    class RestrictingVersionEnvWriteStep:
        def run(self, callback: object, config: object) -> object:
            result = RealEnvWriteStep().run(callback, config)  # type: ignore[arg-type]
            if result.success:
                version_env = config.install_dir / "version.env"  # type: ignore[attr-defined]
                version_env.chmod(0o600)
            return result

    def fake_run(
        cmd: list[str],
        *,
        cwd: Path | str | None = None,
        timeout: int | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify, "EnvWriteStep", RestrictingVersionEnvWriteStep)

    report = verify_install(
        domain="lab.example.com",
        scenarios=(InstallVerifyScenario("mini-core", ("llama-llm", "qdrant", "traefik")),),
        work_dir=tmp_path,
        include_ansible=False,
        run=fake_run,
    )

    assert report.ok is False
    assert report.summary["failed_count"] == 1
    assert "version.env mode must be 0644, got 600" in report.scenarios[0].message


def test_verify_install_cli_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from typer.testing import CliRunner

    from agmind.cli import _HAS_TYPER, _make_app
    from agmind.install.verify import (
        InstallVerifyCheck,
        InstallVerifyReport,
        InstallVerifyScenarioResult,
    )

    if not _HAS_TYPER:
        pytest.skip("typer not installed")

    report = InstallVerifyReport(
        checks=(InstallVerifyCheck(name="ansible-syntax", ok=True, message="ok"),),
        scenarios=(
            InstallVerifyScenarioResult(
                name="setup-default",
                ok=True,
                services=16,
                env_key_count=25,
                deploy_changes=16,
                message="ok",
            ),
        ),
        work_dir="/tmp/agmind-proof",
    )

    monkeypatch.setattr("agmind.cli.verify_cmd.verify_install", lambda **_kwargs: report)

    cli_app = _make_app()
    result = CliRunner().invoke(cli_app, ["verify", "install", "--json", "--skip-ansible"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["summary"]["scenario_count"] == 1
    assert payload["scenarios"][0]["name"] == "setup-default"
