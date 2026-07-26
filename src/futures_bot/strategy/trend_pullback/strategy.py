"""The trend-pullback strategy itself.

Wires together the pieces in the sibling modules. What lives here rather than
there is state management: which position (if any) this strategy believes is
open, tracked independently of the engine's own bookkeeping so analytics can
be recorded without depending on the `Trade` type carrying strategy-specific
fields.

One correctness subtlety worth flagging explicitly, since it is exactly the
kind of thing that looks fine until a specific bar sequence hits it: an entry
this strategy submits can be rejected -- by the risk manager (daily limit,
trading window, trade cap) before it ever reaches the broker, or by the
broker itself (a degenerate bracket if ATR is unusually small). Either way,
the *next* bar shows `position is None`, indistinguishable at a glance from a
trade that opened and immediately closed. This strategy tracks its own
entries as "pending" until it observes the position actually exists, and
silently drops a pending entry that never confirms rather than recording a
trade that never happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Sequence

from ...models import Bar, Position, Side, Signal, SignalAction
from ..base import Strategy, StrategyRegistry
from .analytics import EntryContext
from .exits import (
    ExitConfig,
    breakeven_stop_level,
    check_ema_reversal_exit,
    check_max_bars_exit,
    check_vwap_loss_exit,
    initial_stop_and_target,
    trailing_stop_level,
)
from .filters import FilterConfig, SessionWindow, long_filters_pass, short_filters_pass
from .pullback import PullbackConfig, PullbackTracker
from .rolling import IndicatorSnapshot, RollingIndicators


def _parse_session_windows(raw: Optional[list]) -> Optional[tuple]:
    """Turns config.yaml's ``[["08:30","10:30"], ["13:30","15:00"]]`` into windows."""
    if raw is None:
        return None
    from datetime import time as _time

    windows = []
    for pair in raw:
        start_s, end_s = pair
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
        windows.append(SessionWindow(_time(sh, sm), _time(eh, em)))
    return tuple(windows)


@dataclass
class _OpenTradeState:
    """This strategy's own record of the position it believes is open.

    Kept independent of the engine's `Position`/`Trade` types on purpose --
    see the module docstring on why identity has to live here rather than be
    inferred from what the engine reports back.
    """

    side: Side
    entry_price: Decimal
    entry_bar_index: int
    context: EntryContext
    confirmed: bool = False
    mfe_points: Decimal = field(default=Decimal("0"))
    mae_points: Decimal = field(default=Decimal("0"))
    breakeven_applied: bool = False

    def update_excursion(self, bar: Bar) -> None:
        if self.side is Side.LONG:
            favorable = bar.high - self.entry_price
            adverse = self.entry_price - bar.low
        else:
            favorable = self.entry_price - bar.low
            adverse = bar.high - self.entry_price
        self.mfe_points = max(self.mfe_points, favorable, Decimal("0"))
        self.mae_points = max(self.mae_points, adverse, Decimal("0"))


