"""Tests for `research.dsr`: Probabilistic/Deflated Sharpe Ratio.

Every formula was cross-checked against a runnable, published reference
implementation of Bailey & Lopez de Prado's papers before being trusted
(not derived from memory alone) -- see this module's own docstring. The
`TestExpectedMaximumSharpeRatio::test_matches_the_papers_own_worked_example`
case below is the paper's own cited numerical example, not a hand-picked
easy case.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_bot.research.dsr import (
    deflated_sharpe_ratio,
    expected_maximum_sharpe_ratio,
    minimum_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_ratio_stdev,
    trial_sharpe_stdev,
)


class TestSharpeRatioStdev:
    def test_none_with_fewer_than_two_observations(self):
        assert sharpe_ratio_stdev(Decimal("1"), 1, Decimal("0"), Decimal("0")) is None

    def test_reduces_to_the_normal_case_at_zero_sharpe(self):
        """With skew=0, excess kurtosis=0 (Normal returns) and sr=0, the
        skew/kurtosis correction terms vanish and this must equal the
        textbook sqrt(1/(n-1))."""
        sd = sharpe_ratio_stdev(Decimal("0"), 101, Decimal("0"), Decimal("0"))
        assert sd == Decimal("0.01").sqrt()

    def test_increases_with_fatter_tails(self):
        """Higher excess kurtosis (fatter tails) at a positive Sharpe
        makes the SR estimate less certain, not more."""
        normal_sd = sharpe_ratio_stdev(Decimal("1"), 100, Decimal("0"), Decimal("0"))
        fat_tailed_sd = sharpe_ratio_stdev(Decimal("1"), 100, Decimal("0"), Decimal("5"))
        assert fat_tailed_sd > normal_sd

    def test_negative_skew_increases_uncertainty_at_positive_sharpe(self):
        """A negatively-skewed return series (occasional large losses) is
        less trustworthy at a given positive Sharpe than a symmetric one."""
        symmetric_sd = sharpe_ratio_stdev(Decimal("1"), 100, Decimal("0"), Decimal("0"))
        neg_skew_sd = sharpe_ratio_stdev(Decimal("1"), 100, Decimal("-1"), Decimal("0"))
        assert neg_skew_sd > symmetric_sd


class TestProbabilisticSharpeRatio:
    def test_none_without_a_stdev(self):
        assert probabilistic_sharpe_ratio(Decimal("1"), None) is None
        assert probabilistic_sharpe_ratio(Decimal("1"), Decimal("0")) is None

    def test_half_when_sr_equals_benchmark(self):
        psr = probabilistic_sharpe_ratio(Decimal("1.0"), Decimal("2.0"), benchmark_sr=Decimal("1.0"))
        assert psr == Decimal("0.5")

    def test_above_half_when_sr_exceeds_benchmark(self):
        psr = probabilistic_sharpe_ratio(Decimal("2.0"), Decimal("1.0"), benchmark_sr=Decimal("0"))
        assert psr > Decimal("0.5")

    def test_below_half_when_sr_is_under_benchmark(self):
        psr = probabilistic_sharpe_ratio(Decimal("0.5"), Decimal("1.0"), benchmark_sr=Decimal("1.0"))
        assert psr < Decimal("0.5")

    def test_result_is_a_valid_probability(self):
        psr = probabilistic_sharpe_ratio(Decimal("3"), Decimal("0.5"), benchmark_sr=Decimal("0"))
        assert Decimal("0") <= psr <= Decimal("1")


class TestExpectedMaximumSharpeRatio:
    def test_none_with_zero_or_negative_trials(self):
        assert expected_maximum_sharpe_ratio(0, Decimal("1")) is None
        assert expected_maximum_sharpe_ratio(-5, Decimal("1")) is None

    def test_matches_the_papers_own_worked_example(self):
        """Bailey & Lopez de Prado's own cited example: E[SR]=0, V[SR]=1,
        N=1,000 independent trials -> expected max SR ~= 3.26, even though
        the true SR of every trial is zero. Validated to 2 decimal places
        against the published figure, not just "close enough" hand-waving."""
        result = expected_maximum_sharpe_ratio(1000, Decimal("1"), Decimal("0"))
        assert round(result, 2) == Decimal("3.26")

    def test_grows_with_more_trials(self):
        """More trials raise the bar -- the entire point of this metric."""
        few = expected_maximum_sharpe_ratio(10, Decimal("1"))
        many = expected_maximum_sharpe_ratio(1000, Decimal("1"))
        assert many > few

    def test_grows_with_more_dispersion_across_trials(self):
        low_dispersion = expected_maximum_sharpe_ratio(100, Decimal("0.5"))
        high_dispersion = expected_maximum_sharpe_ratio(100, Decimal("2"))
        assert high_dispersion > low_dispersion

    def test_single_trial_equals_the_mean_exactly(self):
        """With only 1 trial, there was no real search: Phi^-1(1 - 1/1) is
        undefined (p=0), and "expected maximum of one draw" is just that
        draw's own mean -- not inflated by any extreme-value effect."""
        assert expected_maximum_sharpe_ratio(1, Decimal("1"), Decimal("0")) == Decimal("0")
        assert expected_maximum_sharpe_ratio(1, Decimal("1"), Decimal("1.5")) == Decimal("1.5")


