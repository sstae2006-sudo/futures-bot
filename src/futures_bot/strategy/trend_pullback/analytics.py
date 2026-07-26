"""Per-trade analytics and the ML feature dataset.

The engine's own `Trade` record (entry/exit price, time, P&L, exit reason) is
deliberately minimal -- it's what every strategy produces, and extending it
with strategy-specific fields would leak this one strategy's concerns into
code every other strategy also depends on.

So this module builds a richer record by joining two things a *backtest run*
has after it finishes, without changing the engine or the Trade type at all:

* `broker.trades` -- the authoritative list of completed round turns, exactly
  in the order they closed.
* `strategy.entry_contexts` -- one entry per position this strategy opened,
  in the order it opened them, holding the indicator snapshot and reasoning
  at entry time.

Phase 1 trades one position at a time (the risk manager enforces this), so
positions open and close in a strict, non-overlapping sequence. That means
the two lists line up by position alone -- the Nth entry this strategy made
corresponds to the Nth trade the broker closed. No timestamp matching, no
fuzzy joins.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from ...contracts import to_ct
from ...models import Side, Trade


@dataclass(frozen=True)
class EntryContext:
    """Snapshot of everything relevant at the moment a position was opened."""

    entry_reason: str
    ema9: Decimal
    ema21: Decimal
    ema50: Decimal
    ema200: Decimal
    ema50_slope: Decimal
    ema200_slope: Decimal
    rsi: Decimal
    atr: Decimal
    adx: Decimal
    vwap: Decimal
    vwap_distance: Decimal
    volume_ratio: Decimal
    trend_direction: str
    trade_direction: str


@dataclass(frozen=True)
class TradeRecord:
    """One row per trade: the engine's Trade plus this strategy's context plus
    the excursion tracked while the position was open."""

    entry_time: object
    exit_time: object
    side: str
    entry_price: Decimal
    exit_price: Decimal
    net_pnl: Decimal
    exit_reason: str
    duration_bars: int
    mfe: Decimal  # max favorable excursion, in points, always >= 0
    mae: Decimal  # max adverse excursion, in points, always >= 0
    entry_reason: str
    ema9: Decimal
    ema21: Decimal
    ema50: Decimal
    ema200: Decimal
    ema50_slope: Decimal
    ema200_slope: Decimal
    rsi: Decimal
    atr: Decimal
    adx: Decimal
    vwap_distance: Decimal
    volume_ratio: Decimal
    time_of_day: str
    day_of_week: str
    trend_direction: str
    trade_direction: str
    outcome: str  # "win" | "loss" | "scratch"

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_row(self) -> dict:
        return {name: getattr(self, name) for name in self.field_names()}


def build_trade_records(
    trades: Sequence[Trade],
    entry_contexts: Sequence[EntryContext],
    bars_held: Sequence[int],
    excursions: Sequence[tuple[Decimal, Decimal]],
) -> list[TradeRecord]:
    """Joins broker trades with strategy-side context by position, in order.

    All four sequences must be the same length and in the same chronological
    order -- the strategy is responsible for recording exactly one context,
    one bars-held count, and one excursion pair per position it opens.
    """
    if not (len(trades) == len(entry_contexts) == len(bars_held) == len(excursions)):
        raise ValueError(
            f"Mismatched lengths: {len(trades)} trades, {len(entry_contexts)} contexts, "
            f"{len(bars_held)} durations, {len(excursions)} excursions. These must be "
            f"recorded 1:1 with positions opened, in order."
        )

    records: list[TradeRecord] = []
    for trade, ctx, duration, (mfe, mae) in zip(trades, entry_contexts, bars_held, excursions):
        outcome = "win" if trade.net_pnl > 0 else ("loss" if trade.net_pnl < 0 else "scratch")
        ct_time = to_ct(trade.entry_time)
        records.append(
            TradeRecord(
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                side=trade.side.value,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                net_pnl=trade.net_pnl,
                exit_reason=trade.exit_reason,
                duration_bars=duration,
                mfe=mfe,
                mae=mae,
                entry_reason=ctx.entry_reason,
                ema9=ctx.ema9,
                ema21=ctx.ema21,
                ema50=ctx.ema50,
                ema200=ctx.ema200,
                ema50_slope=ctx.ema50_slope,
                ema200_slope=ctx.ema200_slope,
                rsi=ctx.rsi,
                atr=ctx.atr,
                adx=ctx.adx,
                vwap_distance=ctx.vwap_distance,
                volume_ratio=ctx.volume_ratio,
                time_of_day=ct_time.strftime("%H:%M"),
                day_of_week=ct_time.strftime("%A"),
                trend_direction=ctx.trend_direction,
                trade_direction=ctx.trade_direction,
                outcome=outcome,
            )
        )
    return records


def _round(value: Decimal, places: int = 4) -> Decimal:
    """Rounds a Decimal for display/export. Unbounded Decimal division (e.g.
    an EMA or slope) carries dozens of digits of precision that are noise
    beyond a handful of places and make the CSV harder to read into pandas
    with a sane dtype."""
    quant = Decimal("1").scaleb(-places)
    return value.quantize(quant)


def write_trade_log_csv(records: Sequence[TradeRecord], path: Path | str) -> None:
    """Human-facing trade log: what happened and why, one row per trade."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        p.write_text("", encoding="utf-8")
        return
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TradeRecord.field_names())
        writer.writeheader()
        for r in records:
            row = r.as_row()
            for key, value in row.items():
                if isinstance(value, Decimal):
                    row[key] = _round(value)
            writer.writerow(row)


def write_ml_dataset_csv(records: Sequence[TradeRecord], path: Path | str) -> None:
    """Feature dataset for later model training: one row per trade, numeric
    features plus the outcome and P&L as labels.

    Deliberately a separate file from the trade log rather than the same file
    reused -- the trade log is for a person reviewing what the bot did; this
    one is meant to be loaded straight into a dataframe, so it carries only
    the columns a model would actually take as input or target, encoded
    consistently (categoricals as strings, everything numeric as plain
    numbers rather than formatted currency).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    feature_columns = [
        "ema9", "ema21", "ema50", "ema200", "ema50_slope", "ema200_slope",
        "rsi", "atr", "adx", "vwap_distance", "distance_from_ema21",
        "volume_ratio", "time_of_day", "day_of_week",
        "trend_direction", "trade_direction", "outcome", "net_pnl",
    ]

    if not records:
        with p.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerow(feature_columns)
        return

    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(feature_columns)
        for r in records:
            distance_from_ema21 = _round(r.entry_price - r.ema21)
            writer.writerow(
                [
                    _round(r.ema9), _round(r.ema21), _round(r.ema50), _round(r.ema200),
                    _round(r.ema50_slope), _round(r.ema200_slope),
                    _round(r.rsi), _round(r.atr), _round(r.adx),
                    _round(r.vwap_distance), distance_from_ema21,
                    _round(r.volume_ratio), r.time_of_day, r.day_of_week,
                    r.trend_direction, r.trade_direction, r.outcome, _round(r.net_pnl, 2),
                ]
            )
