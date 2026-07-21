"""Logging and the decision journal.

The spec asks for "detailed logging of every trade and decision", and the
distinction matters. Trades are the easy half. The hard half is the trades that
did *not* happen: signals blocked by the kill switch, entries declined because
the window had closed, holds where the strategy saw nothing. Without those, the
review step can only explain the trades that were taken, and the interesting
question during optimisation is usually why a good setup was skipped.

Two outputs:

* A human-readable log for watching it run.
* ``decisions.jsonl``, one JSON object per line, for analysis. Line-delimited
  rather than a single JSON array so an interrupted run still leaves a readable
  file, and so it can be appended to without rewriting.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from .models import Signal, Trade

LOGGER_NAME = "futures_bot"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def setup_logging(level: str = "INFO", directory: Path | str = "logs") -> logging.Logger:
    """Configure console and file logging. Safe to call more than once."""
    log_dir = Path(directory)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper()))
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S%z"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_dir / "bot.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


class DecisionJournal:
    """Append-only record of every decision the bot makes."""

    def __init__(self, directory: Path | str = "logs", enabled: bool = True) -> None:
        self.enabled = enabled
        self.path = Path(directory) / "decisions.jsonl"
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, record: dict) -> None:
        if not self.enabled:
            return
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=_json_default) + "\n")

    def decision(
        self,
        now: datetime,
        signal: Signal,
        acted: bool,
        block_reason: Optional[str] = None,
        price: Optional[Decimal] = None,
        session_pnl: Optional[Decimal] = None,
    ) -> None:
        """Record a strategy decision and what the engine did with it."""
        self._write(
            {
                "type": "decision",
                "timestamp": now,
                "action": signal.action.value,
                "strategy_reason": signal.reason,
                "acted": acted,
                # Populated when risk overrode the strategy — the field that
                # answers "why didn't it take that trade?".
                "block_reason": block_reason,
                "price": price,
                "session_pnl": session_pnl,
                "metadata": signal.metadata or {},
            }
        )

    def trade(self, trade: Trade, session_pnl: Decimal) -> None:
        self._write(
            {
                "type": "trade",
                "timestamp": trade.exit_time,
                "side": trade.side.value,
                "quantity": trade.quantity,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_time": trade.entry_time,
                "gross_pnl": trade.gross_pnl,
                "commission": trade.commission,
                "net_pnl": trade.net_pnl,
                "exit_reason": trade.exit_reason,
                "session_pnl": session_pnl,
            }
        )

    def event(self, now: datetime, kind: str, message: str, **extra) -> None:
        """Record a non-trading event: halts, connection changes, force-flats."""
        self._write(
            {"type": "event", "timestamp": now, "kind": kind, "message": message, **extra}
        )
