"""Builds a parameter schema for each registered strategy by reading its
``__init__`` signature, rather than hand-maintaining a duplicate schema per
strategy here. The backtest launcher's "strategy parameters should
dynamically load" requirement means whichever source of truth is chosen has
to stay in sync with `strategy_params` automatically -- introspection is the
only way to get that for free, since the constructor signature and the
config file's accepted keys are already required to agree (a strategy is
literally constructed with ``strategy_cls(contract, **strategy_params)``).

Two things this can't recover from a bare signature, both handled
explicitly below:

* Which type a parameter is (a bare ``inspect.Parameter`` only has this if
  the constructor bothers to annotate it, and most of these constructors
  annotate with plain builtins -- ``int``, ``float``, ``Decimal``, ``bool``,
  ``str`` -- which is enough for a form to render the right input type).
* A short description. These aren't in the signature at all; a small
  per-strategy override dict fills in the ones worth explaining, and
  everything else falls back to the bare parameter name.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from ..strategy.base import Strategy, StrategyRegistry

#: Excluded from every schema: `contract` is supplied by the API itself
#: (from the request's top-level `contract` field), and `**params` is the
#: catch-all every `Strategy.__init__` forwards to the base class, not a
#: real parameter.
_EXCLUDED = {"self", "contract", "params"}

_TYPE_NAMES = {
    int: "int", float: "number", Decimal: "number", bool: "boolean", str: "string",
}

#: Short, human-readable descriptions for parameters worth explaining.
#: Anything not listed here still appears in the schema -- just without a
#: description -- so a new strategy parameter is never hidden from the API,
#: only less explained until someone adds an entry here.
_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "ema_crossover": {
        "fast_period": "Fast EMA length",
        "slow_period": "Slow EMA length",
        "trend_period": "Long-term trend filter EMA length",
        "min_ema_distance": "Minimum points between fast/slow EMA to act on a cross",
    },
    "opening_range_breakout": {
        "range_minutes": "Length of the opening range window, in minutes",
        "session_start_ct": "When the range starts building (Central Time, HH:MM)",
        "earliest_entry_ct": "No entries before this time (Central Time, HH:MM)",
        "latest_entry_ct": "No entries after this time (Central Time, HH:MM)",
        "max_entries_per_session": "Breakouts to act on before going flat for the session",
        "require_close_beyond": "Ignore wick-only pokes through the level",
        "min_range_points": "Skip sessions where the range is too tight to mean anything",
        "max_range_points": "Skip sessions where the range already made its move",
        "trend_period": "EMA length for the trend filter",
        "allow_long": "Trade long breakouts",
        "allow_short": "Trade short breakdowns",
        "stop_at_range_opposite": "Stop at the far side of the range instead of the configured stop distance",
    },
    "vwap_reversion": {
        "std_devs": "Standard deviations from VWAP before fading the move",
        "min_bars": "Bars into the session before trading starts",
        "exit_at_vwap": "Exit target is the return to VWAP",
        "max_entries_per_session": "Cap on fades per session",
    },
    "trend_pullback": {
        "ema_fast": "Fast EMA length", "ema_mid": "Pullback EMA length",
        "ema_slow": "Regime EMA length (vs. ema_trend)", "ema_trend": "Long-term trend EMA length",
        "atr_period": "ATR lookback", "rsi_period": "RSI lookback", "adx_period": "ADX lookback",
        "volume_sma_period": "Volume average lookback",
        "rsi_long_min": "Minimum RSI to confirm a long entry",
        "rsi_short_max": "Maximum RSI to confirm a short entry",
        "adx_min": "Minimum ADX (trend strength floor)",
        "volume_multiplier": "Entry volume must clear this multiple of its average",
        "atr_min": "Skip bars too quiet to trust",
        "pullback_distance": "Points from the pullback EMA that counts as 'pulled back'",
        "max_arm_bars": "An armed setup older than this goes stale",
        "atr_stop_mult": "Initial stop = entry -/+ ATR * this",
        "atr_target_mult": "Initial target = entry +/- ATR * this",
        "trailing_atr_mult": "Trailing stop trails price by ATR * this",
        "breakeven_trigger_points": "Move stop to breakeven after this much profit",
        "breakeven_buffer_points": "Breakeven stop sits this far past entry",
        "max_bars_in_trade": "Force an exit after this many bars",
        "vwap_loss_enabled": "Exit if price falls back through session VWAP",
        "ema_reversal_enabled": "Exit if the fast/mid EMA cross reverses against the position",
    },
}


@dataclass(frozen=True)
class ParamSchema:
    name: str
    type: str
    default: Any
    description: Optional[str]


def strategy_param_schema(name: str) -> list[ParamSchema]:
    """The constructor parameters `StrategyRegistry.get(name)` accepts,
    excluding `contract`/`**params`, with each one's default value and best-
    guess type. Raises `KeyError` (via `StrategyRegistry.get`) for an
    unregistered name -- the same error the rest of this codebase already
    raises for that, so route handlers can catch it once, consistently.
    """
    strategy_cls = StrategyRegistry.get(name)
    signature = inspect.signature(strategy_cls.__init__)
    schema = []
    for param_name, param in signature.parameters.items():
        if param_name in _EXCLUDED or param.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        default = None if param.default is inspect.Parameter.empty else param.default
        if isinstance(default, Decimal):
            default = float(default)
        type_name = _TYPE_NAMES.get(param.annotation)
        if type_name is None:
            # Annotation wasn't one of the plain builtins above (e.g.
            # `Optional[list]` for trend_pullback's trading_sessions) --
            # fall back to the default value's own type, which is always
            # available since every strategy parameter has one.
            type_name = _TYPE_NAMES.get(type(default), "string")
        description = _DESCRIPTIONS.get(name, {}).get(param_name)
        schema.append(ParamSchema(name=param_name, type=type_name, default=default, description=description))
    return schema


def all_strategy_schemas() -> dict[str, list[ParamSchema]]:
    return {name: strategy_param_schema(name) for name in StrategyRegistry.names()}
