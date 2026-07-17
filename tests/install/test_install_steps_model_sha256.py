"""Phase 15 D-02 (part 2): ModelDownloadStep post-download sha256 verify.

`_download_one` (agmind/install/steps.py) downloads GGUF models via curl but, prior to
this plan, never checked the downloaded bytes against the catalog's pinned sha256 (the
G.5 verify pattern in `agmind/cli/models_cmd.py` only covers the standalone
`agmind models download` CLI path, not the install path real installs use). These tests
are hermetic: curl is mocked (`agmind.install.steps._stream_subprocess`) to write a known
fixture blob — no network, no multi-GB files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import ModelDownloadStep

pytestmark = pytest.mark.backend_any

# embed/rerank roles use MIN_VALID_SIZE_SMALL (10 MiB) — smallest fixture that clears the
# "too small / truncated" retry floor in `_download_one`, keeping the test fast.
_PAYLOAD_SIZE = ModelDownloadStep.MIN_VALID_SIZE_SMALL + 4096
_BLOCK = b"agmind-sha256-fixture-block-0123456789abcdef\n"
_KNOWN_BYTES = (_BLOCK * (_PAYLOAD_SIZE // len(_BLOCK) + 1))[:_PAYLOAD_SIZE]
_KNOWN_SHA256 = hashlib.sha256(_KNOWN_BYTES).hexdigest()
_WRONG_SHA256 = "0" * 64


def _cfg(tmp_path: Path, file_name: str = "embed.gguf") -> InstallConfig:
    return InstallConfig(
        domain="x.example",
        cf_api_token="t" * 40,
        services=["llama-embed"],
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "models",
        embed_repo="org/repo",
        embed_file=file_name,
        sudo_password="pw",
    )


def _mock_curl_writes_blob(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
    """Patch `_stream_subprocess` so the curl invocation writes `payload` to the `-o`
    target — no network, no real curl process runs."""

    def fake_stream(cmd, callback, step_id, **kw):
        del callback, step_id, kw
        output = Path(cmd[cmd.index("-o") + 1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(payload)
        return (0, [])

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", fake_stream)


def test_sha256_mismatch_fails_and_unlinks_poisoned_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A downloaded file whose bytes don't match the catalog sha256 is rejected — the
    poisoned target is removed and the step fails, before any container could load it."""
    cfg = _cfg(tmp_path)
    _mock_curl_writes_blob(monkeypatch, _KNOWN_BYTES)

    ok, msg = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_WRONG_SHA256
    )

    assert ok is False
    assert "sha256" in msg.lower()
    target = cfg.models_dir / cfg.embed_file
    assert not target.exists(), "mismatched model must not survive on disk"


def test_sha256_match_keeps_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A matching sha256 keeps the downloaded file in place."""
    cfg = _cfg(tmp_path)
    _mock_curl_writes_blob(monkeypatch, _KNOWN_BYTES)

    ok, msg = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_KNOWN_SHA256
    )

    assert ok is True, msg
    target = cfg.models_dir / cfg.embed_file
    assert target.exists()
    assert target.read_bytes() == _KNOWN_BYTES


def test_empty_sha256_skips_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Back-compat: an unset sha256 (unverified catalog entries, e.g. the 3 unfetchable
    filename-mismatch models) performs no verification — any bytes are accepted."""
    cfg = _cfg(tmp_path)
    _mock_curl_writes_blob(monkeypatch, _KNOWN_BYTES)

    ok, msg = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=None
    )

    assert ok is True, msg
    assert (cfg.models_dir / cfg.embed_file).exists()


def test_verify_once_skips_rehash_on_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a successful verify, a second `_download_one` for the same already-present
    file (reuse path) must not re-hash — hashing a 20-100 GiB model on every install would
    be prohibitively expensive. A verify-once marker recorded on the first call must let
    the second call skip straight to reuse."""
    import agmind.install.steps as steps_mod

    cfg = _cfg(tmp_path)
    _mock_curl_writes_blob(monkeypatch, _KNOWN_BYTES)

    calls = {"n": 0}
    orig_hash = steps_mod.ModelDownloadStep._file_sha256

    def spy(path: Path) -> str:
        calls["n"] += 1
        return orig_hash(path)

    monkeypatch.setattr(steps_mod.ModelDownloadStep, "_file_sha256", staticmethod(spy))

    ok1, msg1 = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_KNOWN_SHA256
    )
    assert ok1 is True, msg1
    assert calls["n"] == 1, "first verify must hash the fresh download"

    ok2, msg2 = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_KNOWN_SHA256
    )
    assert ok2 is True, msg2
    assert "reused" in msg2.lower()
    assert calls["n"] == 1, "reuse of an already-verified file must not re-hash"
