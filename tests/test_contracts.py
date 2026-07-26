"""Tests for `contracts`'s CME session arithmetic, focused on the holiday
calendar added on top of the pre-existing weekend/maintenance-halt logic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from futures_bot.contracts import cme_full_closures, is_cme_holiday, is_market_open


class TestCmeFullClosures:
    def test_2026_matches_the_known_public_calendar(self):
        # Cross-checked against the published 2026 US federal/market holiday
        # calendar -- these are the eight dates this module claims to model.
        assert cme_full_closures(2026) == {
            date(2026, 1, 1),    # New Year's Day (Thursday)
            date(2026, 4, 3),    # Good Friday
            date(2026, 5, 25),   # Memorial Day
            date(2026, 6, 19),   # Juneteenth (Friday)
            date(2026, 7, 3),    # Independence Day, observed (July 4 is a Saturday)
            date(2026, 9, 7),    # Labor Day
            date(2026, 11, 26),  # Thanksgiving
            date(2026, 12, 25),  # Christmas Day (Friday)
        }

    def test_a_fixed_holiday_landing_on_sunday_is_observed_the_following_monday(self):
        # 2027-01-01 (New Year's Day) is a Friday, not a useful example --
        # 2028-01-01 is a Saturday and 2033-01-01 is a Saturday too. Use
        # Juneteenth 2033-06-19, a Sunday, observed Monday 2033-06-20.
        assert date(2033, 6, 19).weekday() == 6  # Sunday, sanity-check the example itself
        assert date(2033, 6, 20) in cme_full_closures(2033)
        assert date(2033, 6, 19) not in cme_full_closures(2033)

    def test_is_deterministic_and_varies_by_year(self):
        assert cme_full_closures(2030) == cme_full_closures(2030)
        assert cme_full_closures(2030) != cme_full_closures(2031)


class TestIsCmeHoliday:
    def test_true_during_the_thanksgiving_session(self):
        # The session attributed to Thanksgiving 2026-11-26 opens at 17:00 CT
        # the evening before -- both endpoints should read as the holiday.
        session_start = datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc)  # Wed 17:00 CT
        mid_session = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)    # Thu noon CT
        assert is_cme_holiday(session_start)
        assert is_cme_holiday(mid_session)

    def test_false_the_session_before_and_after(self):
        session_before = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)  # Wed noon CT
        session_after = datetime(2026, 11, 26, 23, 0, tzinfo=timezone.utc)   # Thu 17:00 CT -> Fri's session
        assert not is_cme_holiday(session_before)
        assert not is_cme_holiday(session_after)


class TestIsMarketOpenWithHolidays:
    def test_closed_on_a_midweek_holiday(self):
        thanksgiving_noon_ct = datetime(2026, 11, 26, 18, 0, tzinfo=timezone.utc)
        assert not is_market_open(thanksgiving_noon_ct)

    def test_open_the_ordinary_wednesday_before(self):
        wednesday_noon_ct = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)
        assert is_market_open(wednesday_noon_ct)
