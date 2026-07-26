"""Format/delimiter detection, column-mapping suggestion, side
normalization, and row validation for the universal client trade importer
(Phase 10.1) -- see `research/trade_import.py`'s module docstring."""

from __future__ import annotations

import io

import pytest

from futures_bot.research import trade_import as ti

TRADOVATE_HEADERS = [
    "Order ID", "Account", "Contract", "Product", "B/S", "Filled Qty",
    "Fill Time", "Avg Fill Price", "Commission", "P/L",
]
NINJATRADER_HEADERS = [
    "Instrument", "Account", "Action", "Quantity", "Price", "Time", "Commission", "Profit", "Order ID",
]
GENERIC_HEADERS = ["Sym", "Dir", "Qty", "Px", "When"]


class TestFormatDetection:
    def test_detects_tradovate(self):
        assert ti.detect_format(TRADOVATE_HEADERS) == "tradovate"

    def test_detects_ninjatrader(self):
        assert ti.detect_format(NINJATRADER_HEADERS) == "ninjatrader"

    def test_falls_back_to_generic_for_unrecognized_headers(self):
        assert ti.detect_format(GENERIC_HEADERS) == "generic"

    def test_detection_is_case_and_whitespace_insensitive(self):
        shuffled = [h.upper() + "  " for h in TRADOVATE_HEADERS]
        assert ti.detect_format(shuffled) == "tradovate"


class TestDelimiterSniffing:
    def test_sniffs_comma(self):
        assert ti.sniff_delimiter("a,b,c\n1,2,3\n") == ","

    def test_sniffs_semicolon(self):
        assert ti.sniff_delimiter("a;b;c\n1;2;3\n") == ";"

    def test_falls_back_to_comma_on_ambiguous_input(self):
        assert ti.sniff_delimiter("just one column\nanother\n") == ","


class TestMappingSuggestion:
    def test_exact_mapping_for_tradovate(self):
        mapping = ti.suggest_mapping(TRADOVATE_HEADERS, "tradovate")
        assert mapping["timestamp"] == "Fill Time"
        assert mapping["symbol"] == "Contract"
        assert mapping["side"] == "B/S"
        assert mapping["quantity"] == "Filled Qty"
        assert mapping["price"] == "Avg Fill Price"

    def test_exact_mapping_for_ninjatrader(self):
        mapping = ti.suggest_mapping(NINJATRADER_HEADERS, "ninjatrader")
        assert mapping["timestamp"] == "Time"
        assert mapping["symbol"] == "Instrument"
        assert mapping["side"] == "Action"

    def test_fuzzy_mapping_for_generic_headers(self):
        mapping = ti.suggest_mapping(GENERIC_HEADERS, "generic")
        assert mapping["quantity"] == "Qty"
        assert mapping["price"] == "Px"
        assert mapping["timestamp"] == "When"

    def test_unmatched_fields_are_none_not_a_crash(self):
        mapping = ti.suggest_mapping(["Totally", "Unrelated", "Headers"], "generic")
        assert mapping["symbol"] is None
        assert mapping["fill_id"] is None


class TestSideNormalization:
    @pytest.mark.parametrize("raw,expected", [
        ("Buy", "buy"), ("B", "buy"), ("Long", "buy"), ("BuyToCover", "buy"),
        ("Sell", "sell"), ("S", "sell"), ("Short", "sell"), ("SellShort", "sell"),
        ("  sell  ", "sell"),
    ])
    def test_known_values(self, raw, expected):
        assert ti.normalize_side(raw) == expected

    def test_unknown_value_returns_none(self):
        assert ti.normalize_side("Sideways") is None


