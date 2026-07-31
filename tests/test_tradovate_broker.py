"""Tests for `brokers.tradovate.TradovateBroker` against a fully mocked HTTP
layer -- see that module's docstring for why this is the only testing this
adapter has had (no live/demo Tradovate account is reachable from this
environment). These tests verify the adapter builds the requests it intends
to and parses the responses it expects; they cannot verify those match what
Tradovate's servers actually return.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from futures_bot.brokers.base import BrokerError
from futures_bot.brokers.tradovate import TradovateBroker, TradovateCredentials
from futures_bot.contracts import MES
from futures_bot.models import Side


def make_credentials(**overrides) -> TradovateCredentials:
    base = dict(
        username="trader", password="hunter2", app_id="app", app_version="1.0",
        client_id="cid", client_secret="sec", device_id="futures-bot",
        account_id="12345", base_url="https://demo.tradovateapi.com/v1", environment="demo",
    )
    base.update(overrides)
    return TradovateCredentials(**base)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeSession:
    """Records every call and serves canned responses keyed by (method, path).

    ``responses`` maps ``"METHOD /path"`` to either a static payload or a
    callable ``(json, params) -> payload`` for handlers that need to react
    to the request body (e.g. echoing back an order id).
    """

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[dict] = []

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        path = url.split("/v1", 1)[1]
        key = f"{method} {path}"
        self.calls.append({"method": method, "path": path, "json": json, "params": params, "headers": headers})
        if key not in self.responses:
            raise AssertionError(f"FakeSession has no canned response for {key!r}. Calls so far: {self.calls}")
        handler = self.responses[key]
        result = handler(json, params) if callable(handler) else handler
        return result if isinstance(result, FakeResponse) else FakeResponse(result)


def connected_broker(responses: dict, **broker_kwargs) -> TradovateBroker:
    session = FakeSession({
        "POST /auth/accesstokenrequest": {"accessToken": "tok-123", "expirationTime": "2099-01-01"},
        **responses,
    })
    broker_kwargs.setdefault("fill_poll_timeout", 0.05)
    broker_kwargs.setdefault("fill_poll_interval", 0.01)
    broker = TradovateBroker(
        contract=MES, symbol="MESZ5", credentials=make_credentials(), session=session, **broker_kwargs,
    )
    broker.connect()
    return broker


class TestConnection:
    def test_connect_sets_access_token_and_is_idempotent(self):
        session = FakeSession({"POST /auth/accesstokenrequest": {"accessToken": "tok-abc"}})
        broker = TradovateBroker(contract=MES, symbol="MESZ5", credentials=make_credentials(), session=session)
        assert not broker.is_connected
        broker.connect()
        assert broker.is_connected
        broker.connect()  # second call must not re-authenticate
        auth_calls = [c for c in session.calls if c["path"] == "/auth/accesstokenrequest"]
        assert len(auth_calls) == 1

    def test_connect_raises_without_access_token_in_response(self):
        session = FakeSession({"POST /auth/accesstokenrequest": {"errorText": "bad credentials"}})
        broker = TradovateBroker(contract=MES, symbol="MESZ5", credentials=make_credentials(), session=session)
        with pytest.raises(BrokerError):
            broker.connect()

    def test_disconnect_clears_token(self):
        broker = connected_broker({})
        broker.disconnect()
        assert not broker.is_connected

    def test_uninitialized_credentials_read_from_env(self, monkeypatch):
        monkeypatch.setenv("TRADOVATE_USERNAME", "u")
        monkeypatch.setenv("TRADOVATE_PASSWORD", "p")
        monkeypatch.setenv("TRADOVATE_APP_ID", "a")
        monkeypatch.setenv("TRADOVATE_CLIENT_ID", "c")
        monkeypatch.setenv("TRADOVATE_CLIENT_SECRET", "s")
        monkeypatch.setenv("TRADOVATE_ACCOUNT_ID", "999")
        monkeypatch.delenv("TRADOVATE_ENV", raising=False)
        broker = TradovateBroker(contract=MES, symbol="MESZ5", session=FakeSession({}))
        creds = broker.credentials
        assert creds.username == "u"
        assert creds.base_url == "https://demo.tradovateapi.com/v1"

    def test_missing_env_var_raises_broker_error(self, monkeypatch):
        monkeypatch.delenv("TRADOVATE_USERNAME", raising=False)
        broker = TradovateBroker(contract=MES, symbol="MESZ5", session=FakeSession({}))
        with pytest.raises(BrokerError, match="TRADOVATE_USERNAME"):
            broker.connect()

    def test_requires_connection_before_calls(self):
        broker = TradovateBroker(contract=MES, symbol="MESZ5", credentials=make_credentials(), session=FakeSession({}))
        with pytest.raises(BrokerError, match="not connected"):
            broker.get_position()


class TestGetPosition:
    def test_returns_none_when_flat(self):
        broker = connected_broker({"GET /position/list": []})
        assert broker.get_position() is None

    def test_returns_position_from_net_position(self):
        broker = connected_broker({
            "GET /position/list": [{"accountId": 12345, "netPos": 1, "netPrice": 7500.25}],
        })
        pos = broker.get_position()
        assert pos is not None
        assert pos.side is Side.LONG
        assert pos.quantity == 1
        assert pos.entry_price == Decimal("7500.25")

    def test_short_position(self):
        broker = connected_broker({
            "GET /position/list": [{"accountId": 12345, "netPos": -2, "netPrice": 7500}],
        })
        pos = broker.get_position()
        assert pos.side is Side.SHORT
        assert pos.quantity == 2

    def test_ignores_other_accounts(self):
        broker = connected_broker({
            "GET /position/list": [{"accountId": 99999, "netPos": 1, "netPrice": 7500}],
        })
        assert broker.get_position() is None

    def test_prefers_broker_netprice_over_local_tracking(self):
        """get_position() must trust the broker's own reported price over
        whatever this process guessed when it placed the order -- see
        TradovateBroker.get_position's docstring."""
        broker = connected_broker({
            "GET /position/list": [{"accountId": 12345, "netPos": 1, "netPrice": 7510.5}],
        })
        broker._entry = _fake_entry(entry_price=Decimal("7500"))
        pos = broker.get_position()
        assert pos.entry_price == Decimal("7510.5")


