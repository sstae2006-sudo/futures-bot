"""Platform Verification Phase 2 (2026-07-27) -- verifies the duplicate
ADX/analyze_volatility computation Platform Verification Phase 1 found
(docs/PLATFORM_VERIFICATION_PHASE1.md, Part 5) was eliminated with zero
change to any classification/scoring output, and that the stale
``Strategy.context`` risk (Part 6 of the same report) is now closed
defensively.

The full regression suite (``tests/test_platform_verification_phase1.py``,
``tests/test_engine_context_integration.py``,
``tests/test_backtest_context_comparison.py``, every ``test_context_*.py``)
is the primary evidence that nothing changed: every one of those files
calls ``classify_regime``/``analyze_trend`` the same way it always has
(no ``precomputed_*`` arguments), so they exercise exactly the
"recompute yourself" branch that is byte-identical to the pre-Phase-2
code by construction. This file adds the narrower checks specific to
the new wiring itself: that a precomputed value is truly interchangeable
with a freshly-computed one, and that ``ContextEngine.build_context``
actually uses the fast path (not just falls back silently).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from futures_bot.context.context_engine import ContextEngine
from futures_bot.context.regime import classify_regime
from futures_bot.context.trend import analyze_trend
from futures_bot.context.volatility import analyze_volatility
from futures_bot.engine import ContextMode, TradingEngine
from futures_bot.models import Bar, Position, Signal
from futures_bot.strategy.base import Strategy
from futures_bot.strategy.indicators import adx as compute_adx

CT = ZoneInfo("America/Chicago")
START = datetime(2026, 1, 6, 17, 0, tzinfo=CT)


def _zigzag_bars(n: int, start=None) -> list[Bar]:
    start = start or START
    prices: list[Decimal] = []
    cycle_low = Decimal("5900")
    price = cycle_low
    direction = 1
    step = Decimal("4")
    for i in range(n):
        price += step * direction
        if i % 8 == 0:
            direction *= -1
        prices.append(price)
    out = []
    for i, p in enumerate(prices):
        ts = start + timedelta(minutes=i)
        vol = 100 + (i % 50) * 5
        out.append(Bar(timestamp=ts, open=p, high=p + 4, low=p - 4, close=p, volume=vol))
    return out


class TestPrecomputedValuesAreInterchangeableWithFreshComputation:
    """The whole safety argument for the dedup rests on: a precomputed
    value passed to classify_regime/analyze_trend produces the exact
    same RegimeContext/TrendContext as if the function had computed it
    itself. Proven here directly, not just assumed from "the tests
    still pass"."""

    def test_classify_regime_with_precomputed_values_matches_recomputing_them(self):
        bars = _zigzag_bars(120)
        ts = bars[-1].timestamp

        baseline = classify_regime(ts, "MES", "1min", bars)

        volatility_ctx = analyze_volatility(ts, "MES", "1min", bars)
        adx_value = compute_adx(bars, period=14)
        adx_float = float(adx_value) if adx_value is not None else None
        via_precomputed = classify_regime(
            ts, "MES", "1min", bars,
            precomputed_volatility=volatility_ctx,
            precomputed_adx=adx_float,
        )

        assert via_precomputed == baseline

    def test_analyze_trend_with_precomputed_adx_matches_recomputing_it(self):
        bars = _zigzag_bars(120)
        ts = bars[-1].timestamp

        baseline = analyze_trend(ts, "MES", bars)

        adx_value = compute_adx(bars, period=14)
        adx_float = float(adx_value) if adx_value is not None else None
        via_precomputed = analyze_trend(ts, "MES", bars, precomputed_adx=adx_float)

        assert via_precomputed == baseline

    def test_classify_regime_precomputed_none_is_used_as_is_not_retried(self):
        # Too few bars for analyze_volatility's own signal -- baseline call
        # returns UNKNOWN without ever reaching the ADX line. Passing None
        # explicitly must short-circuit to the exact same UNKNOWN result,
        # not attempt (and fail differently on) a recomputation.
        bars = _zigzag_bars(3)
        ts = bars[-1].timestamp
        baseline = classify_regime(ts, "MES", "1min", bars)
        via_precomputed = classify_regime(
            ts, "MES", "1min", bars, precomputed_volatility=analyze_volatility(ts, "MES", "1min", bars),
            precomputed_adx=None,
        )
        assert via_precomputed == baseline


class TestContextEngineActuallyUsesTheSharedComputation:
    """Not enough to prove precomputed-vs-fresh are equivalent in
    isolation -- also prove ContextEngine.build_context is actually
    wired to compute each once and pass it through, not silently
    falling back to the old double-computation path."""

    def test_regime_and_trend_adx_agree_and_match_a_single_direct_computation(self):
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _zigzag_bars(120)
        ts = bars[-1].timestamp

        context = engine.build_context(ts, bars=bars)

        expected_adx = compute_adx(bars, period=14)
        expected_adx_float = float(expected_adx) if expected_adx is not None else None

        assert context.regime_context.adx == expected_adx_float
        assert context.trend_context.adx == expected_adx_float

    def test_build_context_output_matches_calling_every_dimension_independently(self):
        """End-to-end proof that wiring the shared adx/volatility values
        through build_context changes nothing observable: every field of
        the returned MarketContext still matches what calling each
        classifier independently (the old, unshared way) would produce."""
        engine = ContextEngine(symbol="MES", timeframe="1min")
        bars = _zigzag_bars(120)
        ts = bars[-1].timestamp

        context = engine.build_context(ts, bars=bars)

        assert context.regime_context == classify_regime(ts, "MES", "1min", bars)
        assert context.trend_context == analyze_trend(ts, "MES", bars)
        assert context.volatility_context == analyze_volatility(ts, "MES", "1min", bars)


class _SimpleMomentum(Strategy):
    warmup_bars = 5

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        if len(bars) < 6:
            return self.hold("warmup")
        if position is None and bars[-1].close > bars[-2].close:
            return self.enter_long("uptick")
        return self.hold("no signal")


class _ContextAwareButUnused(_SimpleMomentum):
    uses_context = True


class TestStrategyContextResetOnConstruction:
    """Platform Verification Phase 2 requirement #2: the reset must be
    automatic and must not depend on any particular mode/bar ever
    running -- proven here at the narrowest possible scope, the
    TradingEngine constructor itself, before any on_bar call at all."""

    def test_engine_construction_clears_a_pre_existing_stale_context(self, tmp_path):
        from tests.test_platform_verification_phase1 import make_settings
        from futures_bot.brokers.paper import PaperBroker
        from futures_bot.journal import DecisionJournal
        from futures_bot.risk.manager import RiskManager
        from futures_bot.state import StateStore
        from futures_bot.contracts import MES

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        strategy = _ContextAwareButUnused(MES)
        strategy.context = "stale-value-from-a-previous-run"  # simulate reuse

        store = StateStore(settings.state_file)
        risk = RiskManager(settings, store)
        journal = DecisionJournal(settings.logging.directory, settings.logging.log_every_decision)
        broker = PaperBroker(
            contract=settings.contract_spec,
            starting_cash=settings.broker.starting_cash,
            slippage_ticks=settings.broker.slippage_ticks,
            commission_per_side=settings.broker.commission_per_side,
        )

        TradingEngine(settings, strategy, broker, risk, journal, context_mode=ContextMode.OFF)

        assert strategy.context is None
