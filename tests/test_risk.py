"""Risk manager tests.

The persistence tests matter most. A kill switch that forgets across a restart
is not a kill switch, and that failure is invisible until the session it costs
you money.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ, session_date
from futures_bot.models import Position, Side, Trade
from futures_bot.risk.manager import RiskManager
from futures_bot.state import StateStore


def ct(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=CME_TZ)


def make_settings(**overrides) -> Settings:
    risk = RiskSettings(
        contracts_per_trade=1,
        stop_loss_points=Decimal("10"),   # $50 on MES
        take_profit_points=Decimal("20"),
        daily_max_loss=Decimal("120"),
        max_trades_per_session=3,
        account_size=Decimal("2500"),
    )
    base = dict(
        contract="MES",
        mode="paper",
        risk=risk,
        session=SessionSettings(start_ct="08:30", end_ct="15:00"),
        broker=BrokerSettings(),
    )
    base.update(overrides)
    return Settings(**base)


def make_trade(net: Decimal, when: datetime) -> Trade:
    """A completed trade whose net P&L is exactly ``net``."""
    return Trade(
        side=Side.LONG,
        quantity=1,
        entry_price=Decimal("7500"),
        exit_price=Decimal("7500") + (net / Decimal("5")),
        entry_time=when,
        exit_time=when,
        gross_pnl=net,
        commission=Decimal("0"),
        exit_reason="test",
    )


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state.json")


class TestKillSwitch:
    def test_allows_entry_when_flat_and_in_window(self, store):
        rm = RiskManager(make_settings(), store)
        decision = rm.can_enter(ct(2026, 7, 21, 10, 0), None)
        assert decision.allowed, decision.reason

    def test_trips_when_daily_loss_reached(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 10, 0)

        rm.record_trade(now, make_trade(Decimal("-60"), now))
        assert rm.can_enter(now, None).allowed, "one loss should not halt"

        rm.record_trade(now, make_trade(Decimal("-60"), now))
        assert rm.is_halted(now)

        decision = rm.can_enter(now, None)
        assert not decision.allowed
        assert "halted" in decision.reason.lower()

    def test_halt_survives_restart(self, tmp_path):
        """The whole point: a crash must not hand back a fresh loss allowance."""
        path = tmp_path / "state.json"
        now = ct(2026, 7, 21, 10, 0)

        rm = RiskManager(make_settings(), StateStore(path))
        rm.record_trade(now, make_trade(Decimal("-130"), now))
        assert rm.is_halted(now)

        # Simulate process death and restart against the same file.
        revived = RiskManager(make_settings(), StateStore(path))
        assert revived.is_halted(now), "halt was forgotten across restart"
        assert not revived.can_enter(now, None).allowed
        assert revived.session_pnl(now) == Decimal("-130")

    def test_new_session_clears_halt(self, store):
        rm = RiskManager(make_settings(), store)
        day1 = ct(2026, 7, 21, 10, 0)
        rm.record_trade(day1, make_trade(Decimal("-130"), day1))
        assert rm.is_halted(day1)

        day2 = ct(2026, 7, 22, 10, 0)
        assert not rm.is_halted(day2)
        assert rm.session_pnl(day2) == Decimal("0")
        assert rm.can_enter(day2, None).allowed

    def test_evening_session_belongs_to_next_trade_date(self, store):
        """18:00 CT Monday is Tuesday's session, so limits must not reset at midnight."""
        rm = RiskManager(make_settings(), store)
        monday_evening = ct(2026, 7, 20, 18, 0)
        tuesday_morning = ct(2026, 7, 21, 10, 0)

        assert session_date(monday_evening) == session_date(tuesday_morning)

        rm.record_trade(monday_evening, make_trade(Decimal("-130"), monday_evening))
        assert rm.is_halted(tuesday_morning), "evening loss should carry into the same session"

    def test_blocks_entry_that_would_overshoot_limit(self, store):
        """A stop-out that breaches the limit makes the limit unenforceable."""
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 10, 0)

        # $80 down, $120 limit, $50 risk per trade -> would reach -$130.
        rm.record_trade(now, make_trade(Decimal("-80"), now))
        decision = rm.can_enter(now, None)
        assert not decision.allowed
        assert "past the" in decision.reason


class TestSessionFilters:
    def test_rejects_when_market_closed(self, store):
        rm = RiskManager(make_settings(), store)
        saturday = ct(2026, 7, 25, 12, 0)
        decision = rm.can_enter(saturday, None)
        assert not decision.allowed
        assert "closed" in decision.reason.lower()

    def test_rejects_outside_trading_window(self, store):
        rm = RiskManager(make_settings(), store)
        too_early = ct(2026, 7, 21, 7, 0)  # before 08:30 CT
        decision = rm.can_enter(too_early, None)
        assert not decision.allowed
        assert "window" in decision.reason.lower()

    def test_rejects_when_already_in_position(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 10, 0)
        pos = Position(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"), entry_time=now
        )
        assert not rm.can_enter(now, pos).allowed

    def test_enforces_trade_cap(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 10, 0)
        for _ in range(3):
            rm.record_trade(now, make_trade(Decimal("5"), now))
        decision = rm.can_enter(now, None)
        assert not decision.allowed
        assert "cap" in decision.reason.lower()


class TestForceFlat:
    def test_flattens_before_close(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 15, 50)  # 16:00 close, 15-minute buffer
        pos = Position(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"), entry_time=now
        )
        decision = rm.must_flatten(now, pos)
        assert decision.allowed
        assert "force-flat" in decision.reason.lower()

    def test_no_forced_exit_midsession(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 11, 0)
        pos = Position(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"), entry_time=now
        )
        assert not rm.must_flatten(now, pos).allowed

    def test_blocks_entry_near_deadline(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 15, 50)
        decision = rm.can_enter(now, None)
        assert not decision.allowed

    def test_halt_forces_exit(self, store):
        rm = RiskManager(make_settings(), store)
        now = ct(2026, 7, 21, 10, 0)
        rm.record_trade(now, make_trade(Decimal("-130"), now))
        pos = Position(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"), entry_time=now
        )
        assert rm.must_flatten(now, pos).allowed


class TestStateDurability:
    def test_corrupt_state_file_refuses_to_start(self, tmp_path):
        """Silently starting fresh would silently discard an active halt."""
        path = tmp_path / "state.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(RuntimeError, match="could not be read"):
            StateStore(path)

    def test_history_retained_across_sessions(self, store):
        rm = RiskManager(make_settings(), store)
        rm.record_trade(ct(2026, 7, 21, 10, 0), make_trade(Decimal("-20"), ct(2026, 7, 21, 10, 0)))
        rm.record_trade(ct(2026, 7, 22, 10, 0), make_trade(Decimal("15"), ct(2026, 7, 22, 10, 0)))
        assert len(store.state.history) == 1
        assert store.state.current.session_date == "2026-07-22"