class TestDeflatedSharpeRatio:
    def test_none_without_a_stdev(self):
        assert deflated_sharpe_ratio(Decimal("2"), None, 10, Decimal("1")) is None

    def test_none_with_zero_trials(self):
        assert deflated_sharpe_ratio(Decimal("2"), Decimal("0.5"), 0, Decimal("1")) is None

    def test_the_same_sharpe_ratio_scores_lower_with_more_prior_trials(self):
        """The exact failure mode this metric exists to catch: an
        unchanged winning result should look progressively less
        trustworthy as the search behind it grows."""
        few_trials = deflated_sharpe_ratio(Decimal("1.5"), Decimal("0.3"), 5, Decimal("0.5"))
        many_trials = deflated_sharpe_ratio(Decimal("1.5"), Decimal("0.3"), 200, Decimal("0.5"))
        assert many_trials < few_trials

    def test_is_a_valid_probability(self):
        result = deflated_sharpe_ratio(Decimal("1.5"), Decimal("0.3"), 27, Decimal("0.4"))
        assert Decimal("0") <= result <= Decimal("1")

    def test_todays_isolated_spike_reads_as_untrustworthy(self):
        """A loose reconstruction of docs/EXPERIMENT_SWEEP_2026-08-05.md's
        27-combination trend_pullback sweep: a winner whose Sharpe isn't
        dramatically better than the rest of a 27-trial grid should
        deflate hard once 27 trials are accounted for."""
        # Winning trial's SR only modestly above the grid's own dispersion.
        result = deflated_sharpe_ratio(
            sr=Decimal("0.3"), sr_std=Decimal("0.15"),
            trial_count=27, trial_sr_stdev=Decimal("0.25"),
        )
        assert result < Decimal("0.5")


class TestMinimumTrackRecordLength:
    def test_none_without_a_stdev(self):
        assert minimum_track_record_length(Decimal("1"), None, 100) is None

    def test_none_when_sr_does_not_exceed_benchmark(self):
        assert minimum_track_record_length(Decimal("0"), Decimal("1"), 100, benchmark_sr=Decimal("0")) is None
        assert minimum_track_record_length(Decimal("-1"), Decimal("1"), 100, benchmark_sr=Decimal("0")) is None

    def test_positive_when_sr_exceeds_benchmark(self):
        result = minimum_track_record_length(Decimal("1"), Decimal("0.5"), 50, benchmark_sr=Decimal("0"))
        assert result > 0

    def test_higher_confidence_requires_a_longer_track_record(self):
        looser = minimum_track_record_length(
            Decimal("1"), Decimal("0.5"), 50, benchmark_sr=Decimal("0"), confidence=Decimal("0.90"),
        )
        stricter = minimum_track_record_length(
            Decimal("1"), Decimal("0.5"), 50, benchmark_sr=Decimal("0"), confidence=Decimal("0.99"),
        )
        assert stricter > looser


class TestTrialSharpeStdev:
    def test_none_with_fewer_than_two_trials(self):
        assert trial_sharpe_stdev([Decimal("1")]) is None
        assert trial_sharpe_stdev([]) is None

    def test_zero_for_identical_trial_sharpe_ratios(self):
        assert trial_sharpe_stdev([Decimal("1"), Decimal("1"), Decimal("1")]) == Decimal("0")

    def test_matches_a_hand_computed_sample_stdev(self):
        # mean=2, variance (ddof=1) = ((1)^2 + 0 + (1)^2) / 2 = 1
        assert trial_sharpe_stdev([Decimal("1"), Decimal("2"), Decimal("3")]) == Decimal("1")
