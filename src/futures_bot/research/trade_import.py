"""Universal client trade importer: format/delimiter detection, column
mapping, row validation, duplicate fingerprinting, and FIFO position
matching -- turns a broker's raw fill/execution export into closed
`TradeRecord` rows for the same `TradeStore` every backtest/live session
already writes to (see `TradeStore.commit_client_import`).

Deliberately targets fill-by-fill execution records, not a broker's own
pre-closed "Trades" report: round-trip trades are reconstructed here via
FIFO lot matching (`match_fills`), including partial fills, multi-lot
closes, and position reversals -- a real position-tracking component, not
just a column-mapping/CSV-parsing job.

The two named formats (Tradovate, NinjaTrader) are detected against header
names commonly published for each platform's fill/execution export --
**not verified against a live account export** (this environment has
neither), the same honesty already established in `brokers/tradovate.py`'s
module docstring for the live broker adapter. Detection is therefore never
trusted blindly: the column-mapping wizard is always shown to the user and
always editable, regardless of how confidently a format was recognized.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence

from ..contracts import CME_TZ, CONTRACTS, get_contract
from .features import TradeRecord

CANONICAL_FIELDS = (
    "timestamp", "symbol", "side", "quantity", "price", "commission", "realized_pnl", "account", "fill_id",
)
REQUIRED_FIELDS = ("timestamp", "symbol", "side", "quantity", "price")

# --- Format detection --------------------------------------------------

TRADOVATE_FINGERPRINT = {"contract", "b/s", "fill time", "filled qty", "avg fill price"}
NINJATRADER_FINGERPRINT = {"instrument", "action", "time", "quantity", "price"}

_TRADOVATE_MAPPING = {
    "timestamp": "Fill Time", "symbol": "Contract", "side": "B/S", "quantity": "Filled Qty",
    "price": "Avg Fill Price", "commission": "Commission", "realized_pnl": "P/L",
    "account": "Account", "fill_id": "Order ID",
}
_NINJATRADER_MAPPING = {
    "timestamp": "Time", "symbol": "Instrument", "side": "Action", "quantity": "Quantity",
    "price": "Price", "commission": "Commission", "realized_pnl": "Profit",
    "account": "Account", "fill_id": "Order ID",
}

_FUZZY_HINTS = {
    "timestamp": ("time", "date", "timestamp", "when"),
    "symbol": ("symbol", "instrument", "contract", "product", "sym", "ticker"),
    "side": ("side", "action", "b/s", "buy/sell", "dir"),
    "quantity": ("qty", "quantity", "size", "filled"),
    "price": ("price", "fill price", "avg fill", "px"),
    "commission": ("commission", "fee"),
    "realized_pnl": ("p/l", "pnl", "profit", "realized"),
    "account": ("account",),
    "fill_id": ("order id", "fill id", "execution id"),
}


def detect_format(headers: Sequence[str]) -> str:
    normalized = {h.strip().lower() for h in headers}
    if TRADOVATE_FINGERPRINT <= normalized:
        return "tradovate"
    if NINJATRADER_FINGERPRINT <= normalized:
        return "ninjatrader"
    return "generic"


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def suggest_mapping(headers: Sequence[str], detected_format: str) -> dict:
    """Best-guess `canonical field -> raw header` mapping. Exact fingerprint
    lookup for a recognized format, fuzzy substring matching (against
    `_FUZZY_HINTS`) otherwise. Always a *suggestion* -- the frontend wizard
    shows every canonical field with this pre-filled but editable, since a
    generic export's headers are never trusted to have been guessed right."""
    base = {"tradovate": _TRADOVATE_MAPPING, "ninjatrader": _NINJATRADER_MAPPING}.get(detected_format, {})
    header_lookup = {h.strip().lower(): h for h in headers}

    mapping: dict[str, Optional[str]] = {}
    for field_name in CANONICAL_FIELDS:
        exact = base.get(field_name)
        if exact and exact.lower() in header_lookup:
            mapping[field_name] = header_lookup[exact.lower()]
            continue
        found = None
        for hint in _FUZZY_HINTS.get(field_name, ()):
            for norm, original in header_lookup.items():
                if hint in norm:
                    found = original
                    break
            if found:
                break
        mapping[field_name] = found
    return mapping


