"""
Regression coverage for the turtle-data corruption fixed in
docs/DATABASE_CORRUPTION_REPORT.md: a century-pivot date bug in
tools/convert_turtle_data.py and a product_code/contract swap in
tools/import_turtle_data.py. Both scripts live under tools/, which is
intentionally not part of the installable package (see CLAUDE.md's
File Ownership section) and isn't on pytest's pythonpath, so they're
loaded here directly by file path.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from futures_bot.market_data.store import MarketDataStore
from futures_bot.models import Bar

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


convert_turtle_data = _load("convert_turtle_data")
import_turtle_data = _load("import_turtle_data")


class TestParseDateCenturyPivot:
    """tools/convert_turtle_data.py's parse_date -- the century-pivot bug."""

    def test_year_in_1900s_before_the_old_pivot_boundary(self):
        # Under the old `datetime.strptime(value, "%y%m%d")` default, a
        # two-digit year of 68 or below resolved to 2068, not 1968 --
        # exactly the malformed-input shape that produced 17,668
        # corrupted Copper (HG) bars.
        dt = convert_turtle_data.parse_date("681231")
        assert dt.year == 1968
        assert (dt.month, dt.day) == (12, 31)

    def test_year_in_1900s_after_the_old_pivot_boundary(self):
        dt = convert_turtle_data.parse_date("970821")
        assert dt.year == 1997

    def test_year_2000_still_resolves_correctly(self):
        # The fix must not regress the years that already parsed right.
        dt = convert_turtle_data.parse_date("000127")
        assert dt.year == 2000
        assert (dt.month, dt.day) == (1, 27)

    def test_result_is_utc(self):
        dt = convert_turtle_data.parse_date("970821")
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0


class TestParseTicker:
    """
    tools/import_turtle_data.py's parse_ticker -- validates the
    filename stem and returns it unchanged for use as BOTH
    product_code and contract (see the function's docstring for why
    product_code must stay the full ticker, not a generic root).
    """

    @pytest.mark.parametrize("stem", ["CL00F", "GC98Z", "HG64H", "US84H"])
    def test_valid_ticker_is_returned_unchanged(self, stem):
        assert import_turtle_data.parse_ticker(stem) == stem

    @pytest.mark.parametrize(
        "bad_stem",
        [
            "20260115",  # a bare date -- the originally reported corruption shape
            "CONTINUOUS",
            "CL",
            "CL2000F",
            "",
        ],
    )
    def test_malformed_stem_is_rejected_not_silently_imported(self, bad_stem):
        with pytest.raises(ValueError):
            import_turtle_data.parse_ticker(bad_stem)


def _bar(day: str, price: str = "1.0") -> Bar:
    return Bar(
        timestamp=datetime.fromisoformat(f"{day}T00:00:00+00:00").astimezone(timezone.utc),
        open=Decimal(price), high=Decimal(price), low=Decimal(price), close=Decimal(price),
        volume=1,
    )


class TestOverlappingContractsSurviveImport:
    """
    Direct regression test for the 2026-07-26 near-miss: using a
    generic root as product_code collapses overlapping contract-month
    bars onto the same (product_code, resolution, timestamp) identity
    and silently drops all but the first. Using the full ticker (what
    parse_ticker returns) must keep every contract's bars intact even
    when two contracts trade on the same calendar day.
    """

    def test_full_ticker_as_product_code_avoids_collision(self, tmp_path):
        store = MarketDataStore(tmp_path / "test_market_data.db")

        # Two different contract-months, same trading day -- this is
        # the normal, expected shape for adjacent futures contracts.
        overlapping_day = "1998-06-15"

        for stem in ("CL98M", "CL98N"):
            ticker = import_turtle_data.parse_ticker(stem)
            store.upsert_bars(
                product_code=ticker,
                contract=ticker,
                resolution="1day",
                source="turtletrader",
                bars=[_bar(overlapping_day)],
            )

        cur = store._conn.execute(
            "SELECT product_code, contract FROM bars WHERE timestamp LIKE ? ORDER BY product_code",
            (f"{overlapping_day}%",),
        )
        rows = cur.fetchall()
        assert [r[0] for r in rows] == ["CL98M", "CL98N"], (
            "both contracts' bars for the same day must survive independently"
        )
        assert [r[1] for r in rows] == ["CL98M", "CL98N"]

    def test_generic_root_as_product_code_would_collide(self, tmp_path):
        # Demonstrates *why* the fix above matters: this is what the
        # since-reverted "generic root" approach does to the same
        # scenario -- the second contract's bar for the shared day is
        # silently dropped.
        store = MarketDataStore(tmp_path / "test_market_data.db")
        overlapping_day = "1998-06-15"

        for stem in ("CL98M", "CL98N"):
            store.upsert_bars(
                product_code="CL",  # the generic-root approach that caused the data loss
                contract=stem,
                resolution="1day",
                source="turtletrader",
                bars=[_bar(overlapping_day)],
            )

        cur = store._conn.execute(
            "SELECT COUNT(*) FROM bars WHERE product_code = 'CL' AND timestamp LIKE ?",
            (f"{overlapping_day}%",),
        )
        assert cur.fetchone()[0] == 1, (
            "generic-root product_code collides on (product_code, resolution, "
            "timestamp) -- only one of the two contracts' bars survives"
        )
