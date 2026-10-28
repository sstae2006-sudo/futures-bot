"""Probabilistic Sharpe Ratio (PSR) / Deflated Sharpe Ratio (DSR).

Bailey, D.H. & Lopez de Prado, M., "The Sharpe Ratio Efficient Frontier"
(2012) and "The Deflated Sharpe Ratio: Correcting for Selection Bias,
Backtest Overfitting and Non-Normality" (2014). Answers a question none of
this project's existing checks do: given how many parameter combinations
were already tried (`research.safety.check_trial_inflation`'s raw count),
what Sharpe ratio would a *skill-less* search be expected to produce just
by chance, and does the winning trial's own Sharpe clear that bar once its
own return distribution's skew/kurtosis are accounted for?

Every formula here was cross-checked against a runnable, published
reference implementation of the same papers (not derived from memory
alone) before being trusted -- see this module's own test suite
(`tests/test_research_dsr.py`), which includes the papers' own worked
numerical example (expected max Sharpe (~3.26) after 1,000 independent
trials with E[SR]=0, V[SR]=1) as a regression check, not just hand-picked
easy cases.

Deliberately pure math: every function takes plain Decimal/int arguments
(mean/stdev/skew/kurtosis/trial counts), never a `BacktestMetrics`,
`TradeStore`, or strategy object -- callable from a test with no backtest
machinery, same separation `research/safety.py`'s own check functions
already follow. `backtest.metrics.BacktestMetrics.pnl_stdev`/`pnl_skew`/
`pnl_kurtosis` are this module's expected input source, but nothing here
imports that module.

All "Sharpe ratio" inputs/outputs here are per-trade and un-annualized
(matching `BacktestMetrics.sharpe_ratio`'s own convention) -- the papers'
formulas are frequency-agnostic as long as every input uses the same
frequency consistently, which per-trade already satisfies without needing
`annualized_sharpe_ratio`'s daily-bucketing machinery.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import NormalDist
from typing import Optional, Sequence

#: Euler-Mascheroni constant, to the precision the reference papers/
#: implementations use.
EULER_MASCHERONI = Decimal("0.5772156649")

_NORMAL = NormalDist()


def _phi(x: Decimal) -> Decimal:
    """Standard normal CDF. Decimal in, Decimal out -- `statistics.NormalDist`
    only operates on floats, so this is the one place this module crosses
    that boundary, kept as narrow as possible."""
    return Decimal(str(_NORMAL.cdf(float(x))))


def _phi_inv(p: Decimal) -> Decimal:
    """Standard normal inverse CDF (probit). Same float boundary as `_phi`."""
    return Decimal(str(_NORMAL.inv_cdf(float(p))))


def sharpe_ratio_stdev(
    sr: Decimal, n: int, skew: Decimal, kurtosis_excess: Decimal,
) -> Optional[Decimal]:
    """Standard deviation of the *estimate* of the Sharpe ratio itself --
    not the stdev of returns (`BacktestMetrics.pnl_stdev`), the stdev of
    the SR statistic's own sampling distribution. This is what turns a
    point estimate ("SR = 1.2") into a probability statement ("P(true SR
    > 0) = 91%") via `probabilistic_sharpe_ratio` below.

    Generalizes beyond the Normal-returns case (where this would just be
    ``sqrt(1/(n-1))``) by incorporating skew and excess kurtosis -- a
    strategy whose returns are negatively skewed or fat-tailed has a less
    reliable Sharpe estimate than the Normal case would suggest, and this
    formula reflects that rather than assuming Normality away.

    ``skew``/``kurtosis_excess`` must be the *population* (biased)
    moments -- `BacktestMetrics.pnl_skew`/`pnl_kurtosis`'s own convention,
    matching the papers' own derivation. ``None`` for fewer than 2
    observations (undefined).
    """
    if n < 2:
        return None
    variance = (
        Decimal("1") + (Decimal("0.5") * sr ** 2) - (skew * sr)
        + ((kurtosis_excess / Decimal("4")) * sr ** 2)
    ) / Decimal(n - 1)
    if variance < 0:
        # Only possible from a pathological (skew, kurtosis) combination
        # far outside anything a real trade series would produce --
        # guarded rather than trusted to `Decimal.sqrt()`'s own error.
        return None
    return variance.sqrt()


def probabilistic_sharpe_ratio(
    sr: Decimal, sr_std: Optional[Decimal], benchmark_sr: Decimal = Decimal("0"),
) -> Optional[Decimal]:
    """PSR(SR*) = P(true Sharpe ratio > ``benchmark_sr``), given the
    observed ``sr`` and its estimation stdev ``sr_std``
    (`sharpe_ratio_stdev` above). A plain Sharpe ratio answers "how good
    does this look"; PSR answers "how sure can I be this isn't noise."

    ``None`` (not a fabricated probability) when ``sr_std`` is ``None`` or
    zero -- the same "undefined, not zero" discipline every other
    `BacktestMetrics` ratio in this codebase already follows.
    """
    if not sr_std or sr_std == 0:
        return None
    return _phi((sr - benchmark_sr) / sr_std)


def expected_maximum_sharpe_ratio(
    trial_count: int, trial_sr_stdev: Decimal, expected_mean_sr: Decimal = Decimal("0"),
) -> Optional[Decimal]:
    """The Sharpe ratio a *skill-less* search would be expected to produce
    as its best result, purely from trying ``trial_count`` independent
    things -- the benchmark `deflated_sharpe_ratio` below tests the actual
    winner against, instead of the naive (and, at any real trial count,
    far too generous) benchmark of zero.

    ``trial_sr_stdev`` is the standard deviation *across* the trial set's
    own Sharpe ratios (how much the different parameter combinations'
    results actually varied) -- a different quantity from
    `sharpe_ratio_stdev`'s "how uncertain is one trial's own SR estimate."
    ``expected_mean_sr`` defaults to 0: the null hypothesis that a random,
    skill-less search centers on zero Sharpe.

    Deliberately does **not** implement the papers' "effective number of
    independent trials" correlation adjustment (`num_independent_trials`
    in the reference implementations this was validated against) --  that
    needs each trial's own full return series to compute pairwise
    correlations, which this project's `optimization_trials` table
    doesn't persist (only summary statistics per trial). ``trial_count``
    is used directly as the independent-trial count, which is the more
    conservative assumption when trials are actually correlated (treating
    correlated trials as independent overstates how much the search bar
    should rise, i.e. this errs toward being *too* skeptical, not too
    lenient).

    ``None`` for ``trial_count < 1`` (undefined -- there is no search to
    speak of). ``trial_count == 1`` returns ``expected_mean_sr`` exactly:
    the formula's first term, ``Phi^-1(1 - 1/N)``, is mathematically
    undefined at ``N=1`` (``1 - 1/1 = 0``, and the inverse normal CDF has
    no finite value at ``p=0``) -- and the underlying idea (the expected
    *maximum* across several draws exceeds their common mean) has nothing
    to say about a single draw in the first place, where the "maximum" is
    just that one value's own expectation.
    """
    if trial_count < 1:
        return None
    if trial_count == 1:
        return expected_mean_sr
    n = Decimal(trial_count)
    e = Decimal(str(2.718281828459045))
    max_z = (
        (Decimal("1") - EULER_MASCHERONI) * _phi_inv(Decimal("1") - Decimal("1") / n)
        + EULER_MASCHERONI * _phi_inv(Decimal("1") - Decimal("1") / (n * e))
    )
    return expected_mean_sr + (trial_sr_stdev * max_z)


def deflated_sharpe_ratio(
    sr: Decimal,
    sr_std: Optional[Decimal],
    trial_count: int,
    trial_sr_stdev: Decimal,
    expected_mean_sr: Decimal = Decimal("0"),
) -> Optional[Decimal]:
    """DSR = PSR(SR0), where SR0 (`expected_maximum_sharpe_ratio`) is the
    benchmark a skill-less search this size would be expected to clear --
    the multiple-testing-aware answer to "is this result real," as opposed
    to `probabilistic_sharpe_ratio`'s single-trial answer to "is this
    result different from zero." A high PSR against a benchmark of 0 and a
    low DSR against the same trial's own search history is not a
    contradiction -- it's exactly the failure mode this metric exists to
    catch (today's own 27-combination `trend_pullback` tuning sweep is a
    live example, per `docs/EXPERIMENT_SWEEP_2026-08-05.md`).

    ``None`` when either input probability is undefined (``sr_std`` is
    ``None``/zero, or ``trial_count < 1``).
    """
    sr0 = expected_maximum_sharpe_ratio(trial_count, trial_sr_stdev, expected_mean_sr)
    if sr0 is None:
        return None
    return probabilistic_sharpe_ratio(sr, sr_std, benchmark_sr=sr0)


def minimum_track_record_length(
    sr: Decimal, sr_std: Optional[Decimal], n: int,
    benchmark_sr: Decimal = Decimal("0"), confidence: Decimal = Decimal("0.95"),
) -> Optional[Decimal]:
    """How many observations (at this same ``sr``/``sr_std``) would be
    needed for PSR(``benchmark_sr``) to reach ``confidence`` -- "how much
    more evidence would this exact result need before it's trustworthy,"
    a companion question to PSR/DSR's "is what I already have enough."

    ``None`` when ``sr <= benchmark_sr`` (no amount of additional data at
    an unchanged, non-improving Sharpe would ever clear the bar) or
    ``sr_std`` is undefined.
    """
    if not sr_std or sr <= benchmark_sr:
        return None
    z = _phi_inv(confidence)
    return Decimal("1") + (sr_std ** 2 * Decimal(n - 1)) * (z / (sr - benchmark_sr)) ** 2


def trial_sharpe_stdev(trial_sharpe_ratios: Sequence[Decimal]) -> Optional[Decimal]:
    """Sample standard deviation across a set of trials' own Sharpe
    ratios -- the ``trial_sr_stdev`` input `expected_maximum_sharpe_ratio`/
    `deflated_sharpe_ratio` need. A thin, named wrapper (rather than
    inlining this at every call site) so "which stdev convention" is
    answered in exactly one place: sample (ddof=1), matching
    `BacktestMetrics.pnl_stdev`'s own convention, not the population
    moments `pnl_skew`/`pnl_kurtosis` use.

    ``None`` with fewer than 2 trial Sharpe ratios.
    """
    if len(trial_sharpe_ratios) < 2:
        return None
    n = len(trial_sharpe_ratios)
    mean = sum(trial_sharpe_ratios) / Decimal(n)
    variance = sum((s - mean) ** 2 for s in trial_sharpe_ratios) / Decimal(n - 1)
    return variance.sqrt()
