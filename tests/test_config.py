"""Tests for `config.Settings`'s cross-field validators. Currently just the
one added for the delayed WebSocket feed (`live_feed`) -- see
`feeds/massive_websocket.py`'s module docstring for why that feed is
minute-resolution only, confirmed against the real endpoint."""

from __future__ import annotations

from decimal import Decimal

import pytest

from futures_bot.config import RiskSettings, Settings


def _risk() -> RiskSettings:
    return RiskSettings(
        contracts_per_trade=1,
        stop_loss_points=Decimal("5"),
        take_profit_points=Decimal("10"),
        daily_max_loss=Decimal("2000"),
        max_trades_per_session=500,
        account_size=Decimal("5000"),
    )


class TestSessionRuleFields:
    """New, optional RiskSettings fields for the daily-session simulation
    layer -- all default to "disabled" so existing config files are
    completely unaffected."""

    def test_new_fields_default_to_disabled(self):
        r = _risk()
        assert r.profit_target is None
        assert r.max_consecutive_losses is None
        assert r.cooldown_minutes_after_loss == 0

    def test_profit_target_must_be_positive(self):
        with pytest.raises(ValueError):
            RiskSettings(
                contracts_per_trade=1, stop_loss_points=Decimal("5"), take_profit_points=Decimal("10"),
                daily_max_loss=Decimal("2000"), account_size=Decimal("5000"), profit_target=Decimal("0"),
            )

    def test_max_consecutive_losses_must_be_at_least_one(self):
        with pytest.raises(ValueError):
            RiskSettings(
                contracts_per_trade=1, stop_loss_points=Decimal("5"), take_profit_points=Decimal("10"),
                daily_max_loss=Decimal("2000"), account_size=Decimal("5000"), max_consecutive_losses=0,
            )

    def test_all_session_rules_can_be_set_together(self):
        r = RiskSettings(
            contracts_per_trade=1, stop_loss_points=Decimal("5"), take_profit_points=Decimal("10"),
            daily_max_loss=Decimal("300"), account_size=Decimal("50000"),
            profit_target=Decimal("500"), max_consecutive_losses=3, cooldown_minutes_after_loss=15,
        )
        assert r.profit_target == Decimal("500")
        assert r.max_consecutive_losses == 3
        assert r.cooldown_minutes_after_loss == 15


class TestLiveFeedValidation:
    def test_defaults_to_rest(self):
        settings = Settings(contract="MES", risk=_risk())
        assert settings.live_feed == "rest"

    def test_websocket_with_non_1min_research_server_resolution_raises(self):
        with pytest.raises(ValueError, match="live_feed is 'websocket'"):
            Settings(
                contract="MES", risk=_risk(), live_feed="websocket",
                research_server={"resolution": "5min"},
            )

    def test_websocket_with_1min_research_server_resolution_is_fine(self):
        settings = Settings(
            contract="MES", risk=_risk(), live_feed="websocket",
            research_server={"resolution": "1min"},
        )
        assert settings.live_feed == "websocket"

    def test_rest_with_any_resolution_is_unaffected(self):
        # The websocket-only guard must never fire for the default feed --
        # every existing config using research_server.resolution values
        # like "5min" must keep working exactly as before.
        settings = Settings(contract="MES", risk=_risk(), research_server={"resolution": "5min"})
        assert settings.live_feed == "rest"