# --- Side normalization --------------------------------------------------

_SIDE_LOOKUP = {
    "buy": "buy", "b": "buy", "long": "buy", "cover": "buy", "buytocover": "buy",
    "sell": "sell", "s": "sell", "short": "sell", "sellshort": "sell",
}


def normalize_side(raw: str) -> Optional[str]:
    key = re.sub(r"[^a-z]", "", raw.strip().lower())
    return _SIDE_LOOKUP.get(key)


# --- Reading raw rows ----------------------------------------------------

def read_csv_rows(content: bytes) -> tuple[list[str], list[dict]]:
    text = content.decode("utf-8-sig", errors="replace")
    delimiter = sniff_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = list(reader.fieldnames or [])
    rows = [dict(row) for row in reader]
    return headers, rows


def read_excel_rows(content: bytes) -> tuple[list[str], list[dict]]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = workbook.active
    rows_iter = sheet.iter_rows(values_only=True)
    header_row = next(rows_iter, ())
    headers = [str(h) if h is not None else "" for h in header_row]
    rows = []
    for raw in rows_iter:
        rows.append({headers[i]: ("" if (i >= len(raw) or raw[i] is None) else str(raw[i])) for i in range(len(headers))})
    return headers, rows


def read_rows(content: bytes, is_excel: bool) -> tuple[list[str], list[dict]]:
    return read_excel_rows(content) if is_excel else read_csv_rows(content)


# --- Row validation --------------------------------------------------------

@dataclass
class ParsedFill:
    row_number: int
    timestamp: datetime
    symbol: str
    side: str  # 'buy' | 'sell'
    quantity: Decimal
    price: Decimal
    commission: Decimal
    realized_pnl: Optional[Decimal]
    account: Optional[str]
    fill_id: Optional[str]
    raw_row: dict

    @property
    def fingerprint(self) -> str:
        if self.fill_id:
            return f"id:{self.fill_id}"
        basis = f"{self.symbol}|{self.timestamp.isoformat()}|{self.side}|{self.quantity}|{self.price}"
        return hashlib.sha256(basis.encode()).hexdigest()[:24]


def _parse_decimal(raw: str) -> Decimal:
    cleaned = raw.replace(",", "").replace("$", "").strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    value = Decimal(cleaned)
    return -value if negative else value


_TIMESTAMP_FORMATS = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y")


def _parse_timestamp(raw: str) -> datetime:
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        dt = None
        for fmt in _TIMESTAMP_FORMATS:
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            raise ValueError(f"Could not parse timestamp {raw!r}.")
    if dt.tzinfo is None:
        # Every other ingestion path in this codebase (`backtest/data.py`'s
        # CSV loader) treats a naive timestamp as Central Time -- session
        # boundaries, `contracts.session_date`, and the trading-hours filter
        # all assume CT. Tradovate/NinjaTrader fill exports (the two named
        # formats this importer targets) are virtually always naive,
        # exchange/account-local time, not UTC -- defaulting to UTC here
        # silently shifted every imported fill by 5-6 hours (DST-dependent),
        # corrupting `session_date`/`day_of_week`/`hour` for every imported
        # trade. Matching the rest of the codebase's convention instead.
        dt = dt.replace(tzinfo=CME_TZ)
    return dt


