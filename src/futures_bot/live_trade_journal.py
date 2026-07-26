"""`LiveTradeJournal` -- persists every closed live/paper trade to the
shared `TradeStore` the moment it closes, the same way a backtest's trades
are persisted, just one at a time as they happen instead of once at the
end.

Lives at the top level (not under `api/` or `research_server/`) because
both need it: `api/live_session.py`'s `LiveSessionManager` (Phase 7a, the
single dashboard-controlled session) and `research_server/paper_trader.py`
's `AutonomousPaperTrader` (Phase 8B, N concurrent unattended sessions).
Putting it under either one would make the other depend on it sideways;
`api` already depends on `research_server` (for the dashboard), and
`research_server` must never depend back on `api` -- this module is the
shared, dependency-free home both sit above.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Callable, Optional, Sequence

from .backtest.runner import CountingJournal, EntryRecord
from .journal import LOGGER_NAME
from .models import Bar, Trade
from .research.features import build_trade_records
from .research.regime import compute_regimes
from .research.trade_store import TradeStore, default_db_path

log = logging.getLogger(LOGGER_NAME)


class LiveTradeJournal(CountingJournal):
    """`CountingJournal` plus one side effect: persist each closed trade to
    the `TradeStore` the moment it closes, joined against the entry
    decision `CountingJournal.decision()` already buffered for it -- the
    same positional 1:1 join `research.features.build_trade_records`
    documents, which holds here for the same reason it holds for a
    backtest (`RiskManager.can_enter` enforces one open position at a
    time). A persistence failure is logged, never raised -- one bad write
    must not take down a live session any more than one bad strategy bar
    does (see `TradingEngine._safe_signal`).
    """

    def __init__(
        self, directory, enabled: bool, *, run_id: str, contract: str, strategy: str, strategy_params: dict,
        label_regimes: bool = False,
    ) -> None:
        super().__init__(directory, enabled)
        self._run_id = run_id
        self._contract = contract
        self._strategy = strategy
        self._strategy_params = strategy_params
        self._label_regimes = label_regimes
        #: Set by the caller once the engine this journal is attached to
        #: exists (`build_engine` takes the journal as a constructor
        #: argument, so the journal necessarily exists *before* the engine
        #: does) -- lets `trade()` read the engine's own accumulated bar
        #: history for regime labeling without restructuring construction
        #: order. `None` (the default) simply means no regime labels get
        #: attached, which is `api.live_session`'s manual Live Session
        #: behavior; `research_server.paper_trader` sets both
        #: `label_regimes=True` and this, so its trades get labeled.
        self.bars_provider: Optional[Callable[[], Sequence[Bar]]] = None
        #: Every trade this session has closed, in order -- read back by
        #: the caller's shutdown path to compute the run's final aggregate
        #: metrics via `BacktestMetrics`, the same object a backtest uses
        #: for the exact same figures.
        self.closed_trades: list[Trade] = []

    def trade(self, trade: Trade, session_pnl: Decimal) -> None:
        super().trade(trade, session_pnl)
        self.closed_trades.append(trade)
        entry: Optional[EntryRecord] = self.entries[-1] if self.entries else None
        if entry is None:
            log.error("Live trade closed with no matching buffered entry -- not persisted: %r", trade)
            return
        try:
            regimes = None
            if self._label_regimes and self.bars_provider is not None:
                regimes = compute_regimes([trade], self.bars_provider())
            records = build_trade_records(
                [trade], [entry],
                run_id=self._run_id, contract=self._contract, strategy=self._strategy,
                strategy_params=self._strategy_params, regimes=regimes,
            )
            store = TradeStore(default_db_path())
            try:
                store.insert_trades(records)
            finally:
                store.close()
        except Exception:  # noqa: BLE001 -- a storage failure must not kill the live session.
            log.error("Failed to persist live trade to the research DB.", exc_info=True)
