"""Durable per-session state.

The kill switch is only as good as its memory. A bot that hits its daily loss
limit, crashes, restarts, and comes back with a clean slate has no kill switch
at all — it has a speed bump. So realized P&L, trade count, and the halt flag
are written to disk on every change, keyed by CME session date.

Writes go to a temp file and are then renamed. A crash partway through a write
leaves the previous good state intact rather than a truncated file that fails
to parse on the next boot.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import uuid4

#: Windows-specific retry budget. See :meth:`StateStore._write`.
_WRITE_ATTEMPTS = 6
_WRITE_BACKOFF_SECONDS = 0.05


@dataclass
class SessionState:
    """Everything the risk manager needs to remember about the current session."""

    session_date: str
    realized_pnl: Decimal = Decimal("0")
    trade_count: int = 0
    halted: bool = False
    halt_reason: Optional[str] = None
    #: Which rule tripped the halt -- 'daily_loss' | 'profit_target' |
    #: 'consecutive_losses' -- distinct from `halt_reason` (the human-
    #: readable sentence) so a session summary can group/count by cause
    #: without parsing prose. `None` until a halt actually fires.
    halt_category: Optional[str] = None
    #: ISO timestamp (simulated bar time, not wall clock) of the halt --
    #: set once, on the first halt of the session, never overwritten by a
    #: later halt() call. This is "time stop/target was hit."
    halted_at: Optional[str] = None
    #: ISO timestamp the profit target was reached, set once. Distinct from
    #: `halted_at` since a profit-target halt sets both to the same moment,
    #: but keeping this field lets a summary say "target hit" even in a
    #: hypothetical future where hitting target doesn't itself halt.
    target_hit_at: Optional[str] = None
    #: ISO timestamp of the most recently *closed* losing trade -- the
    #: anchor `RiskManager`'s post-loss cooldown counts forward from.
    last_loss_at: Optional[str] = None
    #: Entries the risk manager declined specifically because the session
    #: was already halted -- "missed opportunities after shutdown." Not
    #: persisted on every increment (see `StateStore.record_missed_opportunity`).
    missed_opportunities: int = 0
    consecutive_losses: int = 0
    last_updated: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["realized_pnl"] = str(self.realized_pnl)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        return cls(
            session_date=d["session_date"],
            realized_pnl=Decimal(str(d.get("realized_pnl", "0"))),
            trade_count=int(d.get("trade_count", 0)),
            halted=bool(d.get("halted", False)),
            halt_reason=d.get("halt_reason"),
            halt_category=d.get("halt_category"),
            halted_at=d.get("halted_at"),
            target_hit_at=d.get("target_hit_at"),
            last_loss_at=d.get("last_loss_at"),
            missed_opportunities=int(d.get("missed_opportunities", 0)),
            consecutive_losses=int(d.get("consecutive_losses", 0)),
            last_updated=d.get("last_updated"),
        )


@dataclass
class BotState:
    """Full persisted state: the live session plus a short history."""

    version: int = 1
    current: Optional[SessionState] = None
    history: list[dict] = field(default_factory=list)


class StateStore:
    """Loads and persists :class:`BotState` as JSON."""

    MAX_HISTORY = 90  # roughly a quarter of sessions

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.state = self._read()

    def _read(self) -> BotState:
        if not self.path.exists():
            return BotState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Refuse to silently start fresh: losing state means losing the
            # kill switch, which is exactly when a quiet failure is worst.
            raise RuntimeError(
                f"State file {self.path} exists but could not be read ({exc}). "
                f"Move it aside deliberately if you intend to reset the session."
            ) from exc

        if raw.get("version") != 1:
            raise RuntimeError(f"State file {self.path} has unsupported version {raw.get('version')!r}")

        current = SessionState.from_dict(raw["current"]) if raw.get("current") else None
        return BotState(version=1, current=current, history=raw.get("history", []))

    def _write(self) -> None:
        """Persist state, replacing the previous file atomically.

        The write goes to a temp file that is then renamed over the target, so
        a crash mid-write leaves the last good state rather than a truncated
        one. Two Windows details make the naive version of this unreliable:

        * ``os.replace`` fails with ``PermissionError`` when any handle still
          holds the source or target. Defender's real-time scanner opens
          freshly-written files for a few milliseconds, which is exactly the
          window this pattern lands in.
        * A fixed temp filename turns that transient conflict into a hard
          failure whenever two writes land close together — which they do, as
          recording a trade and then tripping the kill switch both write.

        So each attempt gets its own temp name, and transient permission
        errors are retried with a short backoff. Failing to persist is raised
        rather than swallowed: a kill switch whose state did not reach disk is
        one that will be forgotten on the next restart, and that should be
        loud.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.state.version,
            "current": self.state.current.to_dict() if self.state.current else None,
            "history": self.state.history[-self.MAX_HISTORY :],
        }
        data = json.dumps(payload, indent=2)

        last_error: Optional[BaseException] = None
        for attempt in range(_WRITE_ATTEMPTS):
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
            try:
                tmp.write_text(data, encoding="utf-8")
                os.replace(tmp, self.path)
                return
            except PermissionError as exc:
                last_error = exc
                tmp.unlink(missing_ok=True)
                time.sleep(_WRITE_BACKOFF_SECONDS * (attempt + 1))
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise

        raise RuntimeError(
            f"Could not write state to {self.path} after {_WRITE_ATTEMPTS} attempts "
            f"({last_error}). If this persists, exclude the state directory from "
            f"real-time antivirus scanning, or move state_file somewhere outside it."
        )

    def session(self, for_date: date) -> SessionState:
        """Get the state for ``for_date``, rolling the session over if needed."""
        key = for_date.isoformat()
        current = self.state.current

        if current is None:
            self.state.current = SessionState(session_date=key)
            self._write()
            return self.state.current

        if current.session_date != key:
            # New session: archive the old one and start clean. This is the
            # only path that clears a halt.
            self.state.history.append(current.to_dict())
            self.state.current = SessionState(session_date=key)
            self._write()

        return self.state.current

    def record_pnl(self, for_date: date, net_pnl: Decimal) -> SessionState:
        s = self.session(for_date)
        s.realized_pnl += net_pnl
        s.trade_count += 1
        s.consecutive_losses = s.consecutive_losses + 1 if net_pnl < 0 else 0
        s.last_updated = datetime.now().astimezone().isoformat()
        self._write()
        return s

    def halt(
        self, for_date: date, reason: str, *,
        category: Optional[str] = None, at: Optional[datetime] = None,
    ) -> SessionState:
        """``category``/``at`` are keyword-only additions -- every existing
        2-arg call site keeps working unchanged. ``at`` is the *simulated*
        moment the halt fired (a backtest's replay time, not wall clock);
        falls back to wall clock only when a caller doesn't have a
        simulated `now` to give (there is no such caller today, but this
        keeps the method usable standalone). ``halted_at`` is set once and
        never overwritten by a later halt() call within the same session."""
        s = self.session(for_date)
        s.halted = True
        s.halt_reason = reason
        if category is not None:
            s.halt_category = category
        if s.halted_at is None:
            s.halted_at = (at or datetime.now().astimezone()).isoformat()
        s.last_updated = datetime.now().astimezone().isoformat()
        self._write()
        return s

    def clear_halt(self, for_date: date) -> SessionState:
        """Manual override. Deliberately not called anywhere automatically."""
        s = self.session(for_date)
        s.halted = False
        s.halt_reason = None
        s.halt_category = None
        s.halted_at = None
        self._write()
        return s

    def record_target_hit(self, for_date: date, at: datetime) -> SessionState:
        """Set once -- the first time the session's profit target is reached."""
        s = self.session(for_date)
        if s.target_hit_at is None:
            s.target_hit_at = at.isoformat()
            self._write()
        return s

    def mark_loss(self, for_date: date, at: datetime) -> SessionState:
        """Anchors the post-loss cooldown to the simulated moment a losing
        trade closed. Called once per losing trade; always overwrites, since
        the cooldown always counts from the *most recent* loss."""
        s = self.session(for_date)
        s.last_loss_at = at.isoformat()
        self._write()
        return s

    def record_missed_opportunity(self, for_date: date) -> SessionState:
        """An entry declined specifically because the session was already
        halted. Deliberately does *not* call `_write()` on every increment:
        a chatty strategy can keep signaling for the rest of the trading
        day after shutdown, and a disk write per blocked bar would turn an
        otherwise-fast backtest into a write storm for a soft analytics
        counter, not the durable kill-switch state. The count is correct
        in-memory for the life of this `StateStore` (which is what
        `run_backtest`/live sessions read back), and rides along on the
        next write anything else triggers -- only a crash between the last
        real write and process end could lose the tail of this count,
        which is an acceptable trade for a metric that isn't safety-critical."""
        s = self.session(for_date)
        s.missed_opportunities += 1
        return s