@StrategyRegistry.register("trend_pullback")
class TrendPullbackStrategy(Strategy):
    """Multi-filter trend-following entry on a pullback, ATR-scaled exits.

    Every threshold is a constructor parameter (and therefore a
    ``strategy_params`` entry in config.yaml) rather than a literal in this
    file -- see ``config.example.yaml`` for the full parameter list with
    defaults matching the spec.
    """

    def __init__(
        self,
        contract,
        # Indicator periods
        ema_fast: int = 9,
        ema_mid: int = 21,
        ema_slow: int = 50,
        ema_trend: int = 200,
        atr_period: int = 14,
        rsi_period: int = 14,
        adx_period: int = 14,
        volume_sma_period: int = 20,
        slope_lookback_bars: int = 5,
        # Entry filters
        rsi_long_min: float = 55,
        rsi_short_max: float = 45,
        adx_min: float = 20,
        volume_multiplier: float = 1.25,
        atr_min: float = 0,
        trading_sessions: Optional[list] = None,  # e.g. [["08:30","10:30"], ["13:30","15:00"]]
        # Pullback
        pullback_distance: float = 3,
        max_arm_bars: int = 6,
        # Exits
        atr_stop_mult: float = 1.5,
        atr_target_mult: float = 3.0,
        trailing_atr_mult: float = 2.0,
        breakeven_trigger_points: Optional[float] = 8,
        breakeven_buffer_points: float = 0.5,
        max_bars_in_trade: int = 24,
        vwap_loss_enabled: bool = True,
        ema_reversal_enabled: bool = True,
        **params,
    ) -> None:
        super().__init__(contract, **params)

        self.rolling = RollingIndicators(
            ema_fast=ema_fast, ema_mid=ema_mid, ema_slow=ema_slow, ema_trend=ema_trend,
            atr_period=atr_period, rsi_period=rsi_period, adx_period=adx_period,
            volume_sma_period=volume_sma_period,
        )

        sessions = _parse_session_windows(trading_sessions)
        filter_kwargs = dict(
            rsi_long_min=Decimal(str(rsi_long_min)),
            rsi_short_max=Decimal(str(rsi_short_max)),
            adx_min=Decimal(str(adx_min)),
            volume_multiplier=Decimal(str(volume_multiplier)),
            atr_min=Decimal(str(atr_min)),
        )
        if sessions is not None:
            filter_kwargs["sessions"] = sessions
        self.filter_config = FilterConfig(**filter_kwargs)

        self.pullback_config = PullbackConfig(
            pullback_distance=Decimal(str(pullback_distance)),
            max_arm_bars=max_arm_bars,
        )
        self.long_pullback = PullbackTracker(Side.LONG, self.pullback_config)
        self.short_pullback = PullbackTracker(Side.SHORT, self.pullback_config)

        self.exit_config = ExitConfig(
            atr_stop_mult=Decimal(str(atr_stop_mult)),
            atr_target_mult=Decimal(str(atr_target_mult)),
            trailing_atr_mult=Decimal(str(trailing_atr_mult)),
            breakeven_trigger_points=(
                Decimal(str(breakeven_trigger_points)) if breakeven_trigger_points is not None else None
            ),
            breakeven_buffer_points=Decimal(str(breakeven_buffer_points)),
            max_bars_in_trade=max_bars_in_trade,
            vwap_loss_enabled=vwap_loss_enabled,
            ema_reversal_enabled=ema_reversal_enabled,
        )

        self.slope_lookback_bars = slope_lookback_bars
        self._ema50_history: list[Decimal] = []
        self._ema200_history: list[Decimal] = []

        self._bar_index = 0
        self._current: Optional[_OpenTradeState] = None

        # Populated as trades complete; consumed by analytics.build_trade_records.
        self.entry_contexts: list[EntryContext] = []
        self.bars_held: list[int] = []
        self.excursions: list[tuple] = []

        # Warmup needs the slower of: the longest indicator period, or enough
        # bars for a slope calculation.
        self.warmup_bars = max(ema_trend, adx_period * 2, slope_lookback_bars) + 1

    # --- Slope tracking (kept here rather than in RollingIndicators, since
    # it is this strategy's feature, not a general-purpose indicator) ---

    def _update_slopes(self, snap: IndicatorSnapshot):
        if snap.ema50 is not None:
            self._ema50_history.append(snap.ema50)
        if snap.ema200 is not None:
            self._ema200_history.append(snap.ema200)

        n = self.slope_lookback_bars
        slope50 = (
            (self._ema50_history[-1] - self._ema50_history[-1 - n]) / Decimal(n)
            if len(self._ema50_history) > n
            else None
        )
        slope200 = (
            (self._ema200_history[-1] - self._ema200_history[-1 - n]) / Decimal(n)
            if len(self._ema200_history) > n
            else None
        )
        return slope50, slope200

    # --- Entry context / analytics ---

    def _build_entry_context(
        self, bar: Bar, snap: IndicatorSnapshot, side: Side, reason: str,
        slope50: Decimal, slope200: Decimal,
    ) -> EntryContext:
        volume_ratio = (
            Decimal(bar.volume) / snap.volume_sma if snap.volume_sma else Decimal("0")
        )
        return EntryContext(
            entry_reason=reason,
            ema9=snap.ema9, ema21=snap.ema21, ema50=snap.ema50, ema200=snap.ema200,
            ema50_slope=slope50, ema200_slope=slope200,
            rsi=snap.rsi, atr=snap.atr, adx=snap.adx,
            vwap=snap.vwap, vwap_distance=bar.close - snap.vwap,
            volume_ratio=volume_ratio,
            trend_direction="bullish" if side is Side.LONG else "bearish",
            trade_direction=side.value,
        )

    def _finalize(self) -> None:
        assert self._current is not None
        self.entry_contexts.append(self._current.context)
        self.bars_held.append(self._bar_index - self._current.entry_bar_index)
        self.excursions.append((self._current.mfe_points, self._current.mae_points))
        self._current = None

    # --- Exit checks while a confirmed position is open ---

    def _check_exits(self, bar: Bar, snap: IndicatorSnapshot) -> Optional[Signal]:
        assert self._current is not None
        state = self._current
        synthetic_position = Position(
            side=state.side, quantity=1, entry_price=state.entry_price, entry_time=bar.timestamp
        )

        reason = check_ema_reversal_exit(synthetic_position, snap, self.exit_config)
        if reason:
            return self.exit(reason)

        reason = check_vwap_loss_exit(synthetic_position, bar.close, snap, self.exit_config)
        if reason:
            return self.exit(reason)

        reason = check_max_bars_exit(self._bar_index - state.entry_bar_index, self.exit_config)
        if reason:
            return self.exit(reason)

        return None

    def _check_stop_management(
        self, bar: Bar, snap: IndicatorSnapshot, position: Position
    ) -> Optional[Signal]:
        """Breakeven and trailing stop -- returns a HOLD carrying a new stop
        level, which the engine reads as an instruction to move the resting
        stop rather than as a plain no-op."""
        state = self._current
        assert state is not None and snap.atr is not None

        options: list[tuple[Decimal, str]] = []

        if not state.breakeven_applied:
            be = breakeven_stop_level(position, bar.close, self.exit_config)
            if be is not None:
                options.append((be, "breakeven"))
                state.breakeven_applied = True

        trail = trailing_stop_level(position, bar.close, snap.atr, self.exit_config)
        if trail is not None:
            options.append((trail, "trailing"))

        if not options:
            return None

        # Whichever candidate is more protective wins -- for a long, the
        # higher stop; for a short, the lower one.
        best_stop, kind = (
            max(options, key=lambda pair: pair[0])
            if state.side is Side.LONG
            else min(options, key=lambda pair: pair[0])
        )
        return self._hold_with_stop(f"Moving stop to {best_stop:.2f} ({kind}).", best_stop)

    def _hold_with_stop(self, reason: str, stop_loss: Decimal) -> Signal:
        # A plain HOLD never carries stop_loss for any other strategy -- the
        # engine reads this combination specifically as "move the resting
        # stop for the open position." See engine._handle_signal.
        return Signal(action=SignalAction.HOLD, reason=reason, stop_loss=stop_loss)

    # --- Main loop ---

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        bar = bars[-1]
        self._bar_index += 1
        snap = self.rolling.update(bar)
        slope50, slope200 = self._update_slopes(snap)

        if self._current is not None:
            return self._handle_open_position(bar, snap, position)

        if position is not None:
            # Another position exists that this strategy did not open -- not
            # expected under the Phase 1 single-position constraint, but
            # nothing here should assume that constraint always holds.
            return self.hold("A position is open that this strategy is not tracking.")

        return self._handle_flat(bar, snap, slope50, slope200)

    def _handle_open_position(
        self, bar: Bar, snap: IndicatorSnapshot, position: Optional[Position]
    ) -> Signal:
        state = self._current
        assert state is not None

        if not state.confirmed:
            if position is not None:
                state.confirmed = True
            else:
                # The entry never took effect (blocked by risk, or rejected
                # by the broker) -- nothing to finalize, just resume looking.
                self._current = None
                self.long_pullback.reset()
                self.short_pullback.reset()
                return self.hold("Previous entry attempt did not go through; resuming search.")

        state.update_excursion(bar)

        if position is None:
            # Closed by the broker (stop/target hit this bar) or force-flattened
            # between calls. Either way this strategy's job is just to record
            # what happened, not to act -- the position is already gone.
            self._finalize()
            return self.hold("Position closed (stop/target hit, or force-flattened by risk rules).")

        exit_signal = self._check_exits(bar, snap)
        if exit_signal is not None:
            self._finalize()
            return exit_signal

        stop_signal = self._check_stop_management(bar, snap, position)
        if stop_signal is not None:
            return stop_signal

        return self.hold(
            f"Holding {state.side.value}; {self._bar_index - state.entry_bar_index} bars in, "
            f"MFE {state.mfe_points:.2f} MAE {state.mae_points:.2f}."
        )

    def _handle_flat(
        self, bar: Bar, snap: IndicatorSnapshot, slope50: Optional[Decimal], slope200: Optional[Decimal]
    ) -> Signal:
        if not snap.ready:
            return self.hold(f"Indicators warming up ({self._bar_index}/{self.warmup_bars} bars).")

        long_trend_ok = snap.ema50 > snap.ema200 and bar.close > snap.ema200
        short_trend_ok = snap.ema50 < snap.ema200 and bar.close < snap.ema200

        long_result = self.long_pullback.update(bar, snap, self._bar_index, long_trend_ok)
        short_result = self.short_pullback.update(bar, snap, self._bar_index, short_trend_ok)

        if long_result.triggered:
            block_reason = long_filters_pass(bar, snap, self.filter_config)
            if block_reason is None:
                return self._enter(bar, snap, Side.LONG, long_result.reason, slope50, slope200)
            return self.hold(f"Pullback triggered but entry filters failed: {block_reason}")

        if short_result.triggered:
            block_reason = short_filters_pass(bar, snap, self.filter_config)
            if block_reason is None:
                return self._enter(bar, snap, Side.SHORT, short_result.reason, slope50, slope200)
            return self.hold(f"Pullback triggered but entry filters failed: {block_reason}")

        return self.hold(
            f"{long_result.reason} | {short_result.reason}"
            if long_trend_ok or short_trend_ok
            else "No trend regime (EMA50/EMA200/price not aligned either direction)."
        )

    def _enter(
        self, bar: Bar, snap: IndicatorSnapshot, side: Side, trigger_reason: str,
        slope50: Optional[Decimal], slope200: Optional[Decimal],
    ) -> Signal:
        assert snap.atr is not None
        stop, target = initial_stop_and_target(side, bar.close, snap.atr, self.exit_config)
        context = self._build_entry_context(
            bar, snap, side, trigger_reason,
            slope50 or Decimal("0"), slope200 or Decimal("0"),
        )
        self._current = _OpenTradeState(
            side=side, entry_price=bar.close, entry_bar_index=self._bar_index, context=context,
        )

        # Metadata is pure logging context -- it never reaches the risk
        # manager, broker, or this strategy's own decisions (those are
        # already fully determined by `stop`/`target` above and the state
        # already recorded in `self._current`). Exposing the same indicator
        # snapshot the strategy already computed for `context` lets
        # research/features.py build a richer trade-analytics dataset
        # without this strategy needing to know that consumer exists.
        signal_fn = self.enter_long if side is Side.LONG else self.enter_short
        return signal_fn(
            trigger_reason, stop_loss=stop, take_profit=target,
            ema9=context.ema9, ema21=context.ema21, ema50=context.ema50, ema200=context.ema200,
            ema50_slope=context.ema50_slope, ema200_slope=context.ema200_slope,
            rsi=context.rsi, atr=context.atr, adx=context.adx,
            vwap=context.vwap, vwap_distance=context.vwap_distance,
            volume_ratio=context.volume_ratio, trend_direction=context.trend_direction,
        )
