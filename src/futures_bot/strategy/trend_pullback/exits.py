"""Exit rules.

End-of-session flatten needs no code here at all -- the risk manager already
force-flats before the 16:00 CT close for every strategy, for free. Everything
else (trailing stop, breakeven, EMA reversal, VWAP loss, max bars) is a
strategy-level decision, checked in a fixed order each bar so the exit reason
recorded is the first one that actually applies.

Each function takes a snapshot of what it needs and returns ``None`` or a
reason string -- no side effects, no engine or broker awareness, so each is
testable by constructing a `Position`-like set of numbers directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ...models import Position, Side
from .rolling import IndicatorSnapshot


@dataclass(frozen=True)
class ExitConfig:
    atr_stop_mult: Decimal = Decimal("1.5")
    atr_target_mult: Decimal = Decimal("3.0")
    trailing_atr_mult: Decimal = Decimal("2.0")
    #: Profit (in points) that triggers moving the stop to breakeven. None disables it.
    breakeven_trigger_points: Optional[Decimal] = Decimal("8")
    #: Small buffer beyond pure breakeven, so the exit fill still nets >= 0
    #: after commission rather than landing on exactly zero.
    breakeven_buffer_points: Decimal = Decimal("0.5")
    max_bars_in_trade: int = 24
    vwap_loss_enabled: bool = True
    ema_reversal_enabled: bool = True


def initial_stop_and_target(
    side: Side, entry_price: Decimal, atr: Decimal, config: ExitConfig
) -> tuple[Decimal, Decimal]:
    """ATR-scaled stop/target at entry, sized to current volatility rather
    than a fixed point distance -- a stop that doesn't adapt to conditions is
    loose in a quiet market and tight in a fast one."""
    stop_distance = atr * config.atr_stop_mult
    target_distance = atr * config.atr_target_mult
    if side is Side.LONG:
        return entry_price - stop_distance, entry_price + target_distance
    return entry_price + stop_distance, entry_price - target_distance


def trailing_stop_level(
    position: Position, current_price: Decimal, atr: Decimal, config: ExitConfig
) -> Optional[Decimal]:
    """New stop level if the trail has advanced, else None (never loosens the stop)."""
    if position.stop_loss is None:
        return None
    trail_distance = atr * config.trailing_atr_mult

    if position.side is Side.LONG:
        candidate = current_price - trail_distance
        return candidate if candidate > position.stop_loss else None
    candidate = current_price + trail_distance
    return candidate if candidate < position.stop_loss else None


def breakeven_stop_level(
    position: Position, current_price: Decimal, config: ExitConfig
) -> Optional[Decimal]:
    """Moves the stop to (roughly) breakeven once the trigger profit is reached.

    One-way ratchet: never moves the stop past where it already is, and never
    fires twice (the caller only needs to apply this once profit crosses the
    trigger, since after that the trailing stop takes over and stays ahead of
    it for a bullish/bearish move).
    """
    if config.breakeven_trigger_points is None or position.stop_loss is None:
        return None

    if position.side is Side.LONG:
        profit = current_price - position.entry_price
        if profit < config.breakeven_trigger_points:
            return None
        candidate = position.entry_price + config.breakeven_buffer_points
        return candidate if candidate > position.stop_loss else None

    profit = position.entry_price - current_price
    if profit < config.breakeven_trigger_points:
        return None
    candidate = position.entry_price - config.breakeven_buffer_points
    return candidate if candidate < position.stop_loss else None


def check_ema_reversal_exit(position: Position, snap: IndicatorSnapshot, config: ExitConfig) -> Optional[str]:
    if not config.ema_reversal_enabled or snap.ema9 is None or snap.ema21 is None:
        return None
    if position.side is Side.LONG and snap.ema9 < snap.ema21:
        return f"EMA9 ({snap.ema9:.2f}) crossed below EMA21 ({snap.ema21:.2f}) -- momentum reversed."
    if position.side is Side.SHORT and snap.ema9 > snap.ema21:
        return f"EMA9 ({snap.ema9:.2f}) crossed above EMA21 ({snap.ema21:.2f}) -- momentum reversed."
    return None


def check_vwap_loss_exit(
    position: Position, current_price: Decimal, snap: IndicatorSnapshot, config: ExitConfig
) -> Optional[str]:
    """Protects against a fakeout: entered above VWAP, price fell back below it."""
    if not config.vwap_loss_enabled or snap.vwap is None:
        return None
    if position.side is Side.LONG and current_price < snap.vwap:
        return f"Price ({current_price:.2f}) fell back below session VWAP ({snap.vwap:.2f})."
    if position.side is Side.SHORT and current_price > snap.vwap:
        return f"Price ({current_price:.2f}) rallied back above session VWAP ({snap.vwap:.2f})."
    return None


def check_max_bars_exit(bars_held: int, config: ExitConfig) -> Optional[str]:
    if bars_held >= config.max_bars_in_trade:
        return f"Held {bars_held} bars, at the {config.max_bars_in_trade}-bar limit."
    return None
