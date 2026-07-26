"""Pre-trade risk checks and the daily kill switch.

Every entry passes through :meth:`RiskManager.can_enter` first. The checks run
in a deliberate order — cheapest and most absolute first — so that the reason
recorded in the log is the *most fundamental* one. If the market is closed and
the account is also halted, "market closed" is the more useful explanation.

Exit checks are separate and intentionally more permissive: a rule that can
block an exit is a rule that can trap a position. Nothing here can prevent
flattening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from ..config import Settings
from ..contracts import (
    is_market_open,
    session_close_at,
    session_date,
    to_ct,
)
from ..models import Position, Trade
from ..state import StateStore


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of a risk check, always carrying a human-readable reason."""

    allowed: bool
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


def _parse_ct_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class RiskManager:
    def __init__(self, settings: Settings, store: StateStore) -> None:
        self.settings = settings
        self.store = store
        self._start = _parse_ct_time(settings.session.start_ct)
        self._end = _parse_ct_time(settings.session.end_ct)

    # --- Queries ---

    def session_pnl(self, now: datetime) -> Decimal:
        return self.store.session(session_date(now)).realized_pnl

    def is_halted(self, now: datetime) -> bool:
        return self.store.session(session_date(now)).halted

    def flatten_deadline(self, now: datetime) -> datetime:
        """The moment an open position must be closed by."""
        return session_close_at(now) - timedelta(
            minutes=self.settings.session.flatten_before_close_minutes
        )

    # --- Entry gate ---

    def can_enter(self, now: datetime, position: Optional[Position]) -> RiskDecision:
        if position is not None:
            return RiskDecision(False, "Already in a position; Phase 1 trades one at a time.")

        if not is_market_open(now):
            return RiskDecision(False, "Market is closed (weekend or 16:00–17:00 CT halt).")

        sd = session_date(now)
        state = self.store.session(sd)

        if state.halted:
            # "Missed opportunity after shutdown" -- the strategy still
            # wanted to enter, but the session already ended for the day.
            self.store.record_missed_opportunity(sd)
            return RiskDecision(False, f"Session halted: {state.halt_reason}")

        # Checked before the window test so a breach halts the session rather
        # than merely being declined for the moment.
        limit = self.settings.risk.daily_max_loss
        if state.realized_pnl <= -limit:
            self.store.halt(
                sd,
                f"Daily loss limit reached (realized ${state.realized_pnl}, limit ${-limit}).",
                category="daily_loss", at=now,
            )
            self.store.record_missed_opportunity(sd)
            return RiskDecision(
                False, f"Daily loss limit reached: realized ${state.realized_pnl} vs limit ${-limit}."
            )

        # The mirror image of the loss limit: stop for the day once the
        # profit target is reached, same "checked before the window test so
        # a breach halts the session" reasoning.
        target = self.settings.risk.profit_target
        if target is not None and state.realized_pnl >= target:
            self.store.record_target_hit(sd, now)
            self.store.halt(
                sd,
                f"Profit target reached (realized ${state.realized_pnl}, target ${target}).",
                category="profit_target", at=now,
            )
            self.store.record_missed_opportunity(sd)
            return RiskDecision(
                False, f"Profit target reached: realized ${state.realized_pnl} vs target ${target}."
            )

        max_losses = self.settings.risk.max_consecutive_losses
        if max_losses is not None and state.consecutive_losses >= max_losses:
            self.store.halt(
                sd,
                f"{state.consecutive_losses} consecutive losses reached (limit {max_losses}).",
                category="consecutive_losses", at=now,
            )
            self.store.record_missed_opportunity(sd)
            return RiskDecision(
                False, f"{state.consecutive_losses} consecutive losses reached (limit {max_losses})."
            )

        # A pause, not a session end: trading resumes on its own once the
        # cooldown elapses, so this never touches `halted`.
        cooldown = self.settings.risk.cooldown_minutes_after_loss
        if cooldown and state.last_loss_at is not None:
            elapsed = now - datetime.fromisoformat(state.last_loss_at)
            remaining = timedelta(minutes=cooldown) - elapsed
            if remaining > timedelta(0):
                return RiskDecision(
                    False,
                    f"Cooldown after a loss: {remaining.total_seconds() / 60:.1f} more minute(s).",
                )

        if state.trade_count >= self.settings.risk.max_trades_per_session:
            return RiskDecision(
                False,
                f"Trade cap reached ({state.trade_count}/"
                f"{self.settings.risk.max_trades_per_session} this session).",
            )

        ct_now = to_ct(now)
        if not (self._start <= ct_now.time() <= self._end):
            return RiskDecision(
                False,
                f"Outside trading window {self.settings.session.start_ct}–"
                f"{self.settings.session.end_ct} CT (now {ct_now:%H:%M} CT).",
            )

        # Refuse an entry that cannot be given its full holding window before
        # the force-flat deadline; otherwise the trade is opened only to be
        # closed moments later at whatever price is available.
        if now >= self.flatten_deadline(now):
            return RiskDecision(
                False,
                f"Too close to the force-flat deadline "
                f"({self.flatten_deadline(now):%H:%M %Z}).",
            )

        # A stop that would breach the daily limit on its own makes the limit
        # unenforceable — the loss overshoots before the switch can fire.
        projected = state.realized_pnl - self.settings.risk_per_trade
        if projected < -limit:
            return RiskDecision(
                False,
                f"A full stop-out (${self.settings.risk_per_trade}) would take the session to "
                f"${projected}, past the ${-limit} limit.",
            )

        return RiskDecision(True, "All pre-trade checks passed.")

    # --- Exit gate ---

    def must_flatten(self, now: datetime, position: Optional[Position]) -> RiskDecision:
        """Whether an open position has to be closed regardless of the strategy."""
        if position is None:
            return RiskDecision(False, "No position.")

        if now >= self.flatten_deadline(now):
            return RiskDecision(
                True,
                f"Force-flat before the 16:00 CT close "
                f"(deadline {self.flatten_deadline(now):%H:%M %Z}). "
                f"Holding overnight requires full maintenance margin.",
            )

        state = self.store.session(session_date(now))
        if state.halted:
            return RiskDecision(True, f"Session halted: {state.halt_reason}")

        return RiskDecision(False, "No forced exit required.")

    # --- Bookkeeping ---

    def record_trade(self, now: datetime, trade: Trade) -> None:
        """Record a completed round turn and trip the kill switch (daily
        loss, profit target, or consecutive-loss cap) if breached -- the
        primary point any of these fire; `can_enter`'s copies of the same
        checks are defensive redundancy, matching the pattern the daily-loss
        check already established."""
        sd = session_date(now)
        state = self.store.record_pnl(sd, trade.net_pnl)

        if trade.net_pnl < 0:
            self.store.mark_loss(sd, now)

        limit = self.settings.risk.daily_max_loss
        if state.realized_pnl <= -limit and not state.halted:
            self.store.halt(
                sd,
                f"Daily loss limit reached (realized ${state.realized_pnl}, limit ${-limit}).",
                category="daily_loss", at=now,
            )

        target = self.settings.risk.profit_target
        if target is not None and state.realized_pnl >= target and not state.halted:
            self.store.record_target_hit(sd, now)
            self.store.halt(
                sd,
                f"Profit target reached (realized ${state.realized_pnl}, target ${target}).",
                category="profit_target", at=now,
            )

        max_losses = self.settings.risk.max_consecutive_losses
        if max_losses is not None and state.consecutive_losses >= max_losses and not state.halted:
            self.store.halt(
                sd,
                f"{state.consecutive_losses} consecutive losses reached (limit {max_losses}).",
                category="consecutive_losses", at=now,
            )

    def describe(self, now: datetime) -> str:
        """One-line status summary for the log."""
        state = self.store.session(session_date(now))
        return (
            f"session={state.session_date} pnl=${state.realized_pnl} "
            f"trades={state.trade_count}/{self.settings.risk.max_trades_per_session} "
            f"halted={state.halted}"
        )