def _fake_entry(**overrides):
    from futures_bot.brokers.tradovate import _TrackedEntry
    base = dict(
        side=Side.LONG, quantity=1, entry_price=Decimal("7500"),
        entry_time=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
        stop_loss=Decimal("7490"), take_profit=Decimal("7520"),
        parent_order_id="1", stop_order_id="2", target_order_id="3",
    )
    base.update(overrides)
    return _TrackedEntry(**base)


class TestSubmitBracket:
    def test_places_oso_with_stop_and_target(self):
        broker = connected_broker({
            "POST /order/placeOSO": lambda json, params: {"orderId": 555},
            "GET /fill/list": [],
            "GET /order/list": [],
        })
        order = broker.submit_bracket(
            Side.LONG, 1, Decimal("7490"), Decimal("7520"), datetime.now(timezone.utc),
        )
        assert order.order_id == "555"

        oso_call = next(c for c in broker.session.calls if c["path"] == "/order/placeOSO")
        body = oso_call["json"]
        assert body["action"] == "Buy"
        assert body["symbol"] == "MESZ5"
        assert body["bracket1"]["orderType"] == "Stop"
        assert body["bracket1"]["stopPrice"] == 7490.0
        assert body["bracket1"]["action"] == "Sell"
        assert body["bracket2"]["orderType"] == "Limit"
        assert body["bracket2"]["price"] == 7520.0

    def test_short_bracket_uses_buy_for_children(self):
        broker = connected_broker({
            "POST /order/placeOSO": lambda json, params: {"orderId": 556},
            "GET /fill/list": [],
            "GET /order/list": [],
        })
        broker.submit_bracket(Side.SHORT, 1, Decimal("7520"), Decimal("7490"), datetime.now(timezone.utc))
        oso_call = next(c for c in broker.session.calls if c["path"] == "/order/placeOSO")
        assert oso_call["json"]["action"] == "Sell"
        assert oso_call["json"]["bracket1"]["action"] == "Buy"

    def test_uses_confirmed_fill_price_when_available(self):
        broker = connected_broker({
            "POST /order/placeOSO": lambda json, params: {"orderId": 555},
            "GET /fill/list": [
                {"orderId": "555", "action": "Buy", "qty": 1, "price": 7501.25,
                 "timestamp": "2026-01-05T14:30:00.000Z"},
            ],
            "GET /order/list": [],
        })
        order = broker.submit_bracket(Side.LONG, 1, Decimal("7490"), Decimal("7520"), datetime.now(timezone.utc))
        assert order.status.value == "filled"
        assert broker._entry.entry_price == Decimal("7501.25")

    def test_rejects_a_second_entry_while_one_is_tracked(self):
        broker = connected_broker({
            "POST /order/placeOSO": lambda json, params: {"orderId": 555},
            "GET /fill/list": [],
            "GET /order/list": [],
        })
        broker.submit_bracket(Side.LONG, 1, Decimal("7490"), Decimal("7520"), datetime.now(timezone.utc))
        with pytest.raises(BrokerError, match="Already tracking"):
            broker.submit_bracket(Side.LONG, 1, Decimal("7490"), Decimal("7520"), datetime.now(timezone.utc))

    def test_raises_when_broker_does_not_return_an_order_id(self):
        broker = connected_broker({"POST /order/placeOSO": {"errorText": "insufficient margin"}})
        with pytest.raises(BrokerError):
            broker.submit_bracket(Side.LONG, 1, Decimal("7490"), Decimal("7520"), datetime.now(timezone.utc))

    def test_finds_bracket_children_by_oco_id(self):
        broker = connected_broker({
            "POST /order/placeOSO": lambda json, params: {"orderId": 555},
            "GET /fill/list": [],
            "GET /order/list": [
                {"id": 556, "ocoId": "555", "orderType": "Stop"},
                {"id": 557, "ocoId": "555", "orderType": "Limit"},
            ],
        })
        broker.submit_bracket(Side.LONG, 1, Decimal("7490"), Decimal("7520"), datetime.now(timezone.utc))
        assert broker._entry.stop_order_id == "556"
        assert broker._entry.target_order_id == "557"


