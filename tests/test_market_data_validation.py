"""
Tests for the permanent database validator (market_data/validation.py),
built to catch a recurrence of the turtle-data corruption in
docs/DATABASE_CORRUPTION_REPORT.md plus a broader set of integrity
classes the schema itself doesn't enforce. See docs/DATABASE_VALIDATION.md.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from futures_bot.market_data import validation as v
from futures_bot.market_data.store import MarketDataStore
from futures_bot.models import Bar


def _bar(day: str, o="1", h="1", lo="1", c="1", volume=1) -> Bar:
    return Bar(
        timestamp=datetime.fromisoformat(f"{day}T00:00:00+00:00").astimezone(timezone.utc),
        open=Decimal(o) if isinstance(o, str) and o.replace(".", "").replace("-", "").isdigit() else o,
        high=Decimal(h) if isinstance(h, str) and h.replace(".", "").replace("-", "").isdigit() else h,
        low=Decimal(lo) if isinstance(lo, str) and lo.replace(".", "").replace("-", "").isdigit() else lo,
        close=Decimal(c) if isinstance(c, str) and c.replace(".", "").replace("-", "").isdigit() else c,
        volume=volume,
    )


def _finding(report: v.ValidationReport, check: str) -> v.Finding:
    matches = [f for f in report.findings if f.check == check]
    assert matches, f"no finding for check {check!r}; have {[f.check for f in report.findings]}"
    return matches[0]


class TestIsValidSymbol:
    def test_live_continuous_marker_is_valid(self):
        assert v.is_valid_symbol("MES", "CONTINUOUS")

    def test_live_specific_ticker_is_valid(self):
        assert v.is_valid_symbol("MES", "MESH6")

    def test_live_continuous_product_code_is_valid(self):
        assert v.is_valid_symbol("MES_CONTINUOUS", "CONTINUOUS")

    def test_historical_ticker_as_both_fields_is_valid(self):
        assert v.is_valid_symbol("CL00F", "CL00F")

    def test_historical_ticker_as_product_code_with_continuous_contract_is_invalid(self):
        # The exact shape of the bug fixed 2026-07-26.
        assert not v.is_valid_symbol("CL00F", "CONTINUOUS")

    def test_date_shaped_value_is_invalid(self):
        assert not v.is_valid_symbol("20260115", "20260115")

    def test_empty_values_are_invalid(self):
        assert not v.is_valid_symbol("", "")
        assert not v.is_valid_symbol("MES", "")


class TestValidateDatabaseCleanCase:
    def test_freshly_created_store_passes_every_check(self, tmp_path):
        store = MarketDataStore(tmp_path / "clean.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", "1", "2", "0.5", "1.5", 10)],
        )
        store.close()

        report = v.validate_database(tmp_path / "clean.db")
        assert report.passed
        assert report.failures == []


class TestCorruptedContractSymbols:
    def test_flags_a_date_shaped_product_code(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="20260115", contract="20260115", resolution="1day", source="turtletrader",
            bars=[_bar("2020-01-01")],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "corrupted_contract_symbols")
        assert finding.severity == "FAIL"
        assert finding.count == 1

    def test_flags_the_exact_2026_07_26_bug_shape(self, tmp_path):
        # product_code correct (a real historical ticker), contract wrongly
        # hardcoded to "CONTINUOUS" -- exactly the pre-fix import_turtle_data.py.
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="CL00F", contract="CONTINUOUS", resolution="1day", source="turtletrader",
            bars=[_bar("2000-01-01")],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "corrupted_contract_symbols")
        assert finding.severity == "FAIL"


class TestOhlcRelationships:
    @pytest.mark.parametrize(
        "check,o,h,lo,c",
        [
            ("high_lt_open", "10", "9", "8", "8.5"),
            ("high_lt_close", "8", "9", "7", "9.5"),
            ("low_gt_open", "10", "12", "11", "10.5"),
            ("low_gt_close", "10", "12", "10.5", "9"),
        ],
    )
    def test_flags_the_violation(self, tmp_path, check, o, h, lo, c):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", o, h, lo, c)],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, check)
        assert finding.severity == "FAIL"
        assert finding.count == 1


class TestVolume:
    def test_negative_volume_fails(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", volume=-5)],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        assert _finding(report, "negative_volume").severity == "FAIL"

    def test_zero_volume_warns_not_fails(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", volume=0)],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "zero_volume")
        assert finding.severity == "WARN"
        assert report.passed  # a WARN alone must not fail the report


class TestOhlcNumericAndPresence:
    def test_non_numeric_value_is_flagged(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", o="not-a-number")],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "invalid_ohlc_values:open_non_numeric")
        assert finding.severity == "FAIL"
        assert finding.count == 1


class TestMissingAndMalformedTimestamps:
    def test_missing_timestamp_is_flagged(self, tmp_path):
        # The schema's own NOT NULL rejects a literal NULL outright (good
        # -- defense in depth), so an empty string is what this checks:
        # a schema without that constraint (like the live db's drifted
        # one, see TestSchemaMismatch) wouldn't reject NULL at all.
        store = MarketDataStore(tmp_path / "bad.db")
        store.close()
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.execute(
            "INSERT INTO bars (product_code, contract, resolution, timestamp, open, high, low, close, volume, source) "
            "VALUES ('MES','MESH6','5min', '', '1','1','1','1', 1, 'massive')"
        )
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        assert _finding(report, "missing_timestamps").severity == "FAIL"

    def test_malformed_timestamp_shape_is_flagged(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.close()
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.execute(
            "INSERT INTO bars (product_code, contract, resolution, timestamp, open, high, low, close, volume, source) "
            "VALUES ('MES','MESH6','5min','not-a-timestamp','1','1','1','1', 1, 'massive')"
        )
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        assert _finding(report, "timestamp_ordering_errors:format").severity == "FAIL"

    def test_implausible_year_is_flagged(self, tmp_path):
        # The exact shape of the century-pivot bug fixed 2026-07-26: a
        # plausible-looking but impossible (100-years-in-the-future) date.
        store = MarketDataStore(tmp_path / "bad.db")
        store.close()
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.execute(
            "INSERT INTO bars (product_code, contract, resolution, timestamp, open, high, low, close, volume, source) "
            "VALUES ('HG64H','HG64H','1day','2064-03-25T00:00:00+00:00','1','1','1','1', 1, 'turtletrader')"
        )
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        assert _finding(report, "timestamp_ordering_errors:implausible_year").severity == "FAIL"


class TestDuplicateRows:
    def test_duplicate_identity_bypassing_the_unique_index_is_flagged(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.close()
        # Direct SQL insert bypasses upsert_bars' INSERT OR IGNORE, so this
        # simulates the unique index having been dropped/bypassed somehow.
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.execute("DROP INDEX idx_bars_identity")
        for _ in range(2):
            conn.execute(
                "INSERT INTO bars (product_code, contract, resolution, timestamp, open, high, low, close, volume, source) "
                "VALUES ('MES','MESH6','5min','2026-01-05T00:00:00+00:00','1','1','1','1', 1, 'massive')"
            )
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        assert _finding(report, "duplicate_rows").severity == "FAIL"


class TestSchemaMismatch:
    def test_old_style_table_missing_not_null_is_flagged(self, tmp_path):
        # Simulates the real drift found in the live market_data.db: a
        # table created before store.py's _SCHEMA added NOT NULL/PK.
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.executescript(
            """
            CREATE TABLE bars (
                id INT,
                product_code TEXT,
                contract TEXT,
                resolution TEXT,
                timestamp TEXT,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                volume INT,
                source TEXT,
                created_at TEXT
            );
            CREATE UNIQUE INDEX idx_bars_identity ON bars(product_code, resolution, timestamp);
            CREATE TABLE sync_runs (id TEXT PRIMARY KEY, product_code TEXT, resolution TEXT, kind TEXT,
                status TEXT, requested_start TEXT, requested_end TEXT, bars_fetched INTEGER,
                last_committed_through TEXT, error_message TEXT, started_at TEXT, completed_at TEXT);
            CREATE TABLE gaps (id INTEGER PRIMARY KEY, product_code TEXT, resolution TEXT,
                gap_start TEXT, gap_end TEXT, detected_at TEXT, resolved_at TEXT);
            CREATE TABLE active_contracts (product_code TEXT PRIMARY KEY, contract TEXT, updated_at TEXT);
            CREATE TABLE contract_rolls (id INTEGER PRIMARY KEY, product_code TEXT, from_contract TEXT,
                to_contract TEXT, rolled_at TEXT);
            """
        )
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "schema_mismatch:bars")
        assert finding.severity == "FAIL"

    def test_missing_table_is_flagged(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.execute("CREATE TABLE bars (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "schema_mismatch:tables")
        assert finding.severity == "FAIL"
        assert "gaps" in finding.detail


class TestOverlappingContracts:
    def test_chain_break_is_flagged(self, tmp_path):
        # A legitimate first roll (None -> H6), then a second roll whose
        # from_contract doesn't match what was actually active (U6
        # instead of H6) -- the concrete, checkable shape of "two
        # contracts both look active at once."
        store = MarketDataStore(tmp_path / "bad.db")
        store.close()
        conn = sqlite3.connect(tmp_path / "bad.db")
        conn.executemany(
            "INSERT INTO contract_rolls (product_code, from_contract, to_contract, rolled_at) VALUES (?, ?, ?, ?)",
            [
                ("MES", None, "MESH6", "2026-01-01 00:00:00"),
                ("MES", "MESU6", "MESM6", "2026-01-02 00:00:00"),  # should be from_contract="MESH6"
                ("MES", "MESM6", "MESU6", "2026-01-03 00:00:00"),
            ],
        )
        conn.commit()
        conn.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "overlapping_contracts")
        assert finding.severity == "FAIL"
        assert finding.count >= 1

    def test_no_rolls_yet_passes(self, tmp_path):
        store = MarketDataStore(tmp_path / "clean.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05")],
        )
        store.close()

        report = v.validate_database(tmp_path / "clean.db")
        assert _finding(report, "overlapping_contracts").severity == "PASS"


class TestOrphanRecords:
    def test_gap_referencing_a_product_with_no_bars_warns(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.record_gap(
            "NOSUCHPRODUCT", "5min",
            datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "orphan_records:gaps")
        assert finding.severity == "WARN"
        assert "NOSUCHPRODUCT" in finding.samples


class TestSessionGapsReuseExistingBookkeeping:
    def test_unresolved_gap_is_reported_as_warn(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05")],
        )
        store.record_gap(
            "MES", "5min",
            datetime(2026, 1, 6, tzinfo=timezone.utc), datetime(2026, 1, 7, tzinfo=timezone.utc),
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        finding = _finding(report, "session_gaps")
        assert finding.severity == "WARN"
        assert report.passed


class TestRenderReportAndExitStatus:
    def test_clean_report_renders_passed_and_zero_exit_semantics(self, tmp_path):
        store = MarketDataStore(tmp_path / "clean.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05")],
        )
        store.close()

        report = v.validate_database(tmp_path / "clean.db")
        text = v.render_report(report)
        assert "VALIDATION PASSED" in text
        assert report.passed

    def test_failing_report_renders_failed(self, tmp_path):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", volume=-1)],
        )
        store.close()

        report = v.validate_database(tmp_path / "bad.db")
        text = v.render_report(report)
        assert "VALIDATION FAILED" in text
        assert not report.passed

    def test_main_returns_nonzero_on_failure(self, tmp_path, capsys):
        store = MarketDataStore(tmp_path / "bad.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05", volume=-1)],
        )
        store.close()

        exit_code = v.main(["--db", str(tmp_path / "bad.db")])
        assert exit_code == 1
        assert "VALIDATION FAILED" in capsys.readouterr().out

    def test_main_returns_zero_on_success(self, tmp_path, capsys):
        store = MarketDataStore(tmp_path / "clean.db")
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05")],
        )
        store.close()

        exit_code = v.main(["--db", str(tmp_path / "clean.db")])
        assert exit_code == 0
        assert "VALIDATION PASSED" in capsys.readouterr().out


class TestValidatorNeverWrites:
    def test_validate_database_does_not_modify_mtime_or_content(self, tmp_path):
        db_path = tmp_path / "readonly_check.db"
        store = MarketDataStore(db_path)
        store.upsert_bars(
            product_code="MES", contract="MESH6", resolution="5min", source="massive",
            bars=[_bar("2026-01-05")],
        )
        store.close()

        before = db_path.read_bytes()
        v.validate_database(db_path)
        after = db_path.read_bytes()
        assert before == after, "validate_database must never write to the database file"

    def test_readonly_connection_rejects_a_write_attempt(self, tmp_path):
        db_path = tmp_path / "readonly_check2.db"
        MarketDataStore(db_path).close()

        conn = v._connect_readonly(db_path)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO bars (product_code, contract, resolution, timestamp, open, high, low, close, volume, source) VALUES ('X','X','1day','2026-01-01T00:00:00+00:00','1','1','1','1',1,'x')")
        conn.close()
