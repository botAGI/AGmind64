"""GGUF model download step.

Split out of the historical single-file ``agmind/install/steps.py``; every name
here is re-exported from the package ``__init__``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import timedelta
from pathlib import Path

from agmind.core.files import write_text_atomic
from agmind.install.orchestrator import (
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressKind,
)

from ._common import _make_event

# `_stream_subprocess`, `_offline_install_enabled`, `_ensure_models_dir` and
# `_copy_file_atomic` are monkeypatched on the PACKAGE object by the model tests
# (tests/install/test_install_model_detect.py, test_install_steps_model_sha256.py);
# resolve them through the package at call time so the patches still reach this step
# after the package split (see configs.py).
_steps = sys.modules["agmind.install.steps"]

# ---------- Step 4: model download ----------


class ModelDownloadStep(InstallStep):
    """Download up to 3 GGUF models from HF (LLM + Embed + Rerank).

    Phase M5.1: каждая role (llm/embed/rerank) скачивается отдельным
    call'ом — pair (repo, file) of empty/None → skipped. Order: LLM →
    Embed → Rerank (LLM blocking, embeds tiny, rerank optional).

    Detect logic per file (skip re-download если модель уже скачана):
      1. `{models_dir}/{file}` (default /var/lib/agmind/models/) → reuse
      2. User fallback `~/.local/share/agmind/models/{file}` → move в models_dir
      3. None of above → curl download с resume support

    Минимальный размер чтобы считать "real model" = 100 MiB. Embed/rerank
    модели могут быть < 100 MiB — для них порог снижен до 10 MiB.
    """

    step_id = "model_pull"
    label = "Model download"

    PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    # M4.6: curl --progress-bar also writes speed/ETA, parse it for richer events
    SPEED_RE = re.compile(r"(\d+\.?\d*)\s*([KMG])\s")
    MIN_VALID_SIZE = 100 * 1024 * 1024  # 100 MiB — filter empty placeholders / partial
    MIN_VALID_SIZE_SMALL = 10 * 1024 * 1024  # 10 MiB — для embed/rerank (BGE-M3 = 600 MiB)
    DISK_SPACE_BUFFER_BYTES = 256 * 1024 * 1024

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

    def _detect_existing(
        self,
        models_dir: Path,
        file_name: str,
        min_size: int,
        config: InstallConfig,
    ) -> tuple[Path | None, str]:
        """Return (path, status_msg) — где модель уже есть. None если nowhere."""
        from agmind.models import safe_model_target

        target = safe_model_target(models_dir, file_name)
        if target.exists() and target.stat().st_size >= min_size:
            return target, f"already present в {target.parent}"
        for fb in self._fallback_dirs(config):
            cand = safe_model_target(fb, file_name)
            if cand.exists() and cand.stat().st_size >= min_size:
                return cand, f"found in fallback {fb}"
        return None, "not present anywhere"

    @staticmethod
    def _expected_download_size_bytes(repo: str, file_name: str, min_size: int) -> int:
        """Best-effort expected download size without network calls."""
        try:
            from agmind.install.models import CURATED_MODELS
        except Exception:
            return min_size
        for entry in CURATED_MODELS:
            if entry.repo == repo and entry.file == file_name and entry.size_gib > 0:
                return int(entry.size_gib * 1024 * 1024 * 1024)
        return min_size

    def _check_model_disk_space(
        self,
        *,
        role: str,
        repo: str,
        file_name: str,
        target: Path,
        partial: Path,
        min_size: int,
    ) -> str | None:
        expected_size = self._expected_download_size_bytes(repo, file_name, min_size)
        partial_size = partial.stat().st_size if partial.exists() else 0
        remaining = max(expected_size - partial_size, min_size)
        buffer = max(self.DISK_SPACE_BUFFER_BYTES, expected_size // 20)
        free = shutil.disk_usage(target.parent).free
        if free >= remaining + buffer:
            return None
        free_mb = free // (1024 * 1024)
        needed_mb = (remaining + buffer) // (1024 * 1024)
        return (
            f"{role}: not enough free space in {target.parent} for {file_name}: "
            f"{free_mb} MiB free, need at least {needed_mb} MiB"
        )

    @staticmethod
    def _file_sha256(path: Path) -> str:
        """Compute a file's sha256 in chunks — multi-GB models stay off the heap.

        Mirrors `agmind.cli.models_cmd._file_sha256` (the existing G.5 verify helper for
        the standalone `agmind models download` CLI path); kept local to the install layer
        so it does not reach up into the CLI layer.
        """
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _verify_marker_path(target: Path) -> Path:
        return target.with_name(f".{target.name}.sha256-verified.json")

    @staticmethod
    def _sha256_matches(path: Path, sha256: str | None) -> tuple[bool, str | None]:
        """Pure content check against a pinned sha256 — NEVER deletes.

        Empty/unset sha256 → (True, None) (back-compat: unpinned catalog entries accept any
        bytes). Match → (True, None); mismatch → (False, actual_hex); read error → (False, msg).
        Used to gate a freshly-downloaded PARTIAL *before* it may replace an in-place model, so
        a bad download can never clobber a working file.
        """
        if not sha256:
            return True, None
        try:
            actual = ModelDownloadStep._file_sha256(path)
        except OSError as exc:
            return False, f"cannot read {path} for sha256 verify: {exc}"
        return (True, None) if actual == sha256 else (False, actual)

    @classmethod
    def _write_verify_marker(cls, target: Path, sha256: str, size: int) -> None:
        """Record a verify-once marker so a later reuse of the SAME file skips re-hashing."""
        write_text_atomic(
            cls._verify_marker_path(target), json.dumps({"sha256": sha256, "size": size})
        )

    def _verify_inplace_or_mark(
        self, role: str, target: Path, sha256: str | None
    ) -> tuple[bool, str | None]:
        """Verify an ALREADY-IN-PLACE model against sha256, with a verify-once marker cache.

        Non-destructive by design. The T-15.2-04/05 contract — poisoned bytes must never reach
        a container — is kept (a mismatch fails the step, so the file is never *blessed*), but a
        mismatch here must NOT delete ``target``: deleting a pre-existing model was a footgun —
        a live host running a differently-sourced but valid model (a hash the pin didn't
        anticipate) would be left with NO model the instant an ``agmind install`` re-ran, before
        any replacement was secured. Callers treat a mismatch as "re-download to a verified
        partial and swap", so the working file survives until its replacement lands (or the step
        fails with the file preserved). A match writes a marker recording the verified
        (sha256, size) so a later reuse of the SAME file does not re-hash a 20-100 GiB model.
        """
        if not sha256:
            return True, None
        marker = self._verify_marker_path(target)
        try:
            size = target.stat().st_size
        except OSError as exc:
            return False, f"{role}: cannot stat {target} for sha256 verify: {exc}"
        try:
            recorded = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recorded = None
        if (
            isinstance(recorded, dict)
            and recorded.get("sha256") == sha256
            and recorded.get("size") == size
        ):
            return True, None  # verify-once: already verified for this exact file+hash
        matched, detail = self._sha256_matches(target, sha256)
        if not matched:
            # A now-stale marker (recorded a different verified hash) must not linger, but the
            # FILE stays — the caller re-downloads a verified replacement before anything is lost.
            with contextlib.suppress(OSError):
                marker.unlink()
            return (
                False,
                f"{role}: sha256 mismatch for {target.name} — expected {sha256}, got {detail}",
            )
        self._write_verify_marker(target, sha256, size)
        return True, None

    def _download_one(
        self,
        role: str,
        repo: str | None,
        file_name: str | None,
        config: InstallConfig,
        callback: ProgressCallback,
        revision: str | None = None,
        sha256: str | None = None,
    ) -> tuple[bool, str]:
        """Download single (repo, file). Returns (success, message)."""
        if not repo or not file_name:
            return True, f"{role}: no model — skipped"

        from agmind.models import hf_resolve_url, safe_model_target

        min_size = self.MIN_VALID_SIZE if role == "llm" else self.MIN_VALID_SIZE_SMALL
        try:
            target = safe_model_target(config.models_dir, file_name)
            # revision pins /resolve/<rev>/ (immutable); None → mutable main (back-compat).
            url = hf_resolve_url(repo, file_name, revision=revision)
        except ValueError as exc:
            return False, f"{role}: {exc}"
        config.models_dir.mkdir(parents=True, exist_ok=True)

        existing, status = self._detect_existing(config.models_dir, file_name, min_size, config)
        if existing is not None:
            size_mb = existing.stat().st_size // (1024 * 1024)
            if existing == target:
                ok, verify_err = self._verify_inplace_or_mark(role, target, sha256)
                if ok:
                    callback(
                        _make_event(
                            self.step_id,
                            ProgressKind.LOG,
                            f"{role}: skip download {existing} ({size_mb} MiB) — {status}",
                        )
                    )
                    return True, f"{role}: reused {size_mb} MiB"
                # Hash mismatch on the EXISTING model: do NOT delete it. Re-download to a
                # verified partial and swap only on success, so the current (working) file
                # survives if the replacement can't be secured (offline / bad download).
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        f"{role}: {verify_err}; re-downloading "
                        "(keeping the current file until the replacement verifies)",
                    )
                )
                # fall through to the download path below
            else:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        f"{role}: moving {existing} → {target} (saves re-download {size_mb} MiB)",
                    )
                )
                try:
                    shutil.move(str(existing), str(target))
                except OSError as exc:
                    try:
                        _steps._copy_file_atomic(existing, target)
                        existing.unlink()
                    except OSError as exc2:
                        return False, f"{role}: cannot relocate model: {exc2} (initial: {exc})"
                ok, verify_err = self._verify_inplace_or_mark(role, target, sha256)
                if ok:
                    return True, f"{role}: relocated {size_mb} MiB"
                # The relocated file mismatches. Leave it in place (do NOT delete) — a verified
                # re-download swaps it via a partial, and if that can't be secured the operator's
                # staged file is preserved rather than destroyed (same contract as the in-place
                # path above).
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        f"{role}: {verify_err}; re-downloading a verified copy",
                    )
                )
                # fall through to the download path below

        # Reached when the model is absent, OR when an in-place/relocated file failed its pinned
        # sha256 (fall-through above). In air-gap (AGMIND_OFFLINE) no download can run — fast-fail
        # rather than hang on a curl network error (review MEDIUM model-download-no-offline-fastfail).
        if _steps._offline_install_enabled():
            if target.is_file():
                # A pre-existing file is on disk but fails the pin; we did NOT delete it — a
                # working (if differently-sourced) model must survive with no replacement to hand.
                return (
                    False,
                    f"{role}: AGMIND_OFFLINE and on-disk '{file_name}' fails the pinned sha256 "
                    f"(expected {sha256}); kept the existing file — pre-stage a matching model "
                    "or re-pin.",
                )
            return (
                False,
                f"{role}: AGMIND_OFFLINE and model not present — pre-stage '{file_name}' at "
                f"{target} (or {config.models_dir}/); air-gap installs do not download from HF.",
            )

        partial = target.with_name(f".{target.name}.part")
        if target.is_file() and target.stat().st_size < min_size:
            try:
                if partial.exists():
                    target.unlink()
                else:
                    target.replace(partial)
            except OSError as exc:
                return False, f"{role}: cannot stage partial model download: {exc}"
        disk_error = self._check_model_disk_space(
            role=role,
            repo=repo,
            file_name=file_name,
            target=target,
            partial=partial,
            min_size=min_size,
        )
        if disk_error is not None:
            return False, disk_error
        if shutil.which("curl") is None:
            return False, f"{role}: curl not found on PATH (required to download models)"
        cmd = [
            "curl",
            "-fL",
            "-C",
            "-",
            "-o",
            str(partial),
            "--progress-bar",
            # Network stall guards: this download streams through an uncancellable
            # worker thread, so a half-open HF socket with no timeout would hang the
            # whole TUI. Fail fast on a dead connect (30s) or a transfer that drops
            # below 1 KiB/s for 60s; do NOT set --max-time (slow-but-progressing
            # multi-GB downloads must still succeed).
            "--connect-timeout",
            "30",
            "--speed-limit",
            "1024",
            "--speed-time",
            "60",
            "--retry",
            "3",
            "--retry-connrefused",
            url,
        ]
        last_pct = [-1]

        def parse_curl_pct(line: str) -> None:
            m = self.PROGRESS_RE.search(line)
            if not m:
                return
            try:
                pct = int(float(m.group(1)))
            except (ValueError, IndexError):
                return
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            speed_m = self.SPEED_RE.search(line)
            speed_label = ""
            if speed_m:
                speed_label = f" @ {speed_m.group(1)}{speed_m.group(2)}/s"
            try:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.PROGRESS,
                        f"{role} download {pct}%{speed_label}",
                        pct=pct,
                    )
                )
            except (ValueError, IndexError):
                pass

        # Integrity floor (audit H#10): when the curated catalog knows the expected size, reject
        # anything grossly short of it (a curl rc=0 can still yield a truncated/error body).
        expected = self._expected_download_size_bytes(repo, file_name, min_size)
        # The HF Xet backend occasionally answers a fresh request with a redirect/empty body — curl
        # exits rc=0 yet ~0 MiB lands on disk (the "100% then 0 MiB" glitch the operator hit live).
        # That is TRANSIENT, so retry IN-STEP (clean partial each time) instead of failing the whole
        # install; only a non-zero curl rc (genuine error/cancel) is kept for a later -C - resume.
        # live clean-install 2026-06-07.
        attempts = 3
        last_err = ""
        for attempt in range(1, attempts + 1):
            rc, _ = _steps._stream_subprocess(
                cmd,
                callback,
                self.step_id,
                extra_emit=parse_curl_pct,
                cancel_event=self.cancel_event,
            )
            if self.cancel_event is not None and self.cancel_event.is_set():
                return False, f"{role}: cancelled"
            if rc != 0:
                # Genuine interrupt/error: keep the partial so a later run can `curl -C -` resume it.
                return False, f"{role}: curl rc={rc} (download failed)"
            partial_size = partial.stat().st_size if partial.exists() else 0
            too_small = partial_size < min_size
            truncated = expected > min_size and partial_size < int(expected * 0.88)
            if too_small or truncated:
                # Empty/truncated body — NOT a resumable partial (leaving it would poison the next
                # `curl -C -`). Clear it and retry from scratch.
                with contextlib.suppress(OSError):
                    partial.unlink()
                got_mb = partial_size // (1024 * 1024)
                want_mb = (expected if expected > min_size else min_size) // (1024 * 1024)
                last_err = (
                    f"{role}: downloaded {got_mb} MiB but expected ~{want_mb} MiB (HF/Xet glitch)"
                )
                if attempt < attempts:
                    callback(
                        _make_event(
                            self.step_id,
                            ProgressKind.PROGRESS,
                            f"{last_err} — retrying clean ({attempt}/{attempts})",
                        )
                    )
                continue
            # Verify the DOWNLOADED PARTIAL before it may replace an in-place model — a bad
            # download must never clobber a working file. On mismatch, discard the partial and
            # fail; any existing target is left untouched.
            ok, verify_err = self._sha256_matches(partial, sha256)
            if not ok:
                with contextlib.suppress(OSError):
                    partial.unlink()
                return False, (
                    f"{role}: downloaded {file_name} failed sha256 verify — expected "
                    f"{sha256}, got {verify_err} (discarded the download; any existing "
                    "file was kept)"
                )
            partial.replace(target)
            if sha256:
                with contextlib.suppress(OSError):
                    self._write_verify_marker(target, sha256, target.stat().st_size)
            size_mb = target.stat().st_size // (1024 * 1024)
            return True, f"{role}: downloaded {size_mb} MiB → {target.name}"
        return False, f"{last_err}; gave up after {attempts} clean attempts"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()

        # The models dir lives under a root-owned runtime root; make it user-writable
        # (via sudo if needed) before downloading, else mkdir/curl fail with [Errno 13].
        try:
            _steps._ensure_models_dir(config, callback, self.step_id)
        except (OSError, PermissionError) as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"cannot prepare models dir {config.models_dir}: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        roles = (
            (
                "llm",
                config.model_repo,
                config.model_file,
                config.model_revision,
                config.model_sha256,
            ),
            (
                "embed",
                config.embed_repo,
                config.embed_file,
                config.embed_revision,
                config.embed_sha256,
            ),
            (
                "rerank",
                config.rerank_repo,
                config.rerank_file,
                config.rerank_revision,
                config.rerank_sha256,
            ),
        )

        messages: list[str] = []
        for role, repo, file_name, revision, sha256 in roles:
            ok, msg = self._download_one(
                role, repo, file_name, config, callback, revision=revision, sha256=sha256
            )
            messages.append(msg)
            if not ok:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=msg,
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="; ".join(messages),
            elapsed=timedelta(seconds=time.monotonic() - start),
        )