class TestModifyStopLoss:
    def test_moves_stop_and_returns_true(self):
        broker = connected_broker({"POST /order/modifyOrder": lambda j, p: {"orderId": 2}})
        broker._entry = _fake_entry(side=Side.LONG, stop_loss=Decimal("7490"))
        moved = broker.modify_stop_loss(Decimal("7495"))
        assert moved is True
        assert broker._entry.stop_loss == Decimal("7495")

    def test_same_tick_is_a_noop(self):
        broker = connected_broker({})
        broker._entry = _fake_entry(side=Side.LONG, stop_loss=Decimal("7490"))
        assert broker.modify_stop_loss(Decimal("7490")) is False

    def test_refuses_to_loosen_a_long_stop(self):
        broker = connected_broker({})
        broker._entry = _fake_entry(side=Side.LONG, stop_loss=Decimal("7490"))
        with pytest.raises(BrokerError, match="loosens it"):
            broker.modify_stop_loss(Decimal("7480"))

    def test_refuses_to_loosen_a_short_stop(self):
        broker = connected_broker({})
        broker._entry = _fake_entry(side=Side.SHORT, stop_loss=Decimal("7510"))
        with pytest.raises(BrokerError, match="loosens it"):
            broker.modify_stop_loss(Decimal("7520"))

    def test_raises_without_an_open_position(self):
        broker = connected_broker({})
        with pytest.raises(BrokerError, match="No open position"):
            broker.modify_stop_loss(Decimal("7495"))

    def test_raises_without_a_known_stop_order_id(self):
        broker = connected_broker({})
        broker._entry = _fake_entry(stop_order_id=None)
        with pytest.raises(BrokerError, match="does not have the resting stop"):
            broker.modify_stop_loss(Decimal("7495"))


class TestFlattenAndCancel:
    def test_flatten_liquidates_and_cancels(self):
        broker = connected_broker({
            "POST /order/liquidatePosition": lambda j, p: {"status": "ok"},
            "GET /order/list": [{"id": 9, "ordStatus": "Working"}],
            "POST /order/cancelOrder": lambda j, p: {"status": "ok"},
            "GET /fill/list": [
                {"orderId": "2", "action": "Sell", "qty": 1, "price": 7495,
                 "timestamp": "2026-01-05T15:00:00.000Z"},
            ],
        })
        broker._entry = _fake_entry(side=Side.LONG)
        fill = broker.flatten(datetime.now(timezone.utc), "test exit")
        assert fill is not None
        assert fill.price == Decimal("7495")
        liquidate_call = next(c for c in broker.session.calls if c["path"] == "/order/liquidatePosition")
        assert liquidate_call["json"]["symbol"] == "MESZ5"

    def test_flatten_with_no_tracked_entry_is_a_noop(self):
        broker = connected_broker({})
        assert broker.flatten(datetime.now(timezone.utc), "nothing to flatten") is None

    def test_cancel_all_only_cancels_working_orders(self):
        broker = connected_broker({
            "GET /order/list": [
                {"id": 1, "ordStatus": "Working"},
                {"id": 2, "ordStatus": "Filled"},
                {"id": 3, "ordStatus": "Working"},
            ],
            "POST /order/cancelOrder": lambda j, p: {"status": "ok"},
        })
        broker.cancel_all()
        cancel_calls = [c for c in broker.session.calls if c["path"] == "/order/cancelOrder"]
        assert {c["json"]["orderId"] for c in cancel_calls} == {1, 3}