def apply_mapping_and_validate(raw_rows: Sequence[dict], mapping: dict) -> tuple[list[ParsedFill], list[dict]]:
    """Returns `(parsed_fills, errors)`. `errors` is `[{"row": n, "message": str}, ...]`
    -- a row that fails validation is never silently dropped, only excluded
    and reported, matching this codebase's rule that a bad input is a
    visible caveat, not a quiet omission (see `BacktestMetrics.caveats()`)."""
    fills: list[ParsedFill] = []
    errors: list[dict] = []

    for i, raw in enumerate(raw_rows, start=1):
        try:
            values = {}
            for field_name in REQUIRED_FIELDS:
                col = mapping.get(field_name)
                if not col or col not in raw or raw[col] in (None, ""):
                    raise ValueError(f"Missing required field {field_name!r} (mapped to column {col!r}).")
                values[field_name] = raw[col]

            side = normalize_side(str(values["side"]))
            if side is None:
                raise ValueError(f"Unrecognized side value {values['side']!r}.")

            quantity = _parse_decimal(str(values["quantity"]))
            if quantity <= 0:
                raise ValueError(f"Quantity must be positive, got {quantity}.")

            price = _parse_decimal(str(values["price"]))
            timestamp = _parse_timestamp(str(values["timestamp"]))

            commission_col = mapping.get("commission")
            commission = (
                _parse_decimal(str(raw[commission_col]))
                if commission_col and raw.get(commission_col) not in (None, "") else Decimal("0")
            )

            pnl_col = mapping.get("realized_pnl")
            realized_pnl = (
                _parse_decimal(str(raw[pnl_col])) if pnl_col and raw.get(pnl_col) not in (None, "") else None
            )

            account_col = mapping.get("account")
            account = raw.get(account_col) if account_col else None

            fill_id_col = mapping.get("fill_id")
            fill_id = raw.get(fill_id_col) if fill_id_col else None

            fills.append(ParsedFill(
                row_number=i, timestamp=timestamp, symbol=str(values["symbol"]).strip(), side=side,
                quantity=quantity, price=price, commission=commission, realized_pnl=realized_pnl,
                account=(str(account).strip() if account else None),
                fill_id=(str(fill_id).strip() if fill_id else None),
                raw_row=dict(raw),
            ))
        except (ValueError, InvalidOperation, KeyError) as exc:
            errors.append({"row": i, "message": str(exc)})

    return fills, errors


# --- FIFO position matching ------------------------------------------------

@dataclass
class OpenLot:
    id: Optional[str]  # None until persisted
    symbol: str
    side: str  # 'buy' | 'sell' -- the open position's own direction
    quantity_remaining: Decimal
    entry_price: Decimal
    entry_time: str  # ISO 8601
    entry_fingerprint: str
    raw_entry_row: dict


@dataclass
class ClosedMatch:
    entry_lot: OpenLot
    exit_fill: ParsedFill
    quantity: Decimal
    commission_share: Decimal
    realized_pnl_share: Optional[Decimal]  # broker-reported P&L attributed to this match, if available


@dataclass
class MatchResult:
    closed_matches: list = field(default_factory=list)
    consumed_lot_ids: list = field(default_factory=list)
    updated_lots: list = field(default_factory=list)   # (lot_id, new_quantity_remaining)
    new_open_lots: list = field(default_factory=list)  # OpenLot rows (id=None) still open at batch end
    warnings: list = field(default_factory=list)


