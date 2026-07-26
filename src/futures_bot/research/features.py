"""Turns closed trades into a flat, ML-ready dataset.

Generalizes the pattern `strategy/trend_pullback/analytics.py` already
proved out for one strategy: join acted entry decisions to closed trades
*positionally*, not by matching timestamps. That join is safe here for the
same reason it is safe there -- `RiskManager.can_enter` enforces one open
position at a time, so entries and trades are produced in the same order,
1:1, with no interleaving possible. See `backtest.runner.EntryRecord` for
where the entry side of that pairing is captured.

Two outputs, matching the split trend_pullback's own analytics already
established:

* `write_trade_log_csv` -- human-facing, exact (Decimal values kept as
  strings), one row per trade.
* `write_ml_dataset_csv` -- numeric-friendly: rounded floats, categoricals as
  plain strings, one column per feature. Because different strategies expose
  different keys in `Signal.metadata`, the feature columns are the union of
  every metadata key seen across the records being exported -- a record
  missing a given strategy's key just gets a blank cell there, same as any
  outer join.
"""

from __future__ import annotations

import csv
import json
import uuid
from dataclasses import dataclass, field, fields
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from ..backtest.runner import EntryRecord
from ..contracts import session_date, to_ct
from ..models import Trade


@dataclass(frozen=True)
class TradeRecord:
    """One closed trade, joined with the entry decision that opened it and
    enough context (session, calendar, strategy identity) to be useful on
    its own -- no join against a separate `runs` table required."""

    run_id: str
    contract: str
    strategy: str
    strategy_params: dict

    entry_time: object
    exit_time: object
    side: str
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    commission: Decimal
    net_pnl: Decimal
    holding_minutes: float
    exit_reason: str

    session_date: str
    day_of_week: str
    hour: int

    entry_reason: str
    entry_metadata: dict

    outcome: str  # "win" | "loss" | "scratch"

    # Phase 6B, all optional and all None unless the caller supplies them:
    # excursion tracking mirrors the tuple convention
    # `strategy/trend_pullback/analytics.py`'s own (strategy-local)
    # `build_trade_records` already established for MFE/MAE, generalized
    # here to any strategy's trades rather than just that one. `efficiency`
    # is how much of the available favorable move (MFE) the exit actually
    # captured, in points: realized-favorable-points / mfe_points. Regime
    # labels are the entry-time market classification -- see `api/regime.py`.
    mfe_points: Optional[Decimal] = None
    mae_points: Optional[Decimal] = None
    efficiency: Optional[Decimal] = None
    regime_trend: Optional[str] = None
    regime_volatility: Optional[str] = None
    regime_session: Optional[str] = None

    #: The bracket levels and simulated execution slippage this trade
    #: actually carried (see `models.Trade`). Optional/zero-defaulted
    #: because a client-imported fill (`research.trade_import`) has neither
    #: a bot-managed bracket nor simulated slippage to report.
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    entry_slippage: Decimal = Decimal("0")
    exit_slippage: Decimal = Decimal("0")

    #: Stable identifier for this trade, independent of the `trades` table's
    #: own autoincrement `id` -- generated here (same `uuid4().hex[:12]`
    #: convention every other entity in this codebase uses: runs, jobs,
    #: experiments, ml_models, import batches) so it exists before the row
    #: is ever inserted, and is portable across a CSV export. Auto-assigned
    #: by default so every existing caller that builds a `TradeRecord`
    #: without naming one still gets a unique id, rather than being forced
    #: to pass it -- `default_factory` runs once per instance, not once at
    #: class-definition time, so records still get *distinct* ids.
    trade_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @classmethod
    def core_field_names(cls) -> list[str]:
        """Field names excluding the two dict-valued ones (``strategy_params``,
        ``entry_metadata``), which get flattened separately for CSV export."""
        return [f.name for f in fields(cls) if f.name not in ("strategy_params", "entry_metadata")]


