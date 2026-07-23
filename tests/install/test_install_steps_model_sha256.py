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


def test_sha256_mismatch_discards_download_before_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly-downloaded file whose bytes don't match the catalog sha256 is rejected —
    the download is discarded as a partial and never becomes the target, so poisoned bytes
    never reach a container. (No pre-existing model here → target must not exist after.)"""
    cfg = _cfg(tmp_path)
    _mock_curl_writes_blob(monkeypatch, _KNOWN_BYTES)

    ok, msg = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_WRONG_SHA256
    )

    assert ok is False
    assert "sha256" in msg.lower()
    target = cfg.models_dir / cfg.embed_file
    assert not target.exists(), "mismatched download must not survive as the model"


def test_existing_mismatch_offline_preserves_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Footgun regression: a pre-existing, full-size model whose bytes don't match the pinned
    sha256 must NOT be deleted when no verified replacement can be secured (air-gap). A live
    host running a differently-sourced-but-valid model must never be left with NO model just
    because an ``agmind install`` re-ran against a hash the pin didn't anticipate."""
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.embed_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_KNOWN_BYTES)  # valid working model, but a different source than the pin

    # air-gap: no download may run to replace it
    monkeypatch.setattr("agmind.install.steps._offline_install_enabled", lambda: True)

    def _no_curl(*_a: object, **_k: object) -> tuple[int, list[str]]:
        raise AssertionError("must not attempt a download while offline")

    monkeypatch.setattr("agmind.install.steps._stream_subprocess", _no_curl)

    ok, msg = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_WRONG_SHA256
    )

    assert ok is False
    assert target.exists(), "existing model must be preserved, not deleted, with no replacement"
    assert target.read_bytes() == _KNOWN_BYTES
    assert "kept" in msg.lower()


def test_existing_mismatch_bad_redownload_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-existing model that fails the pin triggers a re-download; if that download ALSO
    fails verification, the original file must survive — a bad download never clobbers the
    working model (the partial is verified BEFORE it may replace the target)."""
    cfg = _cfg(tmp_path)
    target = cfg.models_dir / cfg.embed_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_KNOWN_BYTES)  # working model, but pinned sha below won't match it

    # online: curl writes a DIFFERENT full-size blob that also mismatches the pin
    bad_blob = b"y" * _PAYLOAD_SIZE
    assert hashlib.sha256(bad_blob).hexdigest() != _WRONG_SHA256
    _mock_curl_writes_blob(monkeypatch, bad_blob)

    ok, msg = ModelDownloadStep()._download_one(
        "embed", cfg.embed_repo, cfg.embed_file, cfg, lambda _e: None, sha256=_WRONG_SHA256
    )

    assert ok is False
    assert target.exists(), "a failed re-download must not clobber the working model"
    assert target.read_bytes() == _KNOWN_BYTES


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
