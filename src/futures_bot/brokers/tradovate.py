"""Tradovate broker adapter.

Connects :class:`~futures_bot.brokers.base.Broker` to Tradovate's REST API
(``demo.tradovateapi.com`` / ``live.tradovateapi.com``). REST-only,
deliberately: Tradovate also offers a WebSocket feed for lower-latency order
and fill updates, but its custom SockJS-style frame protocol is materially
harder to get right, and nothing in the `Broker` interface *requires*
push updates -- `TradingEngine` already polls `get_position()` /
`poll_closed_trade()` once per bar on its own schedule. REST polling is the
simpler, more auditable choice for a first adapter; a WebSocket layer can be
added later purely as a latency optimization without changing this
interface.

READ THIS BEFORE POINTING IT AT ANY ACCOUNT, INCLUDING DEMO
=============================================================
This adapter was written against Tradovate's public REST API documentation.
It has **not** been exercised against a live or demo Tradovate account --
this development environment has no Tradovate credentials and no network
path to their API. That means: request/response field names below are
believed correct but unverified, and the only testing this has had is
`tests/test_tradovate_broker.py`'s mocked-HTTP suite, which checks that this
adapter builds the requests it *intends* to and parses the responses it
*expects* -- not that those match what Tradovate's servers actually return.

Before trusting this with a single dollar:

1. Set the environment variables below with **demo** credentials
   (``TRADOVATE_ENV=demo``, the default).
2. Run one `--demo`-style cycle by hand: `connect()`, `submit_bracket()` for
   one contract with a wide stop/target, confirm in Tradovate's own UI that
   the bracket looks exactly as expected, `modify_stop_loss()` once, confirm
   the resting stop actually moved, then `flatten()` and confirm flat.
3. Only after that manual walkthrough matches expectations on demo should
   this run unattended, and only after running unattended on demo for a
   meaningful stretch should `TRADOVATE_ENV=live` ever be set.

Credentials are read from the environment, never from `config.yaml` --
nothing here reads a secret from a file that could end up committed:

    TRADOVATE_ENV              "demo" (default) or "live"
    TRADOVATE_USERNAME         Tradovate account username
    TRADOVATE_PASSWORD         Tradovate account password
    TRADOVATE_APP_ID           Application id issued by Tradovate
    TRADOVATE_APP_VERSION      Application version string (default "1.0")
    TRADOVATE_CLIENT_ID        API client id ("cid")
    TRADOVATE_CLIENT_SECRET    API client secret ("sec")
    TRADOVATE_DEVICE_ID        Any stable string identifying this bot (default "futures-bot")
    TRADOVATE_ACCOUNT_ID       Numeric Tradovate account id to trade
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import requests

from ..contracts import ContractSpec
from ..journal import LOGGER_NAME
from ..models import ExitReason, Fill, Order, OrderStatus, OrderType, Position, Side, Trade, classify_stop_exit
from .base import Broker, BrokerError

log = logging.getLogger(LOGGER_NAME)

_DEMO_URL = "https://demo.tradovateapi.com/v1"
_LIVE_URL = "https://live.tradovateapi.com/v1"

#: How long to keep polling for a fill confirmation after placing an order
#: before giving up on getting an exact price for the log line -- does NOT
#: control whether the entry is treated as successful (see submit_bracket's
#: docstring for why those are deliberately decoupled).
_FILL_POLL_TIMEOUT = 5.0
_FILL_POLL_INTERVAL = 0.5


def _env(name: str, *, required: bool = True, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name, default)
    if required and not value:
        raise BrokerError(
            f"Missing required environment variable {name!r}. Tradovate credentials come from "
            f"the environment, never from config.yaml -- see tradovate.py's module docstring for "
            f"the full list, and docs/USER_MANUAL.md for the safety checklist before first use."
        )
    return value


@dataclass(frozen=True)
class TradovateCredentials:
    username: str
    password: str
    app_id: str
    app_version: str
    client_id: str
    client_secret: str
    device_id: str
    account_id: str
    base_url: str
    environment: str

    @classmethod
    def from_env(cls) -> "TradovateCredentials":
        environment = (os.environ.get("TRADOVATE_ENV") or "demo").strip().lower()
        if environment not in ("demo", "live"):
            raise BrokerError(f"TRADOVATE_ENV must be 'demo' or 'live', got {environment!r}.")
        return cls(
            username=_env("TRADOVATE_USERNAME"),
            password=_env("TRADOVATE_PASSWORD"),
            app_id=_env("TRADOVATE_APP_ID"),
            app_version=_env("TRADOVATE_APP_VERSION", required=False, default="1.0"),
            client_id=_env("TRADOVATE_CLIENT_ID"),
            client_secret=_env("TRADOVATE_CLIENT_SECRET"),
            device_id=_env("TRADOVATE_DEVICE_ID", required=False, default="futures-bot"),
            account_id=_env("TRADOVATE_ACCOUNT_ID"),
            base_url=_LIVE_URL if environment == "live" else _DEMO_URL,
            environment=environment,
        )


@dataclass
class _TrackedEntry:
    """This process's own record of the position it believes is open.

    Needed because Tradovate's plain position endpoint reports net
    size/price but not which resting orders are its protective stop and
    target -- the same reason `trend_pullback/strategy.py` keeps its own
    `_OpenTradeState` independent of the engine's `Position`. Cleared the
    moment `poll_closed_trade` observes the account is flat again.
    """

    side: Side
    quantity: int
    entry_price: Decimal
    entry_time: datetime
    stop_loss: Decimal
    take_profit: Decimal
    parent_order_id: str
    stop_order_id: Optional[str] = None
    target_order_id: Optional[str] = None


def _to_decimal(value) -> Decimal:
    return Decimal(str(value))


class TradovateBroker(Broker):
    """See the module docstring for the safety checklist before using this
    for anything beyond a supervised demo-account walkthrough."""

    def __init__(
        self,
        contract: ContractSpec,
        symbol: str,
        credentials: Optional[TradovateCredentials] = None,
        session: Optional[requests.Session] = None,
        commission_per_side: Decimal = Decimal("0.62"),
        fill_poll_timeout: float = _FILL_POLL_TIMEOUT,
        fill_poll_interval: float = _FILL_POLL_INTERVAL,
    ) -> None:
        """``symbol`` is Tradovate's own contract symbol for the specific
        expiry being traded (e.g. ``"MESZ5"``), not ``contract.symbol``
        (``"MES"``) -- the two are related but not interchangeable, and
        Tradovate needs the former. ``credentials``/``session`` are
        constructor arguments (rather than always read from the environment
        internally) so tests can inject a fake session and fixed
        credentials without touching `os.environ`. ``fill_poll_timeout``/
        ``fill_poll_interval`` are constructor arguments for the same
        reason -- tests shrink them so a "fill never confirms" case doesn't
        make the suite wait out the production-sized timeout.
        """
        self.contract = contract
        self.symbol = symbol
        self._credentials = credentials
        self.session = session or requests.Session()
        self.commission_per_side = commission_per_side
        self.fill_poll_timeout = fill_poll_timeout
        self.fill_poll_interval = fill_poll_interval

        self._access_token: Optional[str] = None
        self._entry: Optional[_TrackedEntry] = None

    # --- Connection ---

    @property
    def credentials(self) -> TradovateCredentials:
        if self._credentials is None:
            self._credentials = TradovateCredentials.from_env()
        return self._credentials

    def connect(self) -> None:
        if self._access_token is not None:
            return  # idempotent, per the Broker contract
        creds = self.credentials
        body = {
            "name": creds.username,
            "password": creds.password,
            "appId": creds.app_id,
            "appVersion": creds.app_version,
            "cid": creds.client_id,
            "sec": creds.client_secret,
            "deviceId": creds.device_id,
        }
        data = self._request("POST", "/auth/accesstokenrequest", json=body, authed=False)
        token = data.get("accessToken")
        if not token:
            raise BrokerError(f"Tradovate authentication did not return an access token: {data}")
        self._access_token = token
        log.info(
            "Connected to Tradovate (%s environment, account %s).",
            creds.environment, creds.account_id,
        )

    def disconnect(self) -> None:
        self._access_token = None

    @property
    def is_connected(self) -> bool:
        return self._access_token is not None

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise BrokerError("Tradovate broker is not connected. Call connect() first.")

    # --- HTTP plumbing ---

    def _request(self, method: str, path: str, *, json=None, params=None, authed: bool = True) -> dict:
        url = f"{self.credentials.base_url}{path}"
        headers = {}
        if authed:
            if self._access_token is None:
                raise BrokerError("Tradovate broker is not connected. Call connect() first.")
            headers["Authorization"] = f"Bearer {self._access_token}"
        try:
            response = self.session.request(method, url, json=json, params=params, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise BrokerError(f"Tradovate request to {path} failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise BrokerError(
                f"Tradovate returned a non-JSON response from {path} (status {response.status_code})."
            ) from exc

        if response.status_code >= 400:
            raise BrokerError(f"Tradovate rejected {method} {path} (status {response.status_code}): {data}")
        if isinstance(data, dict) and data.get("errorText"):
            raise BrokerError(f"Tradovate error on {method} {path}: {data['errorText']}")
        return data

    def _get(self, path: str, params=None):
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json=body)

    # --- Account state ---

    @property
    def cash(self) -> Decimal:
        self._require_connected()
        data = self._get("/cashBalance/getCashBalanceSnapshot", params={"accountId": self.credentials.account_id})
        # Tradovate's snapshot response nests the current balance; fall back
        # across the shapes their docs show depending on endpoint version.
        if isinstance(data, dict):
            for key in ("cashBalance", "netLiqValue", "totalCashValue"):
                if key in data:
                    return _to_decimal(data[key])
        raise BrokerError(f"Could not find a cash balance field in Tradovate's response: {data}")

    def get_position(self) -> Optional[Position]:
        self._require_connected()
        positions = self._get("/position/list")
        mine = [
            p for p in positions
            if str(p.get("accountId")) == str(self.credentials.account_id) and p.get("netPos", 0) != 0
        ]
        if not mine:
            self._entry = None
            return None

        raw = mine[0]
        net = raw["netPos"]
        side = Side.LONG if net > 0 else Side.SHORT
        quantity = abs(int(net))
        # Tradovate's own netPrice is the authoritative fill price -- prefer
        # it over whatever this process guessed at submit_bracket() time
        # (see that method's docstring on why the guess can be stale).
        entry_price = _to_decimal(raw["netPrice"]) if raw.get("netPrice") is not None else (
            self._entry.entry_price if self._entry else None
        )
        if entry_price is None:
            raise BrokerError(
                "Tradovate reports an open position but no netPrice, and this process has no "
                "locally tracked entry price either (likely restarted mid-trade). Cannot safely "
                "report a Position without an entry price."
            )

        entry_time = self._entry.entry_time if self._entry else datetime.now(timezone.utc)
        stop_loss = self._entry.stop_loss if self._entry else None
        take_profit = self._entry.take_profit if self._entry else None
        stop_order_id = self._entry.stop_order_id if self._entry else None
        target_order_id = self._entry.target_order_id if self._entry else None

        return Position(
            side=side, quantity=quantity, entry_price=entry_price, entry_time=entry_time,
            stop_loss=stop_loss, take_profit=take_profit,
            stop_order_id=stop_order_id, target_order_id=target_order_id,
        )

    # --- Orders ---

    def submit_bracket(
        self, side: Side, quantity: int, stop_loss: Decimal, take_profit: Decimal, now: datetime,
    ) -> Order:
        self._require_connected()
        if self._entry is not None:
            raise BrokerError("Already tracking an open position; flatten before entering another.")

        action = "Buy" if side is Side.LONG else "Sell"
        opposite = "Sell" if side is Side.LONG else "Buy"
        creds = self.credentials

        # placeOSO ("Order Sends Order"): the entry is a market order; once
        # it fills, bracket1/bracket2 are submitted as a one-cancels-other
        # pair. This is what satisfies the Broker interface's hard
        # requirement that the protective stop rest at the broker in the
        # same call that opens the position -- there is no gap where the
        # position exists without a resting stop already on file.
        body = {
            "accountSpec": creds.username,
            "accountId": int(creds.account_id),
            "action": action,
            "symbol": self.symbol,
            "orderQty": quantity,
            "orderType": "Market",
            "isAutomated": True,
            "bracket1": {"action": opposite, "orderType": "Stop", "stopPrice": float(stop_loss)},
            "bracket2": {"action": opposite, "orderType": "Limit", "price": float(take_profit)},
        }
        data = self._post("/order/placeOSO", body)
        parent_order_id = data.get("orderId")
        if parent_order_id is None:
            raise BrokerError(f"Tradovate did not return an orderId for the bracket order: {data}")
        parent_order_id = str(parent_order_id)

        rounded_stop = self.contract.round_to_tick(stop_loss)
        rounded_target = self.contract.round_to_tick(take_profit)

        # Best-effort: try to get the actual fill price and the bracket
        # children's order ids for a nicer log line and more precise later
        # bookkeeping. Deliberately NOT allowed to fail this method -- the
        # order was already accepted by Tradovate at this point (we have a
        # parent_order_id), so raising here would desync this process from
        # a position that genuinely exists at the broker. get_position()'s
        # preference for Tradovate's own netPrice is what corrects any
        # imprecision in the fallback price used below.
        fill = self._poll_fill(parent_order_id)
        entry_price = fill.price if fill is not None else self.contract.round_to_tick(stop_loss + take_profit) / 2
        stop_order_id, target_order_id = self._find_bracket_children(parent_order_id)

        self._entry = _TrackedEntry(
            side=side, quantity=quantity, entry_price=entry_price, entry_time=now,
            stop_loss=rounded_stop, take_profit=rounded_target,
            parent_order_id=parent_order_id,
            stop_order_id=stop_order_id, target_order_id=target_order_id,
        )

        return Order(
            order_id=parent_order_id, side=side, quantity=quantity, order_type=OrderType.MARKET,
            status=OrderStatus.FILLED if fill is not None else OrderStatus.PENDING, submitted_at=now,
        )

    def _poll_fill(self, order_id: str) -> Optional[Fill]:
        deadline = time.monotonic() + self.fill_poll_timeout
        while time.monotonic() < deadline:
            try:
                fills = self._get("/fill/list", params={"accountId": self.credentials.account_id})
            except BrokerError:
                return None
            for raw in fills:
                if str(raw.get("orderId")) == order_id:
                    return _parse_fill(raw)
            time.sleep(self.fill_poll_interval)
        return None

    def _find_bracket_children(self, parent_order_id: str) -> tuple[Optional[str], Optional[str]]:
        """Best-effort lookup of the OSO's stop/target child order ids.

        Informational only -- nothing else in this codebase reads
        `Position.stop_order_id`/`target_order_id` for correctness (see
        `models.Position`'s own docstring), so a failure here never blocks
        an entry. Used by `modify_stop_loss` to know which order to amend.
        """
        try:
            orders = self._get("/order/list", params={"accountId": self.credentials.account_id})
        except BrokerError:
            return None, None

        stop_id = target_id = None
        for raw in orders:
            if str(raw.get("ocoId") or raw.get("parentId") or "") != parent_order_id:
                continue
            order_type = str(raw.get("orderType", "")).lower()
            order_id = str(raw.get("id") or raw.get("orderId") or "")
            if not order_id:
                continue
            if order_type == "stop":
                stop_id = order_id
            elif order_type == "limit":
                target_id = order_id
        return stop_id, target_id

    def modify_stop_loss(self, new_stop: Decimal) -> bool:
        self._require_connected()
        if self._entry is None:
            raise BrokerError("No open position; nothing to modify.")
        if self._entry.stop_order_id is None:
            raise BrokerError(
                "This process does not have the resting stop order's id (bracket-child lookup "
                "failed or hasn't completed yet). Refusing to silently no-op an unenforced trail "
                "-- see Broker.modify_stop_loss's docstring."
            )

        rounded = self.contract.round_to_tick(new_stop)
        if rounded == self._entry.stop_loss:
            return False

        # Ratchet-only check, same invariant PaperBroker enforces, using
        # this process's own tracked stop level. Unlike PaperBroker, this
        # adapter does not independently check "does the new stop cross the
        # current market price" -- it has no live quote feed of its own
        # (see the module docstring on why this is REST-only), and relies on
        # Tradovate's own order validation to reject a stop that would fill
        # immediately.
        if self._entry.side is Side.LONG and rounded < self._entry.stop_loss:
            raise BrokerError(
                f"Refusing to move long stop from {self._entry.stop_loss} to {rounded}: that "
                f"loosens it. A trail must only ratchet toward the position."
            )
        if self._entry.side is Side.SHORT and rounded > self._entry.stop_loss:
            raise BrokerError(
                f"Refusing to move short stop from {self._entry.stop_loss} to {rounded}: that "
                f"loosens it. A trail must only ratchet toward the position."
            )

        self._post("/order/modifyOrder", {
            "orderId": int(self._entry.stop_order_id),
            "orderQty": self._entry.quantity,
            "orderType": "Stop",
            "stopPrice": float(rounded),
            "isAutomated": True,
        })
        self._entry.stop_loss = rounded
        return True

    def cancel_all(self) -> None:
        self._require_connected()
        try:
            orders = self._get("/order/list", params={"accountId": self.credentials.account_id})
        except BrokerError:
            return
        for raw in orders:
            status = str(raw.get("ordStatus") or raw.get("status") or "").lower()
            if status != "working":
                continue
            order_id = raw.get("id") or raw.get("orderId")
            if order_id is None:
                continue
            try:
                self._post("/order/cancelOrder", {"orderId": int(order_id)})
            except BrokerError as exc:
                log.warning("Failed to cancel Tradovate order %s: %s", order_id, exc)

    def flatten(self, now: datetime, reason: str) -> Optional[Fill]:
        self._require_connected()
        if self._entry is None:
            return None

        self._post("/order/liquidatePosition", {
            "accountId": int(self.credentials.account_id),
            "symbol": self.symbol,
        })
        # liquidatePosition is documented to cancel the position's own
        # resting orders, but a broad cancel_all afterward is cheap
        # insurance against a stray leg being left working -- an unwatched
        # resting order is exactly the failure mode this codebase treats as
        # unacceptable (see brokers/base.py's module docstring).
        self.cancel_all()

        fill = self._poll_fill_by_side(self._entry.side.opposite, after=self._entry.entry_time)
        log.info("Flattened Tradovate position: %s", reason)
        return fill

    def _poll_fill_by_side(self, side: Side, after: datetime) -> Optional[Fill]:
        deadline = time.monotonic() + self.fill_poll_timeout
        while time.monotonic() < deadline:
            try:
                fills = self._get("/fill/list", params={"accountId": self.credentials.account_id})
            except BrokerError:
                return None
            candidates = [
                _parse_fill(raw) for raw in fills
                if str(raw.get("action", "")).lower() == ("buy" if side is Side.LONG else "sell")
            ]
            candidates = [f for f in candidates if f.timestamp >= after]
            if candidates:
                return max(candidates, key=lambda f: f.timestamp)
            time.sleep(self.fill_poll_interval)
        return None

    # --- Async fill reconciliation ---

    def poll_closed_trade(self, now: datetime) -> Optional[Trade]:
        """See `Broker.poll_closed_trade`'s docstring. Called by the engine
        every bar; must be cheap and safe to call when nothing has changed
        (the common case)."""
        if self._entry is None:
            return None

        self._require_connected()
        positions = self._get("/position/list")
        still_open = any(
            str(p.get("accountId")) == str(self.credentials.account_id) and p.get("netPos", 0) != 0
            for p in positions
        )
        if still_open:
            return None

        entry = self._entry
        exit_fill = self._poll_fill_by_side(entry.side.opposite, after=entry.entry_time)
        if exit_fill is None:
            # The account is flat but this process can't find the closing
            # fill yet (a fill/list lag). Don't fabricate a price -- try
            # again on the next bar rather than record a Trade we're not
            # sure about.
            log.warning(
                "Tradovate position closed but the closing fill has not appeared yet; "
                "will retry next bar."
            )
            return None

        exit_price = exit_fill.price
        gross = (exit_price - entry.entry_price) * entry.side.sign * self.contract.point_value * entry.quantity
        commission = self.commission_per_side * entry.quantity * 2

        if exit_fill.order_id == entry.stop_order_id:
            reason = classify_stop_exit(entry.side, entry.entry_price, exit_price)
        elif exit_fill.order_id == entry.target_order_id:
            reason = ExitReason.TAKE_PROFIT
        else:
            reason = ExitReason.CLOSED_AT_BROKER

        trade = Trade(
            side=entry.side, quantity=entry.quantity, entry_price=entry.entry_price, exit_price=exit_price,
            entry_time=entry.entry_time, exit_time=exit_fill.timestamp,
            gross_pnl=gross, commission=commission, exit_reason=reason,
        )
        self._entry = None
        return trade


def _parse_fill(raw: dict) -> Fill:
    ts_raw = raw.get("timestamp")
    timestamp = _parse_tradovate_timestamp(ts_raw) if ts_raw else datetime.now(timezone.utc)
    action = str(raw.get("action", "")).lower()
    side = Side.LONG if action == "buy" else Side.SHORT
    return Fill(
        order_id=str(raw.get("orderId", "")),
        side=side,
        quantity=int(raw.get("qty", 0)),
        price=_to_decimal(raw["price"]),
        timestamp=timestamp,
    )


def _parse_tradovate_timestamp(value) -> datetime:
    """Tradovate timestamps are ISO 8601 UTC strings (e.g.
    ``"2026-01-05T14:30:00.000Z"``) in every documented example this was
    written against."""
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)
