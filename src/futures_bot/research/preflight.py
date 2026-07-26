"""Data-aware warnings: whether the loaded bars actually fit the strategy.

`Settings.strategy_warnings()` catches contradictions the config file states
about itself (an entry window that closes before it opens, etc.) without
needing any data. This module catches the other half -- configurations that
are internally consistent but don't match the *data* actually being fed in:
a strategy built for 5-minute bars handed an hourly file, or a warmup window
longer than the whole dataset. Both classes are warnings, never errors --
see `config.py`'s module docstring for why that split exists.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Sequence

from ..config import Settings
from ..models import Bar
from ..strategy.base import StrategyRegistry

#: The bar size each bundled strategy was designed and documented around.
#: Not enforced -- just what "the data doesn't match the strategy" is judged
#: against. A strategy not listed here (a custom one, say) skips this check.
STRATEGY_TYPICAL_TIMEFRAME_MINUTES: dict[str, int] = {
    "ema_crossover": 5,
    "opening_range_breakout": 5,
    "vwap_reversion": 5,
    "trend_pullback": 5,
}

#: Above this ratio of actual-to-typical bar spacing, the mismatch is called
#: out by name rather than left to show up as "somehow no trades."
TIMEFRAME_MISMATCH_RATIO = Decimal("3")

#: Approximate CME regular trading hours, used only to estimate how many bars
#: a session can hold at a given resolution -- not a precise session model
#: (that lives in `contracts.py`), just enough to flag "this warmup can
#: never complete inside one session."
APPROX_SESSION_MINUTES = Decimal("450")


def bar_interval_minutes(bars: Sequence[Bar], sample: int = 200) -> Optional[Decimal]:
    """Median gap between consecutive bars, in minutes.

    Gaps over 120 minutes are dropped before taking the median -- those are
    overnight/weekend breaks (the same threshold `backtest.data.load_bars`
    uses for its own gap warning), not the bar size. Returns ``None`` if
    there is no usable pair (e.g. every bar is separated by a gap that large,
    or fewer than 2 bars total).
    """
    deltas: list[Decimal] = []
    for prev, curr in zip(bars, bars[1:]):
        minutes = (curr.timestamp - prev.timestamp).total_seconds() / 60
        if 0 < minutes <= 120:
            deltas.append(Decimal(str(minutes)))
        if len(deltas) >= sample:
            break
    if not deltas:
        return None
    deltas.sort()
    mid = len(deltas) // 2
    if len(deltas) % 2:
        return deltas[mid]
    return (deltas[mid - 1] + deltas[mid]) / 2


def strategy_data_warnings(settings: Settings, bars: Sequence[Bar]) -> list[str]:
    """Warnings that depend on both the strategy configuration and the
    actual bars loaded for a backtest/optimize/compare run. Never blocks --
    see the module docstring."""
    warnings: list[str] = []
    if not bars:
        return warnings

    name = settings.strategy_name
    params = settings.strategy_params
    interval = bar_interval_minutes(bars)

    if interval is not None:
        typical = STRATEGY_TYPICAL_TIMEFRAME_MINUTES.get(name)
        if typical is not None and interval > Decimal(typical) * TIMEFRAME_MISMATCH_RATIO:
            warnings.append(
                f"Data bars are ~{interval:.0f} minutes apart, but {name} is designed around "
                f"~{typical}-minute bars. Signals may be reduced or unavailable: time-of-day "
                f"windows, session-relative warmups, and range/pullback logic all assume much "
                f"finer granularity than this."
            )

        if name == "opening_range_breakout":
            range_minutes = params.get("range_minutes", 30)
            if not isinstance(range_minutes, list) and interval >= Decimal(str(range_minutes)):
                warnings.append(
                    f"The opening range window ({range_minutes} minutes) is not wider than the "
                    f"bar interval (~{interval:.0f} minutes). The range will be built from at "
                    f"most one bar, making 'breakout' nearly meaningless at this resolution."
                )

        if name == "vwap_reversion":
            min_bars = params.get("min_bars", 20)
            if not isinstance(min_bars, list):
                bars_per_session = APPROX_SESSION_MINUTES / interval
                if Decimal(min_bars) > bars_per_session:
                    warnings.append(
                        f"min_bars ({min_bars}) needs more bars than an ~{APPROX_SESSION_MINUTES:.0f}"
                        f"-minute session holds at this resolution (~{bars_per_session:.0f} bars). "
                        f"The VWAP bands would never be considered ready -- this strategy is "
                        f"likely to produce zero trades."
                    )

    # Generic, strategy-agnostic check: does the dataset even have enough
    # bars to clear warmup once? Below that, zero trades is not a risk, it's
    # a certainty, regardless of what the strategy does after warmup.
    try:
        strategy_cls = StrategyRegistry.get(name)
        sample_params = {k: v for k, v in params.items() if not isinstance(v, list)}
        strategy = strategy_cls(settings.contract_spec, **sample_params)
        if strategy.warmup_bars and len(bars) < strategy.warmup_bars:
            warnings.append(
                f"Only {len(bars)} bar(s) loaded, but {name} needs {strategy.warmup_bars} to "
                f"clear warmup. This run cannot produce a single trade."
            )
    except (KeyError, TypeError, ValueError):
        # Unregistered strategy name, or a param combination that fails
        # construction -- both are reported elsewhere (StrategyRegistry.get
        # raises KeyError from cmd_backtest/etc.; a bad param is a startup
        # error, not a preflight warning). Nothing more to check here.
        pass

    return warnings