class TestPollClosedTrade:
    def test_returns_none_when_nothing_tracked(self):
        broker = connected_broker({})
        assert broker.poll_closed_trade(datetime.now(timezone.utc)) is None

    def test_returns_none_while_position_still_open(self):
        broker = connected_broker({"GET /position/list": [{"accountId": 12345, "netPos": 1, "netPrice": 7500}]})
        broker._entry = _fake_entry()
        assert broker.poll_closed_trade(datetime.now(timezone.utc)) is None

    def test_builds_trade_when_stop_fills(self):
        entry = _fake_entry(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"),
            entry_time=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            stop_loss=Decimal("7490"), stop_order_id="2", target_order_id="3",
        )
        broker = connected_broker({
            "GET /position/list": [],
            "GET /fill/list": [
                {"orderId": "2", "action": "Sell", "qty": 1, "price": 7490,
                 "timestamp": "2026-01-05T15:00:00.000Z"},
            ],
        })
        broker._entry = entry
        trade = broker.poll_closed_trade(datetime(2026, 1, 5, 15, 5, tzinfo=timezone.utc))
        assert trade is not None
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == Decimal("7490")
        assert trade.side is Side.LONG
        assert broker._entry is None  # cleared once reconciled

    def test_stop_fill_at_breakeven_is_classified_as_breakeven_stop(self):
        entry = _fake_entry(
            side=Side.LONG, quantity=1, entry_price=Decimal("7500"),
            entry_time=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            stop_loss=Decimal("7500"), stop_order_id="2", target_order_id="3",
        )
        broker = connected_broker({
            "GET /position/list": [],
            "GET /fill/list": [
                {"orderId": "2", "action": "Sell", "qty": 1, "price": 7500,
                 "timestamp": "2026-01-05T15:00:00.000Z"},
            ],
        })
        broker._entry = entry
        trade = broker.poll_closed_trade(datetime(2026, 1, 5, 15, 5, tzinfo=timezone.utc))
        assert trade.exit_reason == "breakeven_stop"

    def test_short_stop_fill_below_entry_is_classified_as_trailing_stop(self):
        entry = _fake_entry(
            side=Side.SHORT, quantity=1, entry_price=Decimal("7500"),
            entry_time=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            stop_loss=Decimal("7480"), stop_order_id="2", target_order_id="3",
        )
        broker = connected_broker({
            "GET /position/list": [],
            "GET /fill/list": [
                {"orderId": "2", "action": "Buy", "qty": 1, "price": 7480,
                 "timestamp": "2026-01-05T15:00:00.000Z"},
            ],
        })
        broker._entry = entry
        trade = broker.poll_closed_trade(datetime(2026, 1, 5, 15, 5, tzinfo=timezone.utc))
        assert trade.exit_reason == "trailing_stop"
        assert trade.net_pnl > 0

    def test_builds_trade_when_target_fills(self):
        entry = _fake_entry(
            side=Side.SHORT, quantity=1, entry_price=Decimal("7500"),
            entry_time=datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc),
            take_profit=Decimal("7480"), stop_order_id="2", target_order_id="3",
        )
        broker = connected_broker({
            "GET /position/list": [],
            "GET /fill/list": [
                {"orderId": "3", "action": "Buy", "qty": 1, "price": 7480,
                 "timestamp": "2026-01-05T15:00:00.000Z"},
            ],
        })
        broker._entry = entry
        trade = broker.poll_closed_trade(datetime(2026, 1, 5, 15, 5, tzinfo=timezone.utc))
        assert trade.exit_reason == "take_profit"

    def test_does_not_fabricate_a_trade_when_fill_not_yet_visible(self):
        """Flat at the broker but the closing fill hasn't shown up in
        fill/list yet -- must retry later, not guess a price."""
        broker = connected_broker({"GET /position/list": [], "GET /fill/list": []})
        broker._entry = _fake_entry()

        trade = broker.poll_closed_trade(datetime.now(timezone.utc))

        assert trade is None
        assert broker._entry is not None  # not cleared -- will retry


class TestErrorHandling:
    def test_http_error_status_raises_broker_error(self):
        broker = connected_broker({
            "GET /position/list": FakeResponse({"errorText": "internal error"}, status_code=500),
        })
        with pytest.raises(BrokerError):
            broker.get_position()

    def test_error_text_in_a_200_response_still_raises(self):
        broker = connected_broker({"GET /position/list": {"errorText": "account not found"}})
        with pytest.raises(BrokerError, match="account not found"):
            broker.get_position()
