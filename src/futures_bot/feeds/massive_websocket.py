"""Live bar feed against Massive's delayed-data WebSocket -- a second,
independent implementation of the same `BarFeed` interface `feeds/massive.py`'s
REST-polling `MassiveBarFeed` already implements, not a replacement for it.
See `feeds/base.py`'s module docstring for the interface contract every
implementation (this one included) must uphold.

Protocol confirmed live against the real endpoint (`wss://delayed.massive.com
/futures`) during development: connect -> receive a "connected" status frame
-> send ``{"action": "auth", "params": <api_key>}`` -> receive "auth_success"
-> send ``{"action": "subscribe", "params": "AM.<TICKER>"}`` -> receive a
subscribe ack -> receive one ``AM`` (minute aggregate) event per completed
minute. Confirmed **not** available on this plan: ``T.<TICKER>`` (tick-level
trades) -- subscribing to it returned "not authorized". ``AM`` is the only
channel this feed uses or supports; unlike the REST feed, there is no
configurable resolution here -- ``resolution`` must be ``"1min"``.

``s``/``e`` on an ``AM`` event are **millisecond** epoch timestamps -- a
real, verified difference from the REST aggs endpoint's **nanosecond**
timestamps (``feeds/massive.py::_parse_bar``), not an assumption carried
over from that sibling module.

Every ``AM`` event is an already-completed minute (it carries both a start
and an end timestamp for a window that has already elapsed), so unlike the
REST feed there is no still-forming-bar filter to apply here -- everything
that arrives is immediately safe to hand to `poll_new_bars`'s caller.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

import websockets

from ..journal import LOGGER_NAME
from ..models import Bar
from .base import BarFeed

log = logging.getLogger(LOGGER_NAME)

WS_URL = "wss://delayed.massive.com/futures"

#: Reconnect backoff on a dropped connection, capped -- must not spin hot,
#: but also must not leave the feed silent for minutes at a time.
_RECONNECT_MIN_SECONDS = 1.0
_RECONNECT_MAX_SECONDS = 30.0


class MassiveWebSocketBarFeedError(RuntimeError):
    """A well-understood, fail-fast failure (auth rejected, subscribe
    rejected/"not authorized") -- as opposed to a transient connection
    drop, which the background loop reconnects from instead of raising.
    Mirrors the fail-fast standard `feeds.massive.MassiveBarFeed._fetch`
    already holds REST errors to."""


class MassiveWebSocketBarFeed(BarFeed):
    def __init__(
        self,
        symbol: str,
        api_key: str,
        resolution: str = "1min",
        connect: Callable = websockets.connect,
    ) -> None:
        if resolution != "1min":
            raise ValueError(
                f"MassiveWebSocketBarFeed only supports 1-minute bars (confirmed against the real "
                f"endpoint -- 'AM' is the only channel this plan has access to), got {resolution!r}."
            )
        self._symbol = symbol
        self.api_key = api_key
        self.resolution = resolution
        self._connect = connect

        self._bars: "queue.Queue[Bar]" = queue.Queue()
        #: (action, ticker) commands for the background loop to send on the
        #: open connection -- a symbol change can't poke the websocket
        #: object directly from this (different) thread.
        self._commands: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._startup_error: "queue.Queue[BaseException]" = queue.Queue(maxsize=1)
        self._ready = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def symbol(self) -> str:
        return self._symbol

    @symbol.setter
    def symbol(self, new_symbol: str) -> None:
        """External roll-detection code assigns this directly (the exact
        contract `feeds.massive.MassiveBarFeed.symbol` already has, e.g.
        `research_server/paper_trader.py`'s `_apply_roll`) -- the
        difference here is this also has to unsubscribe/resubscribe on the
        wire, not just relabel what the next REST request asks for."""
        if new_symbol == self._symbol:
            return
        old_symbol = self._symbol
        self._symbol = new_symbol
        if self._thread is not None:
            self._commands.put(("unsubscribe", old_symbol))
            self._commands.put(("subscribe", new_symbol))

    def start(self, timeout: float = 15.0) -> None:
        """Connects, authenticates, and subscribes -- blocks until the
        initial subscribe ack (or a fail-fast error) arrives, so a caller
        finds out immediately whether this feed is actually usable instead
        of discovering it several silent `poll_new_bars()` calls later."""
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="futures-bot-massive-websocket-feed",
        )
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self.stop()
            raise MassiveWebSocketBarFeedError(
                f"Timed out waiting {timeout}s for the WebSocket feed to connect/authenticate/subscribe."
            )
        try:
            exc = self._startup_error.get_nowait()
        except queue.Empty:
            pass
        else:
            self.stop()
            raise exc

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def poll_new_bars(self, now: Optional[datetime] = None) -> list[Bar]:
        bars: list[Bar] = []
        while True:
            try:
                bars.append(self._bars.get_nowait())
            except queue.Empty:
                break
        return bars

    # --- Background thread body ---

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        backoff = _RECONNECT_MIN_SECONDS
        while not self._stop_event.is_set():
            try:
                async with self._connect(WS_URL) as ws:
                    await self._handshake(ws)
                    backoff = _RECONNECT_MIN_SECONDS  # reset after a successful connect
                    await self._read_loop(ws)
            except MassiveWebSocketBarFeedError as exc:
                # A fail-fast condition (bad auth/an unauthorized channel)
                # will never succeed on retry -- surface it to `start()`
                # (if it's still waiting) and stop, rather than retrying
                # forever against a request that can never succeed.
                if not self._ready.is_set():
                    try:
                        self._startup_error.put_nowait(exc)
                    except queue.Full:
                        pass
                    self._ready.set()
                else:
                    log.error("massive_websocket feed: fail-fast error after startup: %s", exc)
                return
            except Exception as exc:  # noqa: BLE001 -- anything else is a transient drop to retry
                log.warning("massive_websocket feed: connection error, reconnecting: %s", exc)
            if self._stop_event.is_set():
                return
            await self._interruptible_sleep(backoff)
            backoff = min(backoff * 2, _RECONNECT_MAX_SECONDS)

    async def _interruptible_sleep(self, seconds: float) -> None:
        # threading.Event has no async wait -- poll it in short slices so
        # `stop()` (called from a different thread) is noticed promptly
        # instead of only after a full backoff period elapses.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline and not self._stop_event.is_set():
            await asyncio.sleep(min(0.2, max(0.0, deadline - loop.time())))

    async def _handshake(self, ws) -> None:
        """The three-message opening exchange is strictly sequential by
        protocol (status -> auth -> initial subscribe, confirmed live) and
        happens before the read loop (and therefore before any
        symbol-change command could ever be pending), so it's the one
        place a direct, paired send-then-recv is actually safe. Everything
        after this point goes through `_read_loop`'s single reader
        instead -- see that method's docstring for why."""
        status = json.loads(await ws.recv())
        if not (isinstance(status, list) and status and status[0].get("status") == "connected"):
            raise MassiveWebSocketBarFeedError(f"Unexpected initial message: {status!r}")

        await ws.send(json.dumps({"action": "auth", "params": self.api_key}))
        auth_resp = json.loads(await ws.recv())
        if not (isinstance(auth_resp, list) and auth_resp and auth_resp[0].get("status") == "auth_success"):
            raise MassiveWebSocketBarFeedError(f"Authentication failed: {auth_resp!r}")

        await ws.send(json.dumps({"action": "subscribe", "params": f"AM.{self._symbol}"}))
        sub_resp = json.loads(await ws.recv())
        ok = isinstance(sub_resp, list) and sub_resp and sub_resp[0].get("status") == "success"
        if not ok:
            raise MassiveWebSocketBarFeedError(f"Subscribe to AM.{self._symbol} failed: {sub_resp!r}")
        self._ready.set()

    async def _read_loop(self, ws) -> None:
        """The single reader for this connection's entire remaining
        lifetime, once the handshake is done. Deliberately not "send a
        command, then block reading the very next message as its ack" (an
        earlier version of this method did exactly that, in a helper
        shared with `_handshake`) -- once real `AM` events and other
        incidental status traffic are flowing on the same stream, "the
        next message" is not reliably "the ack for what I just sent," and
        two different call sites each blocking on their own `recv()`
        against one shared connection is a race that can leave one of them
        waiting forever for a message the other one already consumed.
        Commands are sent fire-and-forget here; whatever ack or error
        comes back is just handled the same way any other incoming
        message is, by content, not by call-site correlation.
        """
        while not self._stop_event.is_set():
            while True:
                try:
                    action, ticker = self._commands.get_nowait()
                except queue.Empty:
                    break
                await ws.send(json.dumps({"action": action, "params": f"AM.{ticker}"}))

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            for event in json.loads(raw):
                if event.get("ev") == "AM":
                    self._bars.put(_parse_am_event(event))
                elif event.get("ev") == "status" and event.get("status") == "error":
                    # An error status here (e.g. a rejected resubscribe on
                    # a roll) is logged, not fatal -- only the initial
                    # handshake's failures are fail-fast; a mid-session
                    # hiccup shouldn't take an otherwise-working feed down.
                    log.warning("massive_websocket feed: %s", event.get("message"))


def _parse_am_event(event: dict) -> Bar:
    return Bar(
        timestamp=datetime.fromtimestamp(event["s"] / 1000, tz=timezone.utc),
        open=Decimal(str(event["o"])),
        high=Decimal(str(event["h"])),
        low=Decimal(str(event["l"])),
        close=Decimal(str(event["c"])),
        volume=int(event.get("v", 0)),
    )
