"""Live bar sources for paper/live trading.

Everything in `backtest/` replays bars that already exist, in bulk, from a
file. This package is the other half: a source that produces new bars one
at a time as the market actually makes them, for `cli.cmd_live` to hand to
`TradingEngine.on_bar` as they arrive. See `feeds/base.py` for the interface
every source implements.
"""

from __future__ import annotations
