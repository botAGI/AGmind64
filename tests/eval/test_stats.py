"""Phase 18 (M11) — uncertainty for a tiny evaluation set (AI-SPEC §6.5).

With n≈15 the risk is not a wrong mean, it is a CONFIDENT mean. Every number the harness prints
carries an interval computed from the actual per-case data, seeded so it reproduces exactly.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


# --- bootstrap --------------------------------------------------------------------------


def test_bootstrap_is_reproducible_for_a_fixed_seed() -> None:
    from agmind.eval.stats import bootstrap_mean

    values = [0.1, 0.4, 0.9, 1.0, 0.0, 0.55, 0.7]
    a = bootstrap_mean(values, seed=1234, resamples=2000)
    b = bootstrap_mean(values, seed=1234, resamples=2000)

    assert (a.low, a.point, a.high) == (b.low, b.point, b.high)
    assert a.seed == 1234
    assert a.resamples == 2000
    assert a.method == "percentile-bootstrap"


def test_bootstrap_point_is_the_sample_mean_and_lies_inside_the_interval() -> None:
    from agmind.eval.stats import bootstrap_mean

    values = [0.2, 0.4, 0.6, 0.8]
    iv = bootstrap_mean(values, seed=7, resamples=2000)

    assert iv.point == pytest.approx(0.5)
    assert iv.low <= iv.point <= iv.high
    assert iv.n == 4


def test_bootstrap_interval_is_wide_for_tiny_n() -> None:
    """The whole point: 3 cases must not look precise."""
    from agmind.eval.stats import bootstrap_mean

    iv = bootstrap_mean([0.0, 0.5, 1.0], seed=7, resamples=4000)
    assert (iv.high - iv.low) > 0.3, "a 3-case interval must be visibly wide"


def test_bootstrap_of_constant_values_is_degenerate_not_broken() -> None:
    from agmind.eval.stats import bootstrap_mean

    iv = bootstrap_mean([0.7, 0.7, 0.7], seed=7, resamples=500)
    assert iv.low == pytest.approx(0.7)
    assert iv.high == pytest.approx(0.7)


def test_bootstrap_single_value_reports_no_spread() -> None:
    from agmind.eval.stats import bootstrap_mean

    iv = bootstrap_mean([0.42], seed=7, resamples=500)
    assert iv.point == pytest.approx(0.42)
    assert iv.low == pytest.approx(0.42)
    assert iv.high == pytest.approx(0.42)
    assert iv.n == 1


def test_bootstrap_refuses_empty_input() -> None:
    """A number without an n is a bug (AI-SPEC §6.5) — so there is no interval over nothing."""
    from agmind.eval.stats import bootstrap_mean

    with pytest.raises(ValueError, match="at least one observation"):
        bootstrap_mean([], seed=7)


def test_bootstrap_rejects_nonpositive_resamples() -> None:
    from agmind.eval.stats import bootstrap_mean

    with pytest.raises(ValueError, match="resamples"):
        bootstrap_mean([0.5], seed=7, resamples=0)


def test_bootstrap_confidence_widens_the_interval() -> None:
    from agmind.eval.stats import bootstrap_mean

    values = [0.1, 0.3, 0.5, 0.7, 0.9]
    narrow = bootstrap_mean(values, seed=3, resamples=4000, confidence=0.80)
    wide = bootstrap_mean(values, seed=3, resamples=4000, confidence=0.99)
    assert (wide.high - wide.low) >= (narrow.high - narrow.low)


# --- Wilson -----------------------------------------------------------------------------


def test_wilson_matches_known_values() -> None:
    """Hand-checked reference: 7/10 at 95% → roughly [0.397, 0.892]."""
    from agmind.eval.stats import wilson_interval

    iv = wilson_interval(7, 10)
    assert iv.point == pytest.approx(0.7)
    assert iv.low == pytest.approx(0.3968, abs=1e-3)
    assert iv.high == pytest.approx(0.8922, abs=1e-3)
    assert iv.method == "wilson"
    assert iv.n == 10


def test_wilson_stays_in_bounds_at_the_extremes() -> None:
    """Where the normal approximation produces impossible intervals, Wilson must not."""
    from agmind.eval.stats import wilson_interval

    zero = wilson_interval(0, 10)
    assert zero.low == pytest.approx(0.0, abs=1e-9)
    assert 0.0 < zero.high < 1.0, "0/10 is not proof of impossibility"

    perfect = wilson_interval(10, 10)
    assert perfect.high == pytest.approx(1.0, abs=1e-9)
    assert 0.0 < perfect.low < 1.0, "10/10 on n=10 must not read as certainty"


def test_wilson_rejects_impossible_counts() -> None:
    from agmind.eval.stats import wilson_interval

    with pytest.raises(ValueError, match="at least one"):
        wilson_interval(0, 0)
    with pytest.raises(ValueError, match="successes"):
        wilson_interval(11, 10)
    with pytest.raises(ValueError, match="successes"):
        wilson_interval(-1, 10)


# --- paired delta -----------------------------------------------------------------------


def test_paired_delta_of_identical_runs_is_zero() -> None:
    from agmind.eval.stats import paired_delta

    values = [0.2, 0.9, 0.5]
    iv = paired_delta(values, values, seed=11, resamples=1000)
    assert iv.point == pytest.approx(0.0)
    assert iv.low == pytest.approx(0.0)
    assert iv.high == pytest.approx(0.0)


def test_paired_delta_detects_a_uniform_shift() -> None:
    from agmind.eval.stats import paired_delta

    before = [0.2, 0.4, 0.6]
    after = [0.4, 0.6, 0.8]
    iv = paired_delta(before, after, seed=11, resamples=2000)
    assert iv.point == pytest.approx(0.2)
    assert iv.low == pytest.approx(0.2)  # every pair shifted identically → no spread


def test_paired_delta_requires_the_same_cases() -> None:
    """Comparing two independent runs' intervals wastes data; pairing is mandatory."""
    from agmind.eval.stats import paired_delta

    with pytest.raises(ValueError, match="same length"):
        paired_delta([0.1, 0.2], [0.1], seed=1)


def test_paired_delta_is_reproducible() -> None:
    from agmind.eval.stats import paired_delta

    b, a = [0.1, 0.5, 0.9, 0.3], [0.2, 0.4, 0.95, 0.35]
    x = paired_delta(b, a, seed=99, resamples=1500)
    y = paired_delta(b, a, seed=99, resamples=1500)
    assert (x.low, x.point, x.high) == (y.low, y.point, y.high)


# --- reporting contract ------------------------------------------------------------------


def test_interval_renders_with_n_and_method() -> None:
    """Formatting is part of the honesty contract: a bare number must be unprintable."""
    from agmind.eval.stats import bootstrap_mean

    text = bootstrap_mean([0.3, 0.6, 0.9], seed=5, resamples=1000).format()
    assert "n=3" in text
    assert "[" in text and "]" in text


def test_interval_to_dict_carries_provenance() -> None:
    from agmind.eval.stats import bootstrap_mean

    d = bootstrap_mean([0.3, 0.6], seed=5, resamples=800).to_dict()
    assert d["n"] == 2
    assert d["method"] == "percentile-bootstrap"
    assert d["seed"] == 5
    assert d["resamples"] == 800
    assert set(d) >= {"point", "low", "high", "n", "method", "seed", "resamples", "confidence"}
