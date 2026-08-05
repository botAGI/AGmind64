"""Regression: a pooled llama.cpp server must accept every input that fits its own context.

Embedding and reranking run POOLED, non-causal attention, so llama.cpp requires the whole
sequence to land in a single *physical* batch. That batch is ``-ub``, and it defaults to 512 in
the pinned image (verified on ``server-vulkan-b9049``: ``llama-server --help`` prints
"physical maximum batch size (default: 512)"), while ``--ctx-size 8192 --parallel 4`` advertises
2048 tokens per slot. Every input between the two is REJECTED — HTTP 500 "input (N tokens) is
too large to process. increase the physical batch size" — not truncated, not degraded.

This is not theoretical. Measured on the live stack 2026-08-05: 26 of 30 corpus documents failed
to index in RAGFlow, because its chunker emits 545-574 token chunks and the deployed bge-m3
could not embed a single one of them. The sibling ``llama-embed-strizh`` container, which was
hand-started with ``-b 8192 -ub 8192``, embedded 8000-token inputs on the same host at the same
time — a one-variable proof that the flag, not the hardware, was the limit.

The guard derives its subject set from the descriptors themselves (anything passing
``--embeddings`` or ``--reranking``) rather than naming services, so a future pooled service is
covered on the day it is added instead of the day someone remembers this file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

#: Flags that put llama-server into a pooled (non-causal) mode where the whole sequence must
#: fit one physical batch.
_POOLED_FLAGS = frozenset({"--embeddings", "--reranking"})

#: `${NAME:-123}` — the descriptors' interpolation form. The default is what ships.
_DEFAULTED = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*:-(\d+)\}$")


def _flag_value(command: list[str], *names: str) -> str | None:
    """Value following the first of ``names`` present in ``command``."""
    for index, token in enumerate(command):
        if token in names and index + 1 < len(command):
            return command[index + 1]
    return None


def _as_int(raw: str | None) -> int | None:
    """Resolve a literal or a `${VAR:-default}` to the number that actually ships."""
    if raw is None:
        return None
    matched = _DEFAULTED.match(raw)
    text = matched.group(1) if matched else raw
    try:
        return int(text)
    except ValueError:
        return None


def _pooled_services() -> dict[str, list[str]]:
    descriptors = load_descriptors(Path("templates/services"))
    return {
        name: list(descriptor.command or [])
        for name, descriptor in descriptors.items()
        if _POOLED_FLAGS & set(descriptor.command or [])
    }


def _offence(name: str, command: list[str]) -> str | None:
    physical = _as_int(_flag_value(command, "-ub", "--ubatch-size"))
    if physical is None:
        return (
            f"{name}: no -ub/--ubatch-size → llama.cpp defaults the physical batch to 512, so "
            "every input above 512 tokens fails with HTTP 500 regardless of --ctx-size"
        )

    ctx = _as_int(_flag_value(command, "-c", "--ctx-size"))
    if ctx is None:
        return None  # no advertised context to contradict

    slots = _as_int(_flag_value(command, "-np", "--parallel")) or 1
    per_slot = ctx // slots
    if physical < per_slot:
        return (
            f"{name}: physical batch {physical} < per-slot context {per_slot} "
            f"(--ctx-size {ctx} / --parallel {slots}) → inputs between {physical} and {per_slot} "
            "tokens are advertised as acceptable and then rejected with HTTP 500"
        )

    logical = _as_int(_flag_value(command, "-b", "--batch-size"))
    if logical is not None and logical < physical:
        return f"{name}: logical batch {logical} < physical batch {physical} (llama.cpp requires -b >= -ub)"
    return None


def test_discovery_finds_pooled_services() -> None:
    """Guard the guard: an empty subject set would make the assertion below vacuous."""
    pooled = _pooled_services()
    assert pooled, "no service passes --embeddings or --reranking — discovery is broken"
    assert "llama-embed" in pooled


def test_pooled_services_can_embed_their_full_context() -> None:
    offences = [o for name, cmd in _pooled_services().items() if (o := _offence(name, cmd))]
    assert not offences, (
        "pooled llama.cpp services advertise a context they cannot physically accept:\n"
        + "\n".join(f"  - {o}" for o in offences)
        + "\n\nPass -b/-ub >= (--ctx-size / --parallel) so an input that fits a slot can be "
        "processed. Cost measured on Strix Halo for bge-m3: 587 MiB GTT at ub512, "
        "1406 MiB at ub2048."
    )


def test_generated_env_matches_the_descriptors() -> None:
    """The installer pins these into ``.env``, and a pinned value BEATS the descriptor default.

    So a descriptor-only fix is invisible on every existing install — the same class as "upgrade
    bumps the tag and leaves the old digest". The derivation in ``InstallConfig`` therefore has to
    agree with what the descriptors actually pass, and ``--parallel`` is read out of the YAML here
    rather than restated, so changing it in one place cannot silently desync the other.
    """
    from agmind.install.orchestrator import InstallConfig

    config = InstallConfig(domain="example.invalid", cf_api_token="x", services=[])
    pooled = _pooled_services()

    embed_slots = _as_int(_flag_value(pooled["llama-embed"], "-np", "--parallel"))
    assert embed_slots == config.embed_parallel, (
        f"llama-embed.yaml passes --parallel {embed_slots} but InstallConfig derives the batch "
        f"from embed_parallel={config.embed_parallel}"
    )
    assert config.embed_batch == config.embed_ctx_size // config.embed_parallel

    rerank_slots = _as_int(_flag_value(pooled["llama-rerank"], "-np", "--parallel"))
    assert rerank_slots == InstallConfig.RERANK_PARALLEL, (
        f"llama-rerank.yaml passes --parallel {rerank_slots} but InstallConfig.RERANK_PARALLEL "
        f"is {InstallConfig.RERANK_PARALLEL} — the generated AGMIND_RERANK_BATCH would be wrong"
    )
    assert config.rerank_batch == config.rerank_ctx_size // rerank_slots

    # And the shipped defaults must agree, or a no-.env render differs from a real install.
    assert _as_int(_flag_value(pooled["llama-embed"], "-ub")) == config.embed_batch
    assert _as_int(_flag_value(pooled["llama-rerank"], "-ub")) == config.rerank_batch
    assert (
        _as_int(_flag_value(pooled["llama-rerank"], "-c", "--ctx-size")) == config.rerank_ctx_size
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["--embeddings", "--ctx-size", "8192", "--parallel", "4"], "no -ub"),
        (["--embeddings", "--ctx-size", "8192", "--parallel", "4", "-ub", "512"], "< per-slot"),
        (["--embeddings", "--ctx-size", "8192", "--parallel", "4", "-ub", "2048"], None),
        (["--embeddings", "--ctx-size", "${A:-8192}", "-np", "4", "-ub", "${B:-2048}"], None),
        (["--embeddings", "-c", "2048", "-ub", "2048", "-b", "512"], "logical batch"),
    ],
)
def test_detector_fires_on_planted_offences(command: list[str], expected: str | None) -> None:
    """Mutation check: each real failure shape must be caught, and the fixed shape must pass."""
    offence = _offence("probe", command)
    if expected is None:
        assert offence is None, f"false positive on a correct command: {offence}"
    else:
        assert offence is not None and expected in offence