def match_fills(existing_open_lots: Sequence[OpenLot], fills: Sequence[ParsedFill]) -> MatchResult:
    """FIFO position matching for one symbol. `existing_open_lots` are
    mutated in place (their `quantity_remaining` is decremented as they're
    consumed) -- the before/after comparison against the original objects
    is exactly how `consumed_lot_ids`/`updated_lots` get computed, once,
    after all fills are processed, regardless of how many times a given
    lot was touched within this batch.

    Same-direction fill (or no open position) -> extends the queue.
    Opposite-direction fill -> consumes oldest lots first; a fill spanning
    multiple lots at different entry prices produces one `ClosedMatch` per
    lot, each keeping its own real entry price/time. A closing fill larger
    than the whole open position reverses it: the excess becomes a new open
    lot in the new direction.
    """
    original_quantities = {lot.id: lot.quantity_remaining for lot in existing_open_lots if lot.id is not None}
    queue: deque = deque(sorted(existing_open_lots, key=lambda lot: lot.entry_time))

    closed_matches: list[ClosedMatch] = []
    accounts_seen: set[str] = set()

    for fill in sorted(fills, key=lambda f: f.timestamp):
        if fill.account:
            accounts_seen.add(fill.account)

        if not queue or queue[0].side == fill.side:
            queue.append(OpenLot(
                id=None, symbol=fill.symbol, side=fill.side, quantity_remaining=fill.quantity,
                entry_price=fill.price, entry_time=fill.timestamp.isoformat(),
                entry_fingerprint=fill.fingerprint, raw_entry_row=fill.raw_row,
            ))
            continue

        remaining = fill.quantity
        fill_total = fill.quantity
        while remaining > 0 and queue and queue[0].side != fill.side:
            lot = queue[0]
            take = min(lot.quantity_remaining, remaining)
            commission_share = (fill.commission * take / fill_total) if fill_total else Decimal("0")
            realized_share = (
                (fill.realized_pnl * take / fill_total) if fill.realized_pnl is not None and fill_total else None
            )
            closed_matches.append(ClosedMatch(
                entry_lot=lot, exit_fill=fill, quantity=take,
                commission_share=commission_share, realized_pnl_share=realized_share,
            ))
            lot.quantity_remaining -= take
            remaining -= take
            if lot.quantity_remaining == 0:
                queue.popleft()

        if remaining > 0:
            # Reversal: the excess opens a new position in the new direction.
            queue.append(OpenLot(
                id=None, symbol=fill.symbol, side=fill.side, quantity_remaining=remaining,
                entry_price=fill.price, entry_time=fill.timestamp.isoformat(),
                entry_fingerprint=fill.fingerprint, raw_entry_row=fill.raw_row,
            ))

    consumed_lot_ids = [
        lot_id for lot_id, original in original_quantities.items()
        if not any(lot.id == lot_id for lot in queue)
    ]
    updated_lots = [
        (lot.id, lot.quantity_remaining) for lot in queue
        if lot.id is not None and lot.quantity_remaining != original_quantities.get(lot.id)
    ]
    new_open_lots = [lot for lot in queue if lot.id is None and lot.quantity_remaining > 0]

    warnings = []
    if len(accounts_seen) > 1:
        warnings.append(
            f"This batch contains {len(accounts_seen)} different account values "
            f"({', '.join(sorted(accounts_seen))}) -- consider splitting them into separate client profiles."
        )

    return MatchResult(
        closed_matches=closed_matches, consumed_lot_ids=consumed_lot_ids,
        updated_lots=updated_lots, new_open_lots=new_open_lots, warnings=warnings,
    )


# --- TradeRecord construction -----------------------------------------------

_MONTH_CODE_RE = re.compile(r"[FGHJKMNQUVXZ]\d{1,2}$")


def extract_product_code(symbol: str) -> Optional[str]:
    """Best-effort: strips a NinjaTrader-style " 12-25" suffix and/or a
    trailing futures month-code+year (e.g. "Z5", "H26") from a raw symbol,
    then checks the remainder against `contracts.CONTRACTS`. Returns `None`
    if the cleaned symbol isn't one of this project's four configured
    contracts (MES/MNQ/M2K/MYM) -- callers must degrade gracefully, not
    guess a dollar value for a contract with no known point value."""
    cleaned = re.sub(r"[\s\-].*$", "", symbol.strip().upper())
    cleaned = _MONTH_CODE_RE.sub("", cleaned)
    return cleaned if cleaned in CONTRACTS else None


def build_trade_record(match: ClosedMatch, import_id: str, profile_name: str) -> TradeRecord:
    lot = match.entry_lot
    fill = match.exit_fill
    side = "long" if lot.side == "buy" else "short"
    direction = 1 if side == "long" else -1

    if match.realized_pnl_share is not None:
        gross_pnl = match.realized_pnl_share
        pnl_basis = "broker_reported"
    else:
        product_code = extract_product_code(lot.symbol)
        if product_code is not None:
            spec = get_contract(product_code)
            gross_pnl = spec.points_to_dollars((fill.price - lot.entry_price) * direction, int(match.quantity))
            pnl_basis = "computed_from_contract"
        else:
            gross_pnl = (fill.price - lot.entry_price) * direction * match.quantity
            pnl_basis = "points_only_unknown_contract"

    net_pnl = gross_pnl - match.commission_share
    entry_time = datetime.fromisoformat(lot.entry_time)
    exit_time = fill.timestamp
    outcome = "win" if net_pnl > 0 else ("loss" if net_pnl < 0 else "scratch")

    return TradeRecord(
        run_id=import_id,
        contract=extract_product_code(lot.symbol) or lot.symbol,
        strategy=f"import:{profile_name}",
        strategy_params={},
        entry_time=entry_time, exit_time=exit_time, side=side,
        entry_price=lot.entry_price, exit_price=fill.price,
        gross_pnl=gross_pnl, commission=match.commission_share, net_pnl=net_pnl,
        holding_minutes=(exit_time - entry_time).total_seconds() / 60.0,
        exit_reason="imported",
        session_date=exit_time.date().isoformat(), day_of_week=exit_time.strftime("%A"), hour=exit_time.hour,
        entry_reason=f"Imported fill ({pnl_basis})",
        entry_metadata={"raw_entry_fill": lot.raw_entry_row, "raw_exit_fill": fill.raw_row, "pnl_basis": pnl_basis},
        outcome=outcome,
    )


