"""Daily trading-session summaries.

A "session" in this codebase has always been one CME trading day
(`contracts.session_date`), tracked by `StateStore`/`RiskManager` -- the
daily loss limit, trade cap, and trading-hours window already simulate
exactly the "one simulated trading day, maximize daily performance" concept
this module names. What was missing were two of the stop *rules*
(profit target, consecutive-loss halt, and a post-loss cooldown -- added to
`RiskSettings`/`RiskManager`) and a report-time view that reshapes what the
existing machinery already records into one summary row per session day.

Nothing here simulates anything new or touches the engine: `build_session_
summaries` is a pure read of `StateStore.state` after a run, the same data
`RiskManager` was already persisting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .state import StateStore


@dataclass(frozen=True)
class SessionSummary:
    """One simulated trading day's outcome."""

    session_date: str
    starting_balance: Decimal
    session_pnl: Decimal
    trade_count: int
    halted: bool
    #: 'daily_loss' | 'profit_target' | 'consecutive_losses' | None
    halt_category: Optional[str]
    halt_reason: Optional[str]
    halted_at: Optional[str]
    target_hit_at: Optional[str]
    missed_opportunities: int
    consecutive_losses_at_close: int

    @property
    def ending_balance(self) -> Decimal:
        return self.starting_balance + self.session_pnl

    @property
    def stopped_on_profit(self) -> bool:
        return self.halt_category == "profit_target"

    @property
    def stopped_on_loss(self) -> bool:
        return self.halt_category == "daily_loss"

    @property
    def stopped_on_consecutive_losses(self) -> bool:
        return self.halt_category == "consecutive_losses"


def build_session_summaries(store: StateStore, starting_balance: Decimal) -> list[SessionSummary]:
    """One `SessionSummary` per session day the store has a record for:
    every archived day in `store.state.history` (oldest first, the order
    `StateStore.session()` appends in), plus the current/most recent day.

    `starting_balance` compounds day over day (each day starts from the
    running total after every prior day's `session_pnl`) rather than
    resetting to a flat figure -- the same account carries its balance
    forward across days in reality, and this only reads figures
    `RiskManager` already tracked, never re-derives or re-simulates one.
    """
    records = list(store.state.history)
    if store.state.current is not None:
        records = records + [store.state.current.to_dict()]

    summaries: list[SessionSummary] = []
    running_balance = starting_balance
    for r in records:
        session_pnl = Decimal(str(r.get("realized_pnl", "0")))
        summaries.append(
            SessionSummary(
                session_date=r["session_date"],
                starting_balance=running_balance,
                session_pnl=session_pnl,
                trade_count=int(r.get("trade_count", 0)),
                halted=bool(r.get("halted", False)),
                halt_category=r.get("halt_category"),
                halt_reason=r.get("halt_reason"),
                halted_at=r.get("halted_at"),
                target_hit_at=r.get("target_hit_at"),
                missed_opportunities=int(r.get("missed_opportunities", 0)),
                consecutive_losses_at_close=int(r.get("consecutive_losses", 0)),
            )
        )
        running_balance += session_pnl
    return summaries
