"""Tests for `research.safety`: the overfitting/fragility checks and the
required "do not trust this result because..." report.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.metrics import BacktestMetrics
from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar, Position, Side, Signal, Trade
from futures_bot.research.safety import (
    build_safety_report,
    check_commission_sensitivity,
    check_degradation,
    check_parameter_fitting,
    check_trade_counts,
    check_unrealistic_gains,
)
from futures_bot.strategy.base import Strategy


def make_metrics(pnls: Sequence[Decimal], starting_equity: Decimal = Decimal("5000")) -> BacktestMetrics:
    """Synthetic BacktestMetrics with one trade per given net P&L (zero
    commission, so gross == net) -- fast, exact control over every derived
    figure without needing to run a real backtest."""
    when = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    trades = []
    for i, pnl in enumerate(pnls):
        trades.append(
            Trade(
                side=Side.LONG, quantity=1,
                entry_price=Decimal("7500"), exit_price=Decimal("7500") + pnl,
                entry_time=when + timedelta(minutes=i * 10), exit_time=when + timedelta(minutes=i * 10 + 5),
                gross_pnl=pnl, commission=Decimal("0"), exit_reason="take_profit",
            )
        )
    return BacktestMetrics(
        trades=trades, starting_equity=starting_equity, bars_processed=len(pnls) * 10,
        first_bar=when, last_bar=when + timedelta(minutes=len(pnls) * 10),
    )


class TestCheckTradeCounts:
    def test_flags_low_training_trade_count(self):
        train = make_metrics([Decimal("10")] * 5)
        findings = check_trade_counts(train, validation=None)
        assert any("training trades" in f.message for f in findings)

    def test_missing_validation_is_severe(self):
        train = make_metrics([Decimal("10")] * 50)
        findings = check_trade_counts(train, validation=None)
        assert any(f.severe and "out-of-sample" in f.message for f in findings)

    def test_flags_low_validation_trade_count(self):
        train = make_metrics([Decimal("10")] * 50)
        validation = make_metrics([Decimal("5")] * 8)
        findings = check_trade_counts(train, validation)
        assert any("validation trades" in f.message for f in findings)

    def test_healthy_counts_produce_no_findings(self):
        train = make_metrics([Decimal("10")] * 50)
        validation = make_metrics([Decimal("5")] * 30)
        assert check_trade_counts(train, validation) == []


class TestCheckDegradation:
    def test_no_validation_means_no_findings(self):
        train = make_metrics([Decimal("10")] * 20)
        assert check_degradation(train, None) == []

    def test_profitable_to_loss_is_severe(self):
        train = make_metrics([Decimal("10")] * 20)  # net +$200
        validation = make_metrics([Decimal("-5")] * 20)  # net -$100
        findings = check_degradation(train, validation)
        assert any(f.severe for f in findings)

    def test_matches_worked_example_degradation(self):
        """Mirrors the spec's worked example: train $840 -> validation $390 is ~54% down."""
        train = make_metrics([Decimal("84")] * 10)  # net $840
        validation = make_metrics([Decimal("39")] * 10)  # net $390, ~54% degradation
        findings = check_degradation(train, validation)
        assert any("degraded" in f.message for f in findings)

    def test_mild_degradation_is_not_severe(self):
        train = make_metrics([Decimal("100")] * 10)
        validation = make_metrics([Decimal("85")] * 10)  # 15% degradation, below warning threshold
        findings = check_degradation(train, validation)
        assert findings == []

    def test_profit_factor_collapse_is_severe(self):
        train = make_metrics([Decimal("50"), Decimal("50"), Decimal("-10")])  # PF > 1
        validation = make_metrics([Decimal("10"), Decimal("-20"), Decimal("-20")])  # PF < 1
        findings = check_degradation(train, validation)
        assert any(f.severe and "profit factor" in f.message.lower() for f in findings)


class TestCheckUnrealisticGains:
    def test_no_losing_trades_is_severe(self):
        metrics = make_metrics([Decimal("10")] * 10)
        findings = check_unrealistic_gains(metrics)
        assert any(f.severe and "no losing trades" in f.message.lower() for f in findings)

    def test_suspiciously_high_profit_factor_flagged(self):
        metrics = make_metrics([Decimal("100")] * 8 + [Decimal("-5")] * 2)  # PF = 800/10 = 80
        findings = check_unrealistic_gains(metrics)
        assert any("profit factor" in f.message.lower() for f in findings)

    def test_concentrated_single_trade_flagged(self):
        metrics = make_metrics([Decimal("1000")] + [Decimal("10")] * 10)
        findings = check_unrealistic_gains(metrics)
        assert any("single best trade" in f.message for f in findings)

    def test_ordinary_result_produces_no_findings(self):
        pnls = [Decimal("10"), Decimal("-8"), Decimal("12"), Decimal("-9")] * 10
        findings = check_unrealistic_gains(make_metrics(pnls))
        assert findings == []