class TestReadRows:
    def test_reads_csv_with_utf8_bom(self):
        content = "﻿Sym,Dir,Qty,Px,When\nMES,Buy,1,100,2024-01-01T00:00:00+00:00\n".encode("utf-8")
        headers, rows = ti.read_csv_rows(content)
        assert headers == ["Sym", "Dir", "Qty", "Px", "When"]
        assert rows == [{"Sym": "MES", "Dir": "Buy", "Qty": "1", "Px": "100", "When": "2024-01-01T00:00:00+00:00"}]

    def test_reads_excel(self):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Sym", "Dir", "Qty", "Px", "When"])
        ws.append(["MES", "Buy", 1, 100, "2024-01-01T00:00:00+00:00"])
        buffer = io.BytesIO()
        wb.save(buffer)
        headers, rows = ti.read_excel_rows(buffer.getvalue())
        assert headers == ["Sym", "Dir", "Qty", "Px", "When"]
        assert rows[0]["Sym"] == "MES"
        assert rows[0]["Qty"] == "1"


class TestRowValidation:
    def _mapping(self):
        return {"timestamp": "When", "symbol": "Sym", "side": "Dir", "quantity": "Qty", "price": "Px"}

    def test_valid_row_parses(self):
        rows = [{"Sym": "MES", "Dir": "Buy", "Qty": "2", "Px": "5000.25", "When": "2024-01-01T09:00:00+00:00"}]
        fills, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert errors == []
        assert len(fills) == 1
        assert fills[0].side == "buy"
        assert fills[0].quantity == 2

    def test_missing_required_field_is_reported_not_dropped_silently(self):
        rows = [{"Sym": "MES", "Dir": "Buy", "Qty": "", "Px": "5000", "When": "2024-01-01T09:00:00+00:00"}]
        fills, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert fills == []
        assert len(errors) == 1
        assert errors[0]["row"] == 1
        assert "quantity" in errors[0]["message"]

    def test_unrecognized_side_is_an_error(self):
        rows = [{"Sym": "MES", "Dir": "Sideways", "Qty": "1", "Px": "5000", "When": "2024-01-01T09:00:00+00:00"}]
        _, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert len(errors) == 1

    def test_negative_or_zero_quantity_is_an_error(self):
        rows = [{"Sym": "MES", "Dir": "Buy", "Qty": "0", "Px": "5000", "When": "2024-01-01T09:00:00+00:00"}]
        _, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert len(errors) == 1

    def test_one_bad_row_does_not_stop_the_rest_from_parsing(self):
        rows = [
            {"Sym": "MES", "Dir": "Buy", "Qty": "bad", "Px": "5000", "When": "2024-01-01T09:00:00+00:00"},
            {"Sym": "MES", "Dir": "Sell", "Qty": "1", "Px": "5010", "When": "2024-01-01T10:00:00+00:00"},
        ]
        fills, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert len(fills) == 1
        assert len(errors) == 1
        assert errors[0]["row"] == 1

    def test_parenthesized_negative_and_comma_thousands_parse(self):
        rows = [{"Sym": "MES", "Dir": "Buy", "Qty": "1,000", "Px": "$5,000.25", "When": "2024-01-01T09:00:00+00:00"}]
        fills, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert errors == []
        assert fills[0].quantity == 1000
        assert fills[0].price == pytest.approx(5000.25)

    def test_naive_timestamp_is_interpreted_as_central_time_not_utc(self):
        """Regression: Tradovate/NinjaTrader fill exports (the two named
        vendors this importer targets) give naive, exchange-local
        timestamps -- virtually never UTC. Every other ingestion path in
        this codebase (`backtest/data.py`) treats a naive timestamp as CT;
        this importer used to default to UTC instead, silently shifting
        every imported fill by 5-6 hours."""
        from futures_bot.contracts import CME_TZ

        rows = [{"Sym": "MES", "Dir": "Buy", "Qty": "1", "Px": "5000", "When": "2024-01-01T09:00:00"}]
        fills, errors = ti.apply_mapping_and_validate(rows, self._mapping())
        assert errors == []
        assert fills[0].timestamp.utcoffset() == CME_TZ.utcoffset(fills[0].timestamp.replace(tzinfo=None))
        assert fills[0].timestamp.hour == 9  # the naive wall-clock hour, now correctly tagged as CT not UTC
