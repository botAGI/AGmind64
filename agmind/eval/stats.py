"""Uncertainty for a small evaluation set (AI-SPEC §6.5).

With n≈15 cases the failure mode is not an inaccurate mean — it is a mean that *looks* precise.
Every headline number the harness prints is accompanied by an interval computed from the actual
per-case data of that run, with the seed recorded so the interval reproduces exactly.

Why a bootstrap rather than a closed-form power/CI table: ``anchor_recall@k``, ``ndcg@k`` and
``mrr@k`` are bounded *continuous* per-case quantities, not Bernoulli trials. Applying a
proportion formula to their mean and presenting it as "computed from the actual n" would be
precisely the overclaiming this module exists to prevent. Wilson is offered separately and is
correct only for genuinely binary rates (e.g. ``anchor_hit@k`` treated as a success rate).

Clustering: resampling happens over CASES. Chunk-level quantities within a case are not
independent, so a chunk-level bootstrap would understate the interval. Callers pass per-case
vectors and report ``n`` in cases; where a chunk count is also relevant it is printed as a
second, separately-labelled number.

``numpy`` is a core dependency; ``scipy`` lives only in extras and is unavailable in the unit
tier, so the Wilson z-quantile is computed from an inverse-erf identity in the stdlib instead.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # numpy is a core dependency but imported lazily (see module docstring)
    import numpy as np
    from numpy.typing import NDArray

_DEFAULT_RESAMPLES = 10_000
_DEFAULT_CONFIDENCE = 0.95


@dataclass(frozen=True)
class Interval:
    """A point estimate that cannot be printed without its uncertainty and its ``n``."""

    point: float
    low: float
    high: float
    n: int
    method: str
    confidence: float
    seed: int | None = None
    resamples: int | None = None

    def format(self, *, digits: int = 3) -> str:
        """Render as ``0.714 [0.397-0.892] n=10``.

        The ``n`` is not optional decoration: a rate without its denominator is the single
        easiest way for a caption to outlive its caveat, so the formatter always emits it.
        """
        return f"{self.point:.{digits}f} [{self.low:.{digits}f}-{self.high:.{digits}f}] n={self.n}"

    def to_dict(self) -> dict[str, object]:
        return {
            "point": self.point,
            "low": self.low,
            "high": self.high,
            "n": self.n,
            "method": self.method,
            "confidence": self.confidence,
            "seed": self.seed,
            "resamples": self.resamples,
        }


def _validate(values: Sequence[float], resamples: int, confidence: float) -> None:
    if len(values) < 1:
        raise ValueError("need at least one observation to state an interval")
    if resamples < 1:
        raise ValueError(f"resamples must be >= 1, got {resamples}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")


def _percentile_ci(samples: NDArray[np.float64], confidence: float) -> tuple[float, float]:
    import numpy as np

    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(samples, [alpha, 1.0 - alpha])
    return float(low), float(high)


def bootstrap_mean(
    values: Sequence[float],
    *,
    seed: int,
    resamples: int = _DEFAULT_RESAMPLES,
    confidence: float = _DEFAULT_CONFIDENCE,
) -> Interval:
    """Seeded percentile bootstrap of the mean over ``values`` (one entry per case)."""
    _validate(values, resamples, confidence)
    import numpy as np

    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    # Resample CASES with replacement; each draw is a full synthetic run of the same size.
    draws = rng.integers(0, arr.size, size=(resamples, arr.size))
    means = arr[draws].mean(axis=1)
    low, high = _percentile_ci(means, confidence)

    return Interval(
        point=float(arr.mean()),
        low=low,
        high=high,
        n=int(arr.size),
        method="percentile-bootstrap",
        confidence=confidence,
        seed=seed,
        resamples=resamples,
    )


def paired_delta(
    before: Sequence[float],
    after: Sequence[float],
    *,
    seed: int,
    resamples: int = _DEFAULT_RESAMPLES,
    confidence: float = _DEFAULT_CONFIDENCE,
) -> Interval:
    """Bootstrapped mean difference ``after - before`` over the SAME cases.

    A/B is always paired. Comparing two independent runs' intervals throws away the pairing and
    needs far more data to see the same effect; it also invites the two conclusions this report
    is forbidden from drawing ("intervals overlap ⇒ no difference", "means differ ⇒ difference").
    """
    if len(before) != len(after):
        raise ValueError(
            f"paired comparison needs the same length on both sides: {len(before)} vs {len(after)}"
        )
    _validate(before, resamples, confidence)
    import numpy as np

    deltas = np.asarray(after, dtype=float) - np.asarray(before, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, deltas.size, size=(resamples, deltas.size))
    means = deltas[draws].mean(axis=1)
    low, high = _percentile_ci(means, confidence)

    return Interval(
        point=float(deltas.mean()),
        low=low,
        high=high,
        n=int(deltas.size),
        method="paired-percentile-bootstrap",
        confidence=confidence,
        seed=seed,
        resamples=resamples,
    )


def _z_for(confidence: float) -> float:
    """Two-sided normal quantile via the inverse error function (stdlib only)."""
    return math.sqrt(2.0) * _inv_erf(confidence)


def _inv_erf(x: float) -> float:
    """Inverse of ``math.erf`` by Newton refinement on a Winitzki initial guess.

    Accurate to well under 1e-12 across the confidence levels we use, which is orders of
    magnitude tighter than the interval itself — and avoids pulling scipy into the unit tier.
    """
    if not -1.0 < x < 1.0:
        raise ValueError(f"inv_erf domain is (-1, 1), got {x}")
    if x == 0.0:
        return 0.0

    sign = 1.0 if x > 0 else -1.0
    a = 0.147
    ln1mx2 = math.log(1.0 - x * x)
    term = 2.0 / (math.pi * a) + ln1mx2 / 2.0
    guess = sign * math.sqrt(math.sqrt(term * term - ln1mx2 / a) - term)

    for _ in range(4):  # Newton on erf(y) - x
        err = math.erf(guess) - x
        deriv = 2.0 / math.sqrt(math.pi) * math.exp(-guess * guess)
        if deriv == 0.0:  # pragma: no cover - unreachable for our inputs
            break
        guess -= err / deriv
    return guess


def wilson_interval(
    successes: int,
    n: int,
    *,
    confidence: float = _DEFAULT_CONFIDENCE,
) -> Interval:
    """Wilson score interval for a genuinely binary rate.

    Chosen over the normal approximation because it stays inside [0, 1] and remains sensible at
    small ``n`` and near 0 or 1 — exactly where a 15-case evaluation set lives. A run scoring
    10/10 must not be reported as certainty.
    """
    if n < 1:
        raise ValueError("need at least one observation to state an interval")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be within [0, n]: got {successes} of {n}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")

    z = _z_for(confidence)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    spread = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))

    return Interval(
        point=p,
        low=max(0.0, centre - spread),
        high=min(1.0, centre + spread),
        n=n,
        method="wilson",
        confidence=confidence,
    )


__all__ = ["Interval", "bootstrap_mean", "paired_delta", "wilson_interval"]
