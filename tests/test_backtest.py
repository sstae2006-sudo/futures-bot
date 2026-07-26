"""Backtest tests.

Two of these guard against failures that would not announce themselves:

* **Lookahead** — a strategy that can see future bars produces a backtest that
  is profitable and untradeable, and nothing in the output reveals it.
* **State isolation** — a backtest sharing the live state file could trip the
  production kill switch by replaying a losing period.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

import pytest

from futures_bot.backtest.data import DataError, load_bars
from futures_bot.backtest.metrics import BacktestMetrics
from futures_bot.backtest.runner import CountingJournal, run_backtest, split_bars
from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar, Position, Side, Signal, Trade
from futures_bot.state import StateStore
from futures_bot.strategy.base import Strategy


def make_settings(**overrides) -> Settings:
    base = dict(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1,
            stop_loss_points=Decimal("10"),
            take_profit_points=Decimal("20"),
            daily_max_loss=Decimal("500"),
            max_trades_per_session=50,
            account_size=Decimal("5000"),
        ),
        session=SessionSettings(start_ct="08:30", end_ct="15:00"),
        broker=BrokerSettings(starting_cash=Decimal("5000")),
    )
    base.update(overrides)
    return Settings(**base)


def make_bars(n: int, start_price: Decimal = Decimal("7500")) -> list[Bar]:
    start = datetime(2026, 7, 21, 9, 0, tzinfo=CME_TZ)
    bars = []
    price = start_price
    for i in range(n):
        price += Decimal("2")
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                volume=500,
            )
        )
    return bars


class SpyStrategy(Strategy):
    """Records how much history it was shown on each call."""

    warmup_bars = 0

    def __init__(self, contract, **params):
        super().__init__(contract, **params)
        self.observed_lengths: list[int] = []
        self.last_timestamps: list[datetime] = []

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        self.observed_lengths.append(len(bars))
        self.last_timestamps.append(bars[-1].timestamp)
        return self.hold("spy")


class TestNoLookahead:
    def test_strategy_sees_only_history_up_to_current_bar(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        strategy = SpyStrategy(MES)
        bars = make_bars(50)

        run_backtest(settings, strategy, bars, journal_dir=tmp_path)

        # On call i the strategy must see exactly i+1 bars, never more.
        assert strategy.observed_lengths == list(range(1, 51))

    def test_last_bar_is_always_the_current_one(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        strategy = SpyStrategy(MES)
        bars = make_bars(20)

        run_backtest(settings, strategy, bars, journal_dir=tmp_path)

        expected = [b.timestamp for b in bars]
        assert strategy.last_timestamps == expected


class TestStateIsolation:
    def test_backtest_does_not_touch_configured_state_file(self, tmp_path):
        """Replaying a losing period must not halt the live bot."""
        live_state = tmp_path / "live_state.json"
        store = StateStore(live_state)
        store.record_pnl(datetime(2026, 7, 21).date(), Decimal("0"))
        before = live_state.read_text()

        settings = make_settings(
            state_file=live_state,
            logging={"directory": tmp_path, "level": "WARNING"},
        )
        run_backtest(settings, SpyStrategy(MES), make_bars(30), journal_dir=tmp_path)

        assert live_state.read_text() == before, "backtest wrote to the live state file"


class _EntersOnceWithMetadata(Strategy):
    """Enters a single long position on the first bar, carrying metadata --
    a minimal stand-in for a real strategy's entry signal."""

    warmup_bars = 0

    def __init__(self, contract, **params):
        super().__init__(contract, **params)
        self.entered = False

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if position is None and not self.entered:
            self.entered = True
            return self.enter_long("test entry", note="hello", score=1.5)
        return self.hold("holding" if position else "done")


