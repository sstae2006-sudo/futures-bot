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
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional


@dataclass
class SessionState:
    """Everything the risk manager needs to remember about the current session."""

    session_date: str
    realized_pnl: Decimal = Decimal("0")
    trade_count: int = 0
    halted: bool = False
    halt_reason: Optional[str] = None
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.state.version,
            "current": self.state.current.to_dict() if self.state.current else None,
            "history": self.state.history[-self.MAX_HISTORY :],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

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

    def halt(self, for_date: date, reason: str) -> SessionState:
        s = self.session(for_date)
        s.halted = True
        s.halt_reason = reason
        s.last_updated = datetime.now().astimezone().isoformat()
        self._write()
        return s

    def clear_halt(self, for_date: date) -> SessionState:
        """Manual override. Deliberately not called anywhere automatically."""
        s = self.session(for_date)
        s.halted = False
        s.halt_reason = None
        self._write()
        return s