class TestCheckParameterFitting:
    def test_isolated_spike_is_flagged(self):
        winner = Decimal("500")
        others = [Decimal("-100"), Decimal("-50"), Decimal("10"), Decimal("-20"), Decimal("5"), Decimal("-80")]
        findings = check_parameter_fitting(winner, others)
        assert len(findings) == 1
        assert "isolated spike" in findings[0].message

    def test_robust_region_is_not_flagged(self):
        winner = Decimal("500")
        others = [Decimal("480"), Decimal("450"), Decimal("470"), Decimal("300"), Decimal("460")]
        assert check_parameter_fitting(winner, others) == []

    def test_negative_winner_is_not_flagged(self):
        # A losing "winner" has no meaningful robust-region concept.
        assert check_parameter_fitting(Decimal("-10"), [Decimal("-50"), Decimal("-100")]) == []

    def test_too_few_other_trials_skips_the_check(self):
        assert check_parameter_fitting(Decimal("500"), [Decimal("-100")]) == []


class _FixedEdgeStrategy(Strategy):
    """Enters long, exits exactly one bar later, every other bar -- gives a
    deterministic gross P&L per round turn so commission-sensitivity math is
    exact rather than approximate."""

    warmup_bars = 0

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if position is None:
            return self.enter_long("enter")
        return self.exit("exit one bar later")


def _commission_test_settings(commission_per_side: Decimal) -> Settings:
    return Settings(
        contract="MES", mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1, stop_loss_points=Decimal("50"), take_profit_points=Decimal("50"),
            daily_max_loss=Decimal("10000"), max_trades_per_session=1000, account_size=Decimal("5000"),
        ),
        session=SessionSettings(start_ct="00:00", end_ct="23:59"),
        broker=BrokerSettings(
            starting_cash=Decimal("5000"), slippage_ticks=Decimal("0"), commission_per_side=commission_per_side
        ),
    )


def _commission_test_bars(n: int) -> list[Bar]:
    # +1 point (=$5/contract on MES) every bar -- a fixed, known gross edge
    # per round turn, so doubling commission has an exact, predictable effect.
    start = datetime(2026, 1, 6, 9, 0, tzinfo=CME_TZ)  # a Tuesday, well inside the wide-open window
    bars, price = [], Decimal("7500")
    for i in range(n):
        price += Decimal("1")
        bars.append(
            Bar(timestamp=start + timedelta(minutes=i), open=price, high=price + Decimal("0.5"),
                low=price - Decimal("0.5"), close=price, volume=500)
        )
    return bars


class TestCheckCommissionSensitivity:
    def test_flips_to_negative_is_flagged_severe(self):
        # Gross edge per round turn is $5 (1pt * $5). commission_per_side=$2
        # -> baseline net = $5 - $4 = $1 (profitable). Doubled -> $5 - $8 = -$3.
        settings = _commission_test_settings(Decimal("2.00"))
        bars = _commission_test_bars(40)
        findings = check_commission_sensitivity(settings, lambda: _FixedEdgeStrategy(MES), bars)
        assert any(f.severe and "flips" in f.message for f in findings)

    def test_insensitive_edge_is_not_flagged(self):
        # commission_per_side=$0.10 -> baseline net = $5 - $0.20 = $4.80,
        # doubled -> $5 - $0.40 = $4.60 -- barely moves.
        settings = _commission_test_settings(Decimal("0.10"))
        bars = _commission_test_bars(40)
        findings = check_commission_sensitivity(settings, lambda: _FixedEdgeStrategy(MES), bars)
        assert findings == []


class TestBuildSafetyReport:
    def test_no_findings_means_high_confidence(self):
        train = make_metrics([Decimal("10"), Decimal("-5")] * 25)
        validation = make_metrics([Decimal("10"), Decimal("-5")] * 15)
        report = build_safety_report(train=train, validation=validation)
        assert report.confidence == "High"
        assert report.findings == []
        assert "No warnings" in report.format()

    def test_matches_worked_example_shape(self):
        """Reproduces the spec's worked example: train $840 net, 74
        validation trades netting $390 net -- Medium-or-worse confidence,
        with a "degraded" finding among the reasons."""
        train = make_metrics([Decimal("84")] * 10)  # $840 net
        per_trade = Decimal("390") / Decimal("74")
        validation = make_metrics([per_trade] * 74)  # $390 net across 74 trades

        report = build_safety_report(train=train, validation=validation)

        assert report.confidence in ("Medium", "Low")
        joined = " ".join(report.reasons)
        assert "degraded" in joined

    def test_severe_finding_forces_low_confidence(self):
        train = make_metrics([Decimal("10")] * 20)
        validation = make_metrics([Decimal("-5")] * 20)  # profitable -> loss: severe
        report = build_safety_report(train=train, validation=validation)
        assert report.confidence == "Low"

    def test_parameter_fitting_included_when_scores_given(self):
        train = make_metrics([Decimal("10"), Decimal("-3")] * 25)
        validation = make_metrics([Decimal("10"), Decimal("-3")] * 15)
        report = build_safety_report(
            train=train, validation=validation,
            winner_score=Decimal("500"),
            all_scores=[Decimal("-100"), Decimal("-50"), Decimal("10"), Decimal("-20"), Decimal("5"), Decimal("-80")],
        )
        assert any("isolated spike" in r for r in report.reasons)