# --- End-to-end pipeline (shared by preview and confirm) -------------------

@dataclass
class ImportPlan:
    """Everything computed from a set of raw rows against the current
    persisted state -- read-only by construction (nothing here writes to
    the store). The preview endpoint renders this directly; the confirm
    endpoint hands it straight to `TradeStore.commit_client_import`."""
    fills: list
    unique_fills: list
    duplicate_count: int
    errors: list
    matches: list
    consumed_lot_ids: list
    updated_lots: list
    new_open_lots: list
    warnings: list

    @property
    def trade_records_preview(self) -> list:
        return [
            {
                "entry_time": m.entry_lot.entry_time, "exit_time": m.exit_fill.timestamp.isoformat(),
                "symbol": m.entry_lot.symbol, "side": ("long" if m.entry_lot.side == "buy" else "short"),
                "quantity": str(m.quantity), "entry_price": str(m.entry_lot.entry_price),
                "exit_price": str(m.exit_fill.price),
            }
            for m in self.matches
        ]


def plan_import(store, profile_id: str, raw_rows: Sequence[dict], mapping: dict) -> ImportPlan:
    """Read-only: fingerprints against the existing fill ledger, loads
    current open lots, and runs `match_fills` per symbol -- without writing
    anything. Used for both the preview step and (immediately before
    `commit_client_import`) the confirm step, so "what you previewed" and
    "what gets committed" are computed by the exact same code path."""
    fills, errors = apply_mapping_and_validate(raw_rows, mapping)

    fingerprints = [f.fingerprint for f in fills]
    existing_fp = store.fetch_existing_fingerprints(profile_id, fingerprints) if fingerprints else set()
    seen_in_batch: set[str] = set()
    unique_fills: list[ParsedFill] = []
    duplicate_count = 0
    for f in fills:
        if f.fingerprint in existing_fp or f.fingerprint in seen_in_batch:
            duplicate_count += 1
            continue
        seen_in_batch.add(f.fingerprint)
        unique_fills.append(f)

    by_symbol: dict[str, list[ParsedFill]] = defaultdict(list)
    for f in unique_fills:
        by_symbol[f.symbol].append(f)

    matches, consumed, updated, new_lots, warnings = [], [], [], [], []
    for symbol, symbol_fills in by_symbol.items():
        existing_rows = store.fetch_open_lots(profile_id, symbol)
        existing_lots = [
            OpenLot(
                id=r["id"], symbol=r["symbol"], side=r["side"], quantity_remaining=r["quantity_remaining"],
                entry_price=r["entry_price"], entry_time=r["entry_time"],
                entry_fingerprint=r["entry_fingerprint"], raw_entry_row=r["raw_entry_row"],
            )
            for r in existing_rows
        ]
        result = match_fills(existing_lots, symbol_fills)
        matches.extend(result.closed_matches)
        consumed.extend(result.consumed_lot_ids)
        updated.extend(result.updated_lots)
        new_lots.extend(result.new_open_lots)
        warnings.extend(result.warnings)

    return ImportPlan(
        fills=fills, unique_fills=unique_fills, duplicate_count=duplicate_count, errors=errors,
        matches=matches, consumed_lot_ids=consumed, updated_lots=updated, new_open_lots=new_lots,
        warnings=warnings,
    )