def build_trade_records(
    trades: Sequence[Trade],
    entries: Sequence[EntryRecord],
    *,
    run_id: str,
    contract: str,
    strategy: str,
    strategy_params: dict,
    excursions: Optional[Sequence[tuple[Decimal, Decimal]]] = None,
    regimes: Optional[Sequence[tuple[str, str, str]]] = None,
) -> list[TradeRecord]:
    """Joins closed trades with the entry decisions that opened them.

    ``trades`` and ``entries`` must be the same length and in the same
    chronological order -- the invariant the single-position-at-a-time risk
    rule guarantees for any backtest that completed without raising. A
    mismatch means that invariant was violated somewhere upstream (a bug,
    not a market outcome), so this raises rather than guessing at a join.

    ``excursions``, if given, is a same-length, same-order sequence of
    ``(mfe_points, mae_points)`` tuples (see `api/analytics.py`'s
    `compute_excursions`, which builds these from the bars a backtest
    already replayed -- this function itself never touches bars, keeping it
    usable from contexts, like `strategy/trend_pullback`, that don't have
    the raw price series to hand). ``regimes``, if given, is a same-length
    sequence of ``(trend, volatility, session)`` labels (see `api/regime.py`).
    Both default to ``None`` per trade when their respective sequence is
    omitted -- existing callers that don't pass them are unaffected.
    """
    if len(trades) != len(entries):
        raise ValueError(
            f"Mismatched lengths: {len(trades)} trades, {len(entries)} entries. These must be "
            f"recorded 1:1 with positions opened, in order -- run_backtest's single-position "
            f"invariant should make this impossible; treat a mismatch as a bug."
        )
    if excursions is not None and len(excursions) != len(trades):
        raise ValueError(f"excursions has {len(excursions)} entries but there are {len(trades)} trades.")
    if regimes is not None and len(regimes) != len(trades):
        raise ValueError(f"regimes has {len(regimes)} entries but there are {len(trades)} trades.")

    records: list[TradeRecord] = []
    for i, (trade, entry) in enumerate(zip(trades, entries)):
        ct_time = to_ct(trade.entry_time)
        holding_minutes = (trade.exit_time - trade.entry_time).total_seconds() / 60
        outcome = "win" if trade.net_pnl > 0 else ("loss" if trade.net_pnl < 0 else "scratch")

        mfe_points = mae_points = efficiency = None
        if excursions is not None:
            mfe_points, mae_points = excursions[i]
            if mfe_points is not None and mfe_points > 0:
                realized_points = (trade.exit_price - trade.entry_price) * trade.side.sign
                efficiency = realized_points / mfe_points

        regime_trend = regime_volatility = regime_session = None
        if regimes is not None:
            regime_trend, regime_volatility, regime_session = regimes[i]

        records.append(
            TradeRecord(
                run_id=run_id,
                contract=contract,
                strategy=strategy,
                strategy_params=dict(strategy_params),
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                side=trade.side.value,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                gross_pnl=trade.gross_pnl,
                commission=trade.commission,
                net_pnl=trade.net_pnl,
                holding_minutes=holding_minutes,
                exit_reason=trade.exit_reason,
                session_date=session_date(trade.entry_time).isoformat(),
                day_of_week=ct_time.strftime("%A"),
                hour=ct_time.hour,
                entry_reason=entry.reason,
                entry_metadata=dict(entry.metadata),
                outcome=outcome,
                mfe_points=mfe_points,
                mae_points=mae_points,
                efficiency=efficiency,
                regime_trend=regime_trend,
                regime_volatility=regime_volatility,
                regime_session=regime_session,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                entry_slippage=trade.entry_slippage,
                exit_slippage=trade.exit_slippage,
            )
        )
    return records


def _round(value: object, places: int = 4) -> object:
    """Rounds a Decimal for display/export; passes everything else through
    unchanged. Mirrors `trend_pullback/analytics.py::_round` -- unbounded
    Decimal division carries far more digits than are useful in a CSV."""
    if isinstance(value, Decimal):
        return value.quantize(Decimal("1").scaleb(-places))
    return value


def write_trade_log_csv(records: Sequence[TradeRecord], path: Path | str) -> None:
    """Human-facing trade log: exact values, entry metadata as a compact
    JSON cell (readable, doesn't require picking a fixed schema)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        p.write_text("", encoding="utf-8")
        return

    fieldnames = TradeRecord.core_field_names() + ["strategy_params", "entry_metadata"]
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            row = {name: _round(getattr(r, name)) for name in TradeRecord.core_field_names()}
            row["strategy_params"] = json.dumps(_jsonable(r.strategy_params), sort_keys=True)
            row["entry_metadata"] = json.dumps(_jsonable(r.entry_metadata), sort_keys=True)
            writer.writerow(row)


def write_ml_dataset_csv(records: Sequence[TradeRecord], path: Path | str) -> None:
    """Feature dataset for model training: one row per trade, one column per
    feature (metadata keys flattened rather than kept as a JSON blob),
    numeric values as plain floats, categoricals as plain strings."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    base_columns = [
        "trade_id", "run_id", "contract", "strategy", "side",
        "entry_price", "exit_price", "stop_loss", "take_profit",
        "net_pnl", "holding_minutes", "exit_reason",
        "entry_slippage", "exit_slippage",
        "session_date", "day_of_week", "hour", "entry_reason", "outcome",
        "regime_trend", "regime_volatility", "regime_session",
    ]
    # The feature columns are the union of every metadata key seen -- a
    # strategy that doesn't expose a given key just leaves that cell blank.
    metadata_keys = sorted({key for r in records for key in r.entry_metadata})
    # Strategy config, not a market feature -- kept in its own `param_`-
    # prefixed block so it's never mistaken for one (see
    # `research.ml.dataset.BACKTEST_METADATA_COLUMNS`).
    param_keys = sorted({key for r in records for key in r.strategy_params})
    param_columns = [f"param_{key}" for key in param_keys]

    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(base_columns + metadata_keys + param_columns)
        for r in records:
            base_row = [
                r.trade_id, r.run_id, r.contract, r.strategy, r.side,
                float(r.entry_price), float(r.exit_price),
                _to_float_or_blank(r.stop_loss), _to_float_or_blank(r.take_profit),
                float(r.net_pnl), round(r.holding_minutes, 2), r.exit_reason,
                float(r.entry_slippage), float(r.exit_slippage),
                r.session_date, r.day_of_week, r.hour, r.entry_reason, r.outcome,
                r.regime_trend or "", r.regime_volatility or "", r.regime_session or "",
            ]
            feature_row = [_to_float_or_blank(r.entry_metadata.get(key)) for key in metadata_keys]
            param_row = [_to_float_or_blank(r.strategy_params.get(key)) for key in param_keys]
            writer.writerow(base_row + feature_row + param_row)


def _to_float_or_blank(value: Optional[object]) -> object:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def _jsonable(value: dict) -> dict:
    """Converts Decimal values in a metadata/params dict to strings so
    `json.dumps` doesn't choke on them -- same convention `journal.py`'s
    `_json_default` already uses for the decisions log."""
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in value.items()}