class TestCountingJournalEntries:
    """`CountingJournal.entries` is what `research.features.build_trade_records`
    joins against `metrics.trades` -- Phase 3's foundation. Covered here
    because it's `backtest.runner`'s own contract, not a research-layer
    concern; `research`'s tests assume this wiring already works."""

    def test_injected_journal_buffers_acted_entries(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        journal = CountingJournal(tmp_path, settings.logging.log_every_decision)

        metrics = run_backtest(
            settings, _EntersOnceWithMetadata(MES), make_bars(10), journal_dir=tmp_path, journal=journal
        )

        assert len(journal.entries) == metrics.trade_count == 1
        entry = journal.entries[0]
        assert entry.side == "long"
        assert entry.reason == "test entry"
        assert entry.metadata == {"note": "hello", "score": 1.5}

    def test_holds_and_blocked_signals_are_not_buffered_as_entries(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        journal = CountingJournal(tmp_path, settings.logging.log_every_decision)

        run_backtest(settings, SpyStrategy(MES), make_bars(20), journal_dir=tmp_path, journal=journal)

        assert journal.entries == []  # SpyStrategy only ever holds

    def test_run_backtest_without_a_journal_still_works(self, tmp_path):
        """Default behavior (no injected journal) must be unaffected --
        confirms the new parameter is additive, not a required change."""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(settings, _EntersOnceWithMetadata(MES), make_bars(10), journal_dir=tmp_path)
        assert metrics.trade_count == 1


class TestSplit:
    def test_split_is_chronological(self):
        bars = make_bars(100)
        train, test = split_bars(bars, Decimal("0.7"))
        assert len(train) == 70 and len(test) == 30
        assert train[-1].timestamp < test[0].timestamp

    def test_rejects_invalid_fraction(self):
        with pytest.raises(ValueError):
            split_bars(make_bars(10), Decimal("1.5"))


class TestChronologicalGuard:
    """Audit fix: overlapping datasets (e.g. two contracts' data stitched
    across a rollover) must not silently replay a duplicated moment twice.
    `_check_chronological` used to only reject strictly-decreasing
    timestamps; equal adjacent timestamps sailed through uncaught."""

    def test_duplicate_adjacent_timestamp_is_rejected(self, tmp_path):
        bars = make_bars(5)
        duplicated = list(bars[:2]) + [bars[1]] + list(bars[2:])  # bars[1] repeated
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        with pytest.raises(ValueError, match="duplicates"):
            run_backtest(settings, SpyStrategy(MES), duplicated, journal_dir=tmp_path)

    def test_out_of_order_timestamp_is_still_rejected(self, tmp_path):
        bars = make_bars(5)
        reordered = [bars[0], bars[2], bars[1], bars[3], bars[4]]
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        with pytest.raises(ValueError, match="comes before"):
            run_backtest(settings, SpyStrategy(MES), reordered, journal_dir=tmp_path)

    def test_strictly_increasing_bars_pass(self, tmp_path):
        bars = make_bars(5)
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        run_backtest(settings, SpyStrategy(MES), bars, journal_dir=tmp_path)  # must not raise


class _EntersOnceWithExplicitBracket(Strategy):
    """Enters a single long position with an explicit stop/target, so the
    fill outcome is deterministic against a known bar sequence -- unlike
    the bundled strategies' ATR/indicator-driven levels."""

    warmup_bars = 0

    def __init__(self, contract, stop_loss_offset: Decimal, take_profit_offset: Decimal, **params):
        super().__init__(contract, **params)
        self.stop_loss_offset = stop_loss_offset
        self.take_profit_offset = take_profit_offset
        self.entered = False

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if position is None and not self.entered:
            self.entered = True
            price = bars[-1].close
            return self.enter_long(
                "test entry",
                stop_loss=price - self.stop_loss_offset,
                take_profit=price + self.take_profit_offset,
            )
        return self.hold("done")


class TestSessionSummaries:
    """Integration proof that the daily-session rules (profit target, in
    this case) flow all the way through a real `run_backtest()` call into
    `BacktestMetrics.session_summaries` -- not just the RiskManager unit
    tests in test_risk.py."""

    def test_profit_target_reflected_in_session_summaries(self, tmp_path):
        risk = RiskSettings(
            contracts_per_trade=1, stop_loss_points=Decimal("100"), take_profit_points=Decimal("2"),
            daily_max_loss=Decimal("500"), max_trades_per_session=50, account_size=Decimal("5000"),
            profit_target=Decimal("5"),
        )
        settings = make_settings(risk=risk, logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(3)  # bar[1]'s high clears entry+2 -- see make_bars' price path

        metrics = run_backtest(
            settings, _EntersOnceWithExplicitBracket(MES, Decimal("100"), Decimal("2")),
            bars, journal_dir=tmp_path,
        )

        assert metrics.trade_count == 1
        assert metrics.trades[0].exit_reason == "take_profit"
        assert len(metrics.session_summaries) == 1
        summary = metrics.session_summaries[0]
        assert summary.stopped_on_profit is True
        assert summary.halted is True
        assert summary.target_hit_at is not None
        assert summary.starting_balance == settings.broker.starting_cash
        assert summary.session_pnl > 0

    def test_no_session_rule_configured_still_produces_a_summary(self, tmp_path):
        """session_summaries is always populated (one day, unhalted) even
        when none of the optional session rules are set -- proves this is
        additive, not opt-in machinery bolted on separately."""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_bars(10)

        metrics = run_backtest(settings, SpyStrategy(MES), bars, journal_dir=tmp_path)

        assert len(metrics.session_summaries) == 1
        summary = metrics.session_summaries[0]
        assert summary.halted is False
        assert summary.halt_category is None
        assert summary.starting_balance == settings.broker.starting_cash


class TestProgressCallback:
    """Phase 6B: `run_backtest`'s optional `progress_callback`, added for
    `api.jobs.JobManager` to report live progress. Additive -- omitting it
    (every pre-Phase-6B caller) must behave exactly as before."""

    class _AlwaysHold(Strategy):
        warmup_bars = 0

        def on_bar(self, bars, position):
            return self.hold("noop")

    def test_omitted_callback_does_not_change_behavior(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        metrics = run_backtest(settings, self._AlwaysHold(MES), make_bars(20), journal_dir=tmp_path)
        assert metrics.bars_processed == 20

    def test_callback_receives_final_bar_count(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        calls = []
        run_backtest(
            settings, self._AlwaysHold(MES), make_bars(20), journal_dir=tmp_path,
            progress_callback=lambda current, total: calls.append((current, total)),
        )
        assert calls, "progress_callback was never called"
        assert calls[-1] == (20, 20)

    def test_callback_progress_is_monotonically_increasing(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        calls = []
        run_backtest(
            settings, self._AlwaysHold(MES), make_bars(250), journal_dir=tmp_path,
            progress_callback=lambda current, total: calls.append(current),
        )
        assert calls == sorted(calls)
        assert calls[0] >= 1
        assert calls[-1] == 250

    def test_callback_call_count_is_bounded_regardless_of_bar_count(self, tmp_path):
        """Never more than ~100 calls, even for a large replay -- see the
        docstring on why per-bar reporting would be wasteful."""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        calls = []
        run_backtest(
            settings, self._AlwaysHold(MES), make_bars(5000), journal_dir=tmp_path,
            progress_callback=lambda current, total: calls.append(current),
        )
        assert len(calls) <= 105  # ~100 plus the guaranteed-final call


class TestDataLoading:
    def _write(self, path: Path, rows: str) -> Path:
        path.write_text(rows, encoding="utf-8")
        return path

    def test_loads_standard_csv(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n"
            "2026-07-21 09:00:00,7500.00,7502.00,7499.00,7501.00,1000\n"
            "2026-07-21 09:01:00,7501.00,7503.00,7500.00,7502.00,1200\n",
        )
        bars, report = load_bars(csv_path)
        assert len(bars) == 2
        assert bars[0].close == Decimal("7501.00")
        assert report.bars_loaded == 2

    def test_tolerates_alternative_column_names(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "Date,O,H,L,C,Vol\n2026-07-21 09:00:00,7500,7502,7499,7501,900\n",
        )
        bars, _ = load_bars(csv_path)
        assert len(bars) == 1

    def test_naive_timestamps_are_treated_as_exchange_time(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n2026-07-21 09:00:00,7500,7502,7499,7501,900\n",
        )
        bars, _ = load_bars(csv_path)
        assert bars[0].timestamp.tzinfo is not None
        assert bars[0].timestamp.utcoffset() == datetime(2026, 7, 21, tzinfo=CME_TZ).utcoffset()

    def test_drops_duplicate_timestamps(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n"
            "2026-07-21 09:00:00,7500,7502,7499,7501,900\n"
            "2026-07-21 09:00:00,7500,7502,7499,7501,900\n",
        )
        bars, report = load_bars(csv_path)
        assert len(bars) == 1
        assert report.duplicate_timestamps == 1

    def test_sorts_out_of_order_rows(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n"
            "2026-07-21 09:05:00,7505,7506,7504,7505,900\n"
            "2026-07-21 09:00:00,7500,7502,7499,7501,900\n",
        )
        bars, report = load_bars(csv_path)
        assert bars[0].timestamp < bars[1].timestamp
        assert report.out_of_order_rows == 1

    def test_detects_intraday_gap(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n"
            "2026-07-21 09:00:00,7500,7502,7499,7501,900\n"
            # A same-day gap well past max_gap_minutes (default 120).
            "2026-07-21 13:00:00,7501,7503,7500,7502,900\n",
        )
        bars, report = load_bars(csv_path)
        assert len(report.suspicious_gaps) == 1

    def test_gap_warning_is_pure_ascii(self, tmp_path):
        """Regression test: `DataQualityReport.warnings()` used a Unicode
        arrow (U+2192) in the gap message, which raised UnicodeEncodeError
        the moment it reached a `print()` on Windows' default cp1252
        console -- a crash that only showed up with real data containing an
        actual gap, not in any existing test. Encoding to cp1252 here
        reproduces that check without depending on the terminal's actual
        codepage."""
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n"
            "2026-07-21 09:00:00,7500,7502,7499,7501,900\n"
            "2026-07-21 13:00:00,7501,7503,7500,7502,900\n",
        )
        _, report = load_bars(csv_path)
        warnings = report.warnings()
        assert any("gap" in w.lower() for w in warnings)
        for w in warnings:
            w.encode("cp1252")  # raises UnicodeEncodeError if this regresses

    def test_rejects_missing_columns(self, tmp_path):
        csv_path = self._write(tmp_path / "d.csv", "timestamp,open,close\n2026-07-21,1,2\n")
        with pytest.raises(DataError, match="missing required column"):
            load_bars(csv_path)

    def test_rejects_high_below_low(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\n2026-07-21 09:00:00,7500,7490,7499,7501,900\n",
        )
        with pytest.raises(DataError, match="below low"):
            load_bars(csv_path)

    def test_rejects_unparseable_timestamp(self, tmp_path):
        csv_path = self._write(
            tmp_path / "d.csv",
            "timestamp,open,high,low,close,volume\nnot-a-date,7500,7502,7499,7501,900\n",
        )
        with pytest.raises(DataError, match="Could not parse timestamp"):
            load_bars(csv_path)


def make_trade(net: Decimal) -> Trade:
    when = datetime(2026, 7, 21, 10, 0, tzinfo=CME_TZ)
    return Trade(
        side=Side.LONG, quantity=1,
        entry_price=Decimal("7500"), exit_price=Decimal("7500") + net / Decimal("5"),
        entry_time=when, exit_time=when,
        gross_pnl=net, commission=Decimal("0"), exit_reason="test",
    )


class TestMetrics:
    def test_basic_totals(self):
        m = BacktestMetrics(
            trades=[make_trade(Decimal("100")), make_trade(Decimal("-50"))],
            starting_equity=Decimal("1000"),
        )
        assert m.net_pnl == Decimal("50")
        assert m.trade_count == 2
        assert m.win_rate == Decimal("50")
        assert m.profit_factor == Decimal("2")
        assert m.expectancy == Decimal("25")

    def test_max_drawdown(self):
        # +100, -80, -60, +200 -> peak 1100, trough 960 -> drawdown 140
        m = BacktestMetrics(
            trades=[make_trade(Decimal(x)) for x in ("100", "-80", "-60", "200")],
            starting_equity=Decimal("1000"),
        )
        assert m.max_drawdown == Decimal("140")

    def test_drawdown_curve_matches_max_drawdown(self):
        """`max_drawdown` is `abs(min(drawdown_curve))` -- the one shared
        peak-tracking computation both the HTML report and the API's
        per-point drawdown chart also read (see `BacktestMetrics.
        drawdown_curve`'s docstring: this used to be three separate
        implementations of the same algorithm)."""
        m = BacktestMetrics(
            trades=[make_trade(Decimal(x)) for x in ("100", "-80", "-60", "200")],
            starting_equity=Decimal("1000"),
        )
        # starting_equity, +100, -80, -60, +200 -> 1000, 1100, 1020, 960, 1160
        assert m.drawdown_curve == [Decimal("0"), Decimal("0"), Decimal("-80"), Decimal("-140"), Decimal("0")]
        assert m.max_drawdown == abs(min(m.drawdown_curve))
        assert len(m.drawdown_curve) == len(m.equity_curve)

    def test_max_consecutive_losses(self):
        m = BacktestMetrics(
            trades=[make_trade(Decimal(x)) for x in ("-10", "-10", "-10", "50", "-10")],
        )
        assert m.max_consecutive_losses == 3

    def test_flags_low_trade_count(self):
        m = BacktestMetrics(trades=[make_trade(Decimal("10"))], starting_equity=Decimal("1000"))
        assert any("noise" in c for c in m.caveats())

    def test_flags_single_dominant_trade(self):
        m = BacktestMetrics(
            trades=[make_trade(Decimal("500"))] + [make_trade(Decimal("5")) for _ in range(40)],
            starting_equity=Decimal("1000"),
        )
        assert any("net profit" in c for c in m.caveats())

    def test_flags_ambiguous_bars(self):
        m = BacktestMetrics(trades=[make_trade(Decimal("10"))], ambiguous_bars=7)
        assert any("both stop and target" in c for c in m.caveats())

    def test_always_flags_in_sample(self):
        m = BacktestMetrics(trades=[make_trade(Decimal("10")) for _ in range(200)])
        assert any("in-sample" in c for c in m.caveats())

    def test_no_trades_is_reported_plainly(self):
        m = BacktestMetrics(trades=[])
        assert m.caveats() == ["No trades were taken. Nothing here can be evaluated."]
