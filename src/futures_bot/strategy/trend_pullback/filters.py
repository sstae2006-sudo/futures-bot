"""Entry filters: the conditions that must ALL hold before a trend direction
is even considered tradeable.

Kept as pure functions over a snapshot rather than methods on the strategy,
so each one is independently testable without constructing a whole strategy
or feeding it bar history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from typing import Optional

from ...contracts import to_ct
from ...models import Bar
from .rolling import IndicatorSnapshot


@dataclass(frozen=True)
class SessionWindow:
    start: time
    end: time

    def contains(self, t: time) -> bool:
        return self.start <= t <= self.end


@dataclass(frozen=True)
class FilterConfig:
    rsi_long_min: Decimal = Decimal("55")
    rsi_short_max: Decimal = Decimal("45")
    adx_min: Decimal = Decimal("20")
    volume_multiplier: Decimal = Decimal("1.25")
    atr_min: Decimal = Decimal("0")
    sessions: tuple[SessionWindow, ...] = (
        SessionWindow(time(8, 30), time(10, 30)),
        SessionWindow(time(13, 30), time(15, 0)),
    )


def in_trading_session(bar: Bar, config: FilterConfig) -> bool:
    ct_time = to_ct(bar.timestamp).time()
    return any(window.contains(ct_time) for window in config.sessions)


def _volume_confirmed(bar: Bar, snap: IndicatorSnapshot, config: FilterConfig) -> bool:
    if snap.volume_sma is None or snap.volume_sma == 0:
        return False
    return Decimal(bar.volume) > snap.volume_sma * config.volume_multiplier


def _atr_sufficient(snap: IndicatorSnapshot, config: FilterConfig) -> bool:
    return snap.atr is not None and snap.atr > config.atr_min


def long_filters_pass(bar: Bar, snap: IndicatorSnapshot, config: FilterConfig) -> Optional[str]:
    """Returns None if every long filter passes, otherwise the first reason it failed.

    Checks cheapest/most-decisive conditions first so the failure reason
    reported is the most fundamental one, not an incidental later check.
    """
    if not snap.ready:
        return "Indicators still warming up."
    if not (snap.ema50 > snap.ema200):
        return f"EMA50 ({snap.ema50:.2f}) not above EMA200 ({snap.ema200:.2f}) -- no uptrend regime."
    if not (bar.close > snap.ema200):
        return f"Price ({bar.close:.2f}) not above EMA200 ({snap.ema200:.2f})."
    if not (bar.close > snap.vwap):
        return f"Price ({bar.close:.2f}) not above session VWAP ({snap.vwap:.2f})."
    if not (snap.ema9 > snap.ema21):
        return f"EMA9 ({snap.ema9:.2f}) not above EMA21 ({snap.ema21:.2f})."
    if not (snap.rsi > config.rsi_long_min):
        return f"RSI ({snap.rsi:.1f}) not above {config.rsi_long_min}."
    if not (snap.adx > config.adx_min):
        return f"ADX ({snap.adx:.1f}) not above {config.adx_min} -- trend too weak."
    if not _atr_sufficient(snap, config):
        return f"ATR ({snap.atr}) not above the {config.atr_min} minimum."
    if not _volume_confirmed(bar, snap, config):
        return (
            f"Volume ({bar.volume}) not above {config.volume_multiplier}x the "
            f"20-bar average ({snap.volume_sma:.0f})."
        )
    if not in_trading_session(bar, config):
        return "Outside the configured trading sessions."
    return None


def short_filters_pass(bar: Bar, snap: IndicatorSnapshot, config: FilterConfig) -> Optional[str]:
    """Mirror image of :func:`long_filters_pass` for the downtrend case."""
    if not snap.ready:
        return "Indicators still warming up."
    if not (snap.ema50 < snap.ema200):
        return f"EMA50 ({snap.ema50:.2f}) not below EMA200 ({snap.ema200:.2f}) -- no downtrend regime."
    if not (bar.close < snap.ema200):
        return f"Price ({bar.close:.2f}) not below EMA200 ({snap.ema200:.2f})."
    if not (bar.close < snap.vwap):
        return f"Price ({bar.close:.2f}) not below session VWAP ({snap.vwap:.2f})."
    if not (snap.ema9 < snap.ema21):
        return f"EMA9 ({snap.ema9:.2f}) not below EMA21 ({snap.ema21:.2f})."
    if not (snap.rsi < config.rsi_short_max):
        return f"RSI ({snap.rsi:.1f}) not below {config.rsi_short_max}."
    if not (snap.adx > config.adx_min):
        return f"ADX ({snap.adx:.1f}) not above {config.adx_min} -- trend too weak."
    if not _atr_sufficient(snap, config):
        return f"ATR ({snap.atr}) not above the {config.atr_min} minimum."
    if not _volume_confirmed(bar, snap, config):
        return (
            f"Volume ({bar.volume}) not above {config.volume_multiplier}x the "
            f"20-bar average ({snap.volume_sma:.0f})."
        )
    if not in_trading_session(bar, config):
        return "Outside the configured trading sessions."
    return None
