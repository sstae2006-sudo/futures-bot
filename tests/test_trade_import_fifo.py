"""The correctness-critical core of the client trade importer: FIFO
position matching (`match_fills`) and its persistence across import
batches through `TradeStore` (Phase 10.1). Covers building a position,
partial closes, full closes, reversals, commission proportional splitting,
the three P&L bases (broker-reported / computed-from-contract / unknown-
contract points-only), and -- the piece that makes multi-import correctness
possible -- open lots surviving between two separate
`commit_client_import` calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from futures_bot.research import trade_import as ti
from futures_bot.research.trade_import import OpenLot, ParsedFill, build_trade_record, match_fills
from futures_bot.research.trade_store import TradeStore, default_db_path


def fill(hour, side, qty, price, pnl=None, comm="0.62", fid=None, symbol="MESZ5", account="ACC1", minute=0):
    return ParsedFill(
        row_number=0, timestamp=datetime(2024, 1, 1, hour, minute, tzinfo=timezone.utc), symbol=symbol,
        side=side, quantity=Decimal(str(qty)), price=Decimal(str(price)), commission=Decimal(comm),
        realized_pnl=(Decimal(str(pnl)) if pnl is not None else None), account=account, fill_id=fid, raw_row={},
    )


class TestBuildAndClose:
    def test_two_buys_extend_the_position_no_closes_yet(self):
        result = match_fills([], [fill(1, "buy", 1, 100), fill(2, "buy", 1, 102)])
        assert result.closed_matches == []
        assert len(result.new_open_lots) == 2

    def test_full_close_of_a_single_lot(self):
        result = match_fills([], [fill(1, "buy", 1, 100), fill(2, "sell", 1, 110)])
        assert len(result.closed_matches) == 1
        m = result.closed_matches[0]
        assert m.entry_lot.entry_price == 100 and m.exit_fill.price == 110 and m.quantity == 1
        assert result.new_open_lots == []

    def test_partial_close_leaves_remainder_open(self):
        result = match_fills([], [fill(1, "buy", 3, 100), fill(2, "sell", 1, 110)])
        assert len(result.closed_matches) == 1
        assert result.closed_matches[0].quantity == 1
        assert len(result.new_open_lots) == 1
        assert result.new_open_lots[0].quantity_remaining == 2

    def test_fifo_order_oldest_lot_closed_first(self):
        fills = [fill(1, "buy", 1, 100), fill(2, "buy", 1, 102), fill(3, "sell", 1, 110)]
        result = match_fills([], fills)
        assert len(result.closed_matches) == 1
        assert result.closed_matches[0].entry_lot.entry_price == 100  # oldest, not newest (102)

    def test_close_spanning_two_lots_produces_two_matches(self):
        fills = [fill(1, "buy", 1, 100), fill(2, "buy", 1, 102), fill(3, "sell", 2, 110)]
        result = match_fills([], fills)
        assert len(result.closed_matches) == 2
        assert result.closed_matches[0].entry_lot.entry_price == 100
        assert result.closed_matches[1].entry_lot.entry_price == 102
        assert result.new_open_lots == []

    def test_reversal_when_closing_fill_exceeds_open_quantity(self):
        fills = [fill(1, "buy", 1, 100), fill(2, "sell", 3, 90)]
        result = match_fills([], fills)
        assert len(result.closed_matches) == 1
        assert result.closed_matches[0].quantity == 1
        assert len(result.new_open_lots) == 1
        assert result.new_open_lots[0].side == "sell"
        assert result.new_open_lots[0].quantity_remaining == 2


class TestCommissionAndPnlBasis:
    def test_commission_is_split_proportionally_across_matched_lots(self):
        fills = [fill(1, "buy", 1, 100), fill(2, "buy", 1, 100), fill(3, "sell", 2, 110, comm="1.00")]
        result = match_fills([], fills)
        assert len(result.closed_matches) == 2
        assert result.closed_matches[0].commission_share == Decimal("0.50")
        assert result.closed_matches[1].commission_share == Decimal("0.50")

    def test_broker_reported_pnl_is_preferred_when_present(self):
        lot = OpenLot(id="L1", symbol="MESZ5", side="buy", quantity_remaining=Decimal("1"), entry_price=Decimal("100"),
                       entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(), entry_fingerprint="a", raw_entry_row={})
        closing = fill(2, "sell", 1, 110, pnl="45.00")
        result = match_fills([lot], [closing])
        tr = build_trade_record(result.closed_matches[0], "imp1", "john-doe")
        assert tr.entry_metadata["pnl_basis"] == "broker_reported"
        assert tr.gross_pnl == Decimal("45.00")

    def test_computed_from_known_contract_when_no_broker_pnl(self):
        lot = OpenLot(id="L1", symbol="MESZ5", side="buy", quantity_remaining=Decimal("1"), entry_price=Decimal("100"),
                       entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(), entry_fingerprint="a", raw_entry_row={})
        closing = fill(2, "sell", 1, 110)  # +10 points, MES point value = $5
        result = match_fills([lot], [closing])
        tr = build_trade_record(result.closed_matches[0], "imp1", "john-doe")
        assert tr.entry_metadata["pnl_basis"] == "computed_from_contract"
        assert tr.gross_pnl == Decimal("50")
        assert tr.contract == "MES"

    def test_unknown_contract_falls_back_to_raw_points_with_a_flagged_caveat(self):
        lot = OpenLot(id="L1", symbol="CLZ5", side="buy", quantity_remaining=Decimal("1"), entry_price=Decimal("70"),
                       entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(), entry_fingerprint="a", raw_entry_row={})
        closing = fill(2, "sell", 1, 72, symbol="CLZ5")
        result = match_fills([lot], [closing])
        tr = build_trade_record(result.closed_matches[0], "imp1", "john-doe")
        assert tr.entry_metadata["pnl_basis"] == "points_only_unknown_contract"
        assert tr.gross_pnl == Decimal("2")  # no multiplier applied

    def test_short_side_pnl_direction_is_correct(self):
        lot = OpenLot(id="L1", symbol="MESZ5", side="sell", quantity_remaining=Decimal("1"), entry_price=Decimal("100"),
                       entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(), entry_fingerprint="a", raw_entry_row={})
        closing = fill(2, "buy", 1, 95)  # covers a short at a lower price -> profit
        result = match_fills([lot], [closing])
        tr = build_trade_record(result.closed_matches[0], "imp1", "john-doe")
        assert tr.side == "short"
        assert tr.gross_pnl == Decimal("25")  # (100-95)*5


class TestMultiAccountWarning:
    def test_warns_when_a_batch_spans_multiple_accounts(self):
        fills = [fill(1, "buy", 1, 100, account="ACC1"), fill(2, "buy", 1, 100, account="ACC2")]
        result = match_fills([], fills)
        assert any("account" in w.lower() for w in result.warnings)

    def test_no_warning_for_a_single_account(self):
        fills = [fill(1, "buy", 1, 100, account="ACC1"), fill(2, "sell", 1, 110, account="ACC1")]
        result = match_fills([], fills)
        assert result.warnings == []


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "import_fifo.db"))
    yield


class TestCrossImportCarryForward:
    """The piece that makes multi-batch imports correct: an open lot from
    one `commit_client_import` call must be visible to -- and consumed
    correctly by -- the next one."""

    def test_position_opened_in_one_batch_closes_correctly_in_the_next(self):
        store = TradeStore(default_db_path())
        store.insert_client_profile(profile_id="p1", name="carry-forward-test")

        headers = ["Order ID", "Account", "Contract", "B/S", "Filled Qty", "Fill Time", "Avg Fill Price", "Commission", "P/L"]
        mapping = ti.suggest_mapping(headers, ti.detect_format(headers))

        batch1 = [{
            "Order ID": "1", "Account": "ACC1", "Contract": "MESZ5", "B/S": "Buy", "Filled Qty": "1",
            "Fill Time": "2024-01-01T09:00:00+00:00", "Avg Fill Price": "5000", "Commission": "1.24", "P/L": "",
        }]
        plan1 = ti.plan_import(store, "p1", batch1, mapping)
        assert plan1.matches == []
        assert len(plan1.new_open_lots) == 1

        trade_records1 = [ti.build_trade_record(m, "imp1", "carry-forward-test") for m in plan1.matches]
        store.commit_client_import(
            import_id="imp1", profile_id="p1", filename="b1.csv", detected_format="tradovate",
            new_fill_rows=[
                {"fingerprint": f.fingerprint, "symbol": f.symbol, "fill_time": f.timestamp.isoformat(),
                 "side": f.side, "quantity": f.quantity, "price": f.price, "raw_row": f.raw_row}
                for f in plan1.unique_fills
            ],
            consumed_lot_ids=plan1.consumed_lot_ids, updated_lots=plan1.updated_lots,
            new_open_lots=[
                {"symbol": lot.symbol, "side": lot.side, "quantity_remaining": lot.quantity_remaining,
                 "entry_price": lot.entry_price, "entry_time": lot.entry_time,
                 "entry_fingerprint": lot.entry_fingerprint, "raw_entry_row": lot.raw_entry_row}
                for lot in plan1.new_open_lots
            ],
            trade_records=trade_records1, total_fill_rows=1, imported_fill_count=1, duplicate_fill_count=0,
            error_count=0, errors=[], warnings=[],
        )
        assert store.trade_count() == 0
        open_lots = store.fetch_open_lots("p1", "MESZ5")
        assert len(open_lots) == 1 and open_lots[0]["quantity_remaining"] == 1

        # Batch 2, a separate upload entirely, closes it.
        batch2 = [{
            "Order ID": "2", "Account": "ACC1", "Contract": "MESZ5", "B/S": "Sell", "Filled Qty": "1",
            "Fill Time": "2024-01-02T09:00:00+00:00", "Avg Fill Price": "5020", "Commission": "0.62", "P/L": "",
        }]
        plan2 = ti.plan_import(store, "p1", batch2, mapping)
        assert len(plan2.matches) == 1
        assert plan2.matches[0].entry_lot.entry_price == 5000  # the lot from batch 1

        trade_records2 = [ti.build_trade_record(m, "imp2", "carry-forward-test") for m in plan2.matches]
        store.commit_client_import(
            import_id="imp2", profile_id="p1", filename="b2.csv", detected_format="tradovate",
            new_fill_rows=[
                {"fingerprint": f.fingerprint, "symbol": f.symbol, "fill_time": f.timestamp.isoformat(),
                 "side": f.side, "quantity": f.quantity, "price": f.price, "raw_row": f.raw_row}
                for f in plan2.unique_fills
            ],
            consumed_lot_ids=plan2.consumed_lot_ids, updated_lots=plan2.updated_lots, new_open_lots=[],
            trade_records=trade_records2, total_fill_rows=1, imported_fill_count=1, duplicate_fill_count=0,
            error_count=0, errors=[], warnings=[],
        )
        assert store.trade_count() == 1
        assert store.fetch_open_lots("p1", "MESZ5") == []

    def test_reuploading_the_same_batch_reports_full_duplicates_and_creates_no_new_trades(self):
        store = TradeStore(default_db_path())
        store.insert_client_profile(profile_id="p1", name="dedup-test")
        headers = ["Order ID", "Account", "Contract", "B/S", "Filled Qty", "Fill Time", "Avg Fill Price", "Commission", "P/L"]
        mapping = ti.suggest_mapping(headers, ti.detect_format(headers))
        rows = [
            {"Order ID": "1", "Account": "ACC1", "Contract": "MESZ5", "B/S": "Buy", "Filled Qty": "1",
             "Fill Time": "2024-01-01T09:00:00+00:00", "Avg Fill Price": "5000", "Commission": "1.24", "P/L": ""},
            {"Order ID": "2", "Account": "ACC1", "Contract": "MESZ5", "B/S": "Sell", "Filled Qty": "1",
             "Fill Time": "2024-01-01T10:00:00+00:00", "Avg Fill Price": "5010", "Commission": "0.62", "P/L": ""},
        ]
        plan1 = ti.plan_import(store, "p1", rows, mapping)
        trade_records = [ti.build_trade_record(m, "imp1", "dedup-test") for m in plan1.matches]
        store.commit_client_import(
            import_id="imp1", profile_id="p1", filename="r.csv", detected_format="tradovate",
            new_fill_rows=[
                {"fingerprint": f.fingerprint, "symbol": f.symbol, "fill_time": f.timestamp.isoformat(),
                 "side": f.side, "quantity": f.quantity, "price": f.price, "raw_row": f.raw_row}
                for f in plan1.unique_fills
            ],
            consumed_lot_ids=plan1.consumed_lot_ids, updated_lots=plan1.updated_lots,
            new_open_lots=[
                {"symbol": lot.symbol, "side": lot.side, "quantity_remaining": lot.quantity_remaining,
                 "entry_price": lot.entry_price, "entry_time": lot.entry_time,
                 "entry_fingerprint": lot.entry_fingerprint, "raw_entry_row": lot.raw_entry_row}
                for lot in plan1.new_open_lots
            ],
            trade_records=trade_records, total_fill_rows=2, imported_fill_count=2, duplicate_fill_count=0,
            error_count=0, errors=[], warnings=[],
        )
        assert store.trade_count() == 1

        plan2 = ti.plan_import(store, "p1", rows, mapping)
        assert plan2.duplicate_count == 2
        assert plan2.unique_fills == []
        assert plan2.matches == []
