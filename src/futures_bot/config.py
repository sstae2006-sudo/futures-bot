"""Settings loading and validation.

The spec asks for "a simple settings file for easy adjustments", which means a
non-programmer will be editing it. So validation errors need to say what is
wrong in plain language, and dangerous-but-legal values need to be surfaced in
dollar terms rather than left as abstract parameters.

Two tiers of checking:

* **Errors** reject configurations that cannot work at all — a stop of zero, a
  negative loss limit, an unknown contract.
* **Warnings** flag configurations that are legal but likely to end the account.
  These do not block startup. It is the trader's money and the trader's call;
  the job here is to make sure the number was seen before it was risked.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .contracts import ContractSpec, get_contract


def _hhmm_to_minutes(value: str) -> int:
    """``"08:30"`` -> 510. No validation -- callers already know the string
    looks like a time (it came out of a strategy_params dict a strategy
    itself would parse the same way); this only needs to compare two of them."""
    hh, mm = value.split(":")
    return int(hh) * 60 + int(mm)


class RiskSettings(BaseModel):
    """Position sizing and loss limits."""

    contracts_per_trade: int = Field(
        default=1, ge=1, le=10, description="Contracts per position. Phase 1 spec says 1."
    )
    stop_loss_points: Decimal = Field(
        ..., gt=0, description="Stop distance from entry, in index points."
    )
    take_profit_points: Decimal = Field(
        ..., gt=0, description="Profit target distance from entry, in index points."
    )
    daily_max_loss: Decimal = Field(
        ..., gt=0, description="Kill switch. Net realized loss that halts trading for the session."
    )
    max_trades_per_session: int = Field(
        default=10, ge=1, description="Ceiling on round turns per session."
    )
    account_size: Decimal = Field(
        ..., gt=0, description="Starting account equity, used for risk sanity checks."
    )
    profit_target: Optional[Decimal] = Field(
        default=None, gt=0,
        description=(
            "Session profit target, dollars. Once realized session P&L reaches this, trading "
            "stops for the rest of the day -- the mirror image of daily_max_loss. Unset (default) "
            "means no profit-based stop; the session only ends on the loss limit, trade cap, or "
            "trading-hours close."
        ),
    )
    max_consecutive_losses: Optional[int] = Field(
        default=None, ge=1,
        description="Halt the session after this many losing trades in a row. Unset (default) disables this rule.",
    )
    cooldown_minutes_after_loss: int = Field(
        default=0, ge=0,
        description=(
            "Minutes to decline new entries after a losing trade closes -- a pause, not a session "
            "end; trading resumes on its own once the cooldown elapses. 0 (default) disables this rule."
        ),
    )


class SessionSettings(BaseModel):
    """Trading-hours filter."""

    start_ct: str = Field(default="08:30", description="Earliest entry time, Central Time (HH:MM).")
    end_ct: str = Field(default="15:00", description="Latest entry time, Central Time (HH:MM).")
    flatten_before_close_minutes: int = Field(
        default=15,
        ge=0,
        description=(
            "Force-flat this many minutes before the 16:00 CT close. An account that "
            "cannot meet overnight maintenance margin must not carry a position through it."
        ),
    )
    trade_on_weekends: bool = Field(default=False)

    @field_validator("start_ct", "end_ct")
    @classmethod
    def _valid_time(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError(f"Time must look like HH:MM, got {v!r}")
        try:
            hh, mm = int(parts[0]), int(parts[1])
        except ValueError:
            raise ValueError(f"Time must look like HH:MM, got {v!r}") from None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"{v!r} is not a valid 24-hour time")
        return v

    @model_validator(mode="after")
    def _window_is_ordered(self) -> "SessionSettings":
        """start_ct must be before end_ct.

        Silently doing nothing is the failure mode here: if this were left
        unchecked, a reversed window doesn't error, it just makes
        `RiskManager.can_enter`'s ``start <= now <= end`` check false for
        every possible time of day, so every entry gets blocked with a
        generic "outside trading window" reason and nothing ever points at
        the config value that caused it.
        """
        start_h, start_m = (int(x) for x in self.start_ct.split(":"))
        end_h, end_m = (int(x) for x in self.end_ct.split(":"))
        if (start_h, start_m) >= (end_h, end_m):
            raise ValueError(
                f"session.start_ct ({self.start_ct}) must be before session.end_ct "
                f"({self.end_ct}), or no entry can ever pass the trading-window check."
            )
        return self


class BrokerSettings(BaseModel):
    name: Literal["paper", "tradovate", "ibkr"] = Field(
        default="paper",
        description="Which broker adapter to use. 'paper' simulates fills locally.",
    )
    # Paper-trading realism knobs. Assuming fills at the signal price is the
    # single most common way a backtest flatters a strategy that loses live.
    slippage_ticks: Decimal = Field(
        default=Decimal("1"),
        ge=0,
        description="Ticks of adverse slippage applied to every market fill.",
    )
    commission_per_side: Decimal = Field(
        default=Decimal("0.62"),
        ge=0,
        description="Commission per contract per side, in dollars.",
    )
    starting_cash: Decimal = Field(default=Decimal("200"), gt=0)

    # Live-adapter-only. Never a secret -- credentials for 'tradovate' come
    # from the environment (see brokers/tradovate.py's module docstring),
    # never from this file.
    tradovate_symbol: Optional[str] = Field(
        default=None,
        description=(
            "Tradovate's own contract symbol for the specific expiry to trade, e.g. 'MESZ5'. "
            "Required when broker.name is 'tradovate' -- this is not the same as the top-level "
            "'contract' setting (which names the generic product, e.g. 'MES', for pricing)."
        ),
    )

    @model_validator(mode="after")
    def _tradovate_needs_symbol(self) -> "BrokerSettings":
        if self.name == "tradovate" and not self.tradovate_symbol:
            raise ValueError(
                "broker.tradovate_symbol is required when broker.name is 'tradovate' -- Tradovate "
                "needs the specific expiry's own symbol (e.g. 'MESZ5'), not the generic contract "
                "name. Credentials themselves go in environment variables, not here -- see "
                "brokers/tradovate.py's module docstring."
            )
        return self


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    directory: Path = Field(default=Path("logs"))
    log_every_decision: bool = Field(
        default=True,
        description=(
            "Log holds and rejections as well as trades. Verbose, but the review step "
            "needs to see the trades that were *not* taken."
        ),
    )


class ResearchServerSettings(BaseModel):
    """Phase 8B: opt-in autonomous mode -- data sync, multi-strategy paper
    trading, and nightly research all start automatically when the API
    process boots, instead of requiring someone to click each on by hand.

    ``enabled`` defaults to ``False`` on purpose: everyone who was already
    running the research dashboard before this phase existed should see
    zero behavior change unless they deliberately opt in.
    """

    enabled: bool = False
    paper_strategies: list[str] = Field(
        default_factory=list,
        description="Strategy names to auto-paper-trade concurrently. Checked against "
                     "StrategyRegistry when the research server actually starts, not here -- "
                     "registry membership depends on which strategy modules happened to be "
                     "imported by that point, the same reason strategy_name isn't validated here either.",
    )
    resolution: str = Field(default="5min", description="Bar resolution for both paper trading and data sync.")
    poll_seconds: int = Field(default=30, ge=1, description="Seconds between live bar polls.")
    data_sync_products: list[str] = Field(
        default_factory=lambda: ["MES"], description="Product codes (e.g. 'MES') to keep synced via MarketDataScheduler."
    )
    nightly_job_hour_ct: int = Field(default=2, ge=0, le=23, description="Hour (Central Time, 24h) nightly research jobs submit.")
    weekly_report_weekday: int = Field(
        default=6, ge=0, le=6, description="Which weekday (Monday=0..Sunday=6) additionally runs the weekly comparison."
    )

    @field_validator("data_sync_products")
    @classmethod
    def _known_products(cls, v: list[str]) -> list[str]:
        for symbol in v:
            get_contract(symbol)  # raises with a helpful message if unknown
        return [s.strip().upper() for s in v]


class DeploymentSettings(BaseModel):
    """Deployment topology -- team-collaboration support.

    ``environment`` is informational/display only (surfaced in
    `/api/system/health` and Mission Control's status bar) -- it does not
    gate any behavior on its own. The actual "is this reachable beyond
    localhost" decision stays exactly where it already was:
    `api/__main__.py`'s `--host`/`--allow-network-exposure` guard. This
    field never bypasses that guard; it only labels which mode an
    operator intended, the same way `mode` (paper/live) labels intent
    without itself opening a broker connection.

    The database *connection string* is never in here or in
    ``config.yaml`` -- it holds credentials, so it's env-var only
    (``FUTURES_BOT_DATABASE_URL``), the same convention
    ``MASSIVE_API_KEY``/``TRADOVATE_*`` already established. Only the
    non-secret pool-tuning knobs live here.
    """

    environment: Literal["development", "team", "production"] = Field(
        default="development",
        description=(
            "Which deployment topology this process considers itself running under. "
            "'development': single developer, localhost, SQLite (today's default -- "
            "unchanged unless deliberately set otherwise). 'team': shared backend + "
            "shared TimescaleDB reachable over a private Tailscale network, no "
            "authentication (see TEAM_DEPLOYMENT.md). 'production': reserved for a "
            "future phase with real authentication in front of it -- not yet "
            "supported end-to-end by this codebase."
        ),
    )
    pool_size: int = Field(
        default=5, ge=1,
        description="Baseline pooled connections kept open to the database (TimescaleDB/Postgres only -- unused when FUTURES_BOT_DATABASE_URL is unset).",
    )
    max_overflow: int = Field(
        default=10, ge=0,
        description="Extra connections allowed above pool_size under burst load before a caller waits.",
    )
    pool_recycle_seconds: int = Field(
        default=1800, ge=60,
        description="Recycle a pooled connection after this many seconds, so a connection idle long enough for a firewall/NAT (e.g. across a Tailscale link) to silently drop it is never handed out stale.",
    )


class Settings(BaseModel):
    """Top-level configuration."""

    contract: str = Field(default="MES")
    mode: Literal["backtest", "paper", "live"] = Field(default="paper")
    risk: RiskSettings
    session: SessionSettings = Field(default_factory=SessionSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    strategy_name: str = Field(default="ema_crossover")
    strategy_params: dict = Field(default_factory=dict)
    research_server: ResearchServerSettings = Field(default_factory=ResearchServerSettings)
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)
    live_feed: Literal["rest", "websocket"] = Field(
        default="rest",
        description="How LiveSessionManager and AutonomousPaperTrader poll for new bars: 'rest' "
                     "(feeds.massive.MassiveBarFeed, the default, unchanged) or 'websocket' "
                     "(feeds.massive_websocket.MassiveWebSocketBarFeed, delayed-data push, "
                     "minute-resolution only). Defaults to 'rest' -- zero behavior change unless "
                     "deliberately opted into, same as research_server.enabled.",
    )
    state_file: Path = Field(
        default=Path("state/bot_state.json"),
        description="Where session P&L and the kill-switch flag persist across restarts.",
    )

    @field_validator("contract")
    @classmethod
    def _known_contract(cls, v: str) -> str:
        get_contract(v)  # raises with a helpful message if unknown
        return v.strip().upper()

    @model_validator(mode="after")
    def _live_mode_guard(self) -> "Settings":
        if self.mode == "live" and self.broker.name == "paper":
            raise ValueError(
                "mode is 'live' but broker is 'paper'. Set a real broker, or set mode to 'paper'."
            )
        return self

    @model_validator(mode="after")
    def _websocket_feed_is_minute_only(self) -> "Settings":
        # Only research_server's resolution is knowable at config-load time
        # -- api/live_session.py::LiveSessionManager.start() takes its
        # resolution as a per-request API parameter, not from Settings, so
        # that path is checked at call time instead (see its own guard).
        if self.live_feed == "websocket" and self.research_server.resolution != "1min":
            raise ValueError(
                f"live_feed is 'websocket' but research_server.resolution is "
                f"{self.research_server.resolution!r} -- the delayed WebSocket feed only publishes "
                f"minute aggregates (confirmed against the real endpoint: 'AM' events only, no "
                f"finer/coarser channel). Set research_server.resolution to '1min', or set live_feed "
                f"back to 'rest' to use any other resolution."
            )
        return self

    @property
    def contract_spec(self) -> ContractSpec:
        return get_contract(self.contract)

    # --- Derived risk figures, in dollars ---

    @property
    def risk_per_trade(self) -> Decimal:
        """Dollar loss if the stop is hit, excluding commission."""
        return self.contract_spec.points_to_dollars(
            self.risk.stop_loss_points, self.risk.contracts_per_trade
        )

    @property
    def reward_per_trade(self) -> Decimal:
        return self.contract_spec.points_to_dollars(
            self.risk.take_profit_points, self.risk.contracts_per_trade
        )

    @property
    def reward_risk_ratio(self) -> Decimal:
        return self.risk.take_profit_points / self.risk.stop_loss_points

    @property
    def risk_pct_of_account(self) -> Decimal:
        return (self.risk_per_trade / self.risk.account_size) * 100

    @property
    def daily_limit_pct_of_account(self) -> Decimal:
        return (self.risk.daily_max_loss / self.risk.account_size) * 100

    @property
    def losing_trades_to_hit_limit(self) -> Decimal:
        return self.risk.daily_max_loss / self.risk_per_trade

    def risk_warnings(self) -> list[str]:
        """Legal-but-dangerous configurations, described in dollars.

        Returned rather than raised: these are judgment calls about the
        trader's own capital, not correctness bugs.
        """
        warnings: list[str] = []
        spec = self.contract_spec

        if self.risk_pct_of_account > 5:
            warnings.append(
                f"Each trade risks ${self.risk_per_trade} of a ${self.risk.account_size} account "
                f"({self.risk_pct_of_account:.1f}%). Above roughly 2% per trade, a normal losing "
                f"streak becomes hard to recover from."
            )

        if self.daily_limit_pct_of_account > 20:
            warnings.append(
                f"The daily loss limit of ${self.risk.daily_max_loss} is "
                f"{self.daily_limit_pct_of_account:.1f}% of the account. Hitting it twice in a week "
                f"is a serious drawdown."
            )

        if self.losing_trades_to_hit_limit < 2:
            warnings.append(
                f"A single losing trade (${self.risk_per_trade}) meets or nearly meets the daily "
                f"limit (${self.risk.daily_max_loss}). The kill switch will end most sessions on "
                f"the first loss, which leaves very little data to review."
            )

        # The stop has to be wide enough to survive ordinary noise, or it gets
        # taken out by movement that has nothing to do with the strategy.
        if self.risk.stop_loss_points < Decimal("4") and spec.symbol == "MES":
            warnings.append(
                f"A {self.risk.stop_loss_points}-point stop on MES is inside typical intraday "
                f"noise. Expect frequent stop-outs unrelated to the signal."
            )

        if self.reward_risk_ratio < Decimal("1"):
            warnings.append(
                f"Target ({self.risk.take_profit_points} pts) is smaller than the stop "
                f"({self.risk.stop_loss_points} pts), a {self.reward_risk_ratio:.2f}:1 ratio. "
                f"This needs a win rate above "
                f"{100 / (1 + float(self.reward_risk_ratio)):.0f}% just to break even before costs."
            )

        # Round-turn commission measured against the target.
        round_turn = self.broker.commission_per_side * 2 * self.risk.contracts_per_trade
        if self.reward_per_trade > 0 and (round_turn / self.reward_per_trade) > Decimal("0.15"):
            warnings.append(
                f"Commission (${round_turn} round turn) eats "
                f"{(round_turn / self.reward_per_trade * 100):.0f}% of a winning trade "
                f"(${self.reward_per_trade}). Small targets are mostly costs."
            )

        # These two numbers are read independently elsewhere (risk warnings
        # are computed off account_size; the paper broker's actual equity is
        # seeded from starting_cash) -- a large mismatch usually means one of
        # them was edited and the other forgotten, not a deliberate choice.
        cash_diff_pct = abs(self.broker.starting_cash - self.risk.account_size) / self.risk.account_size * 100
        if cash_diff_pct > 10:
            warnings.append(
                f"broker.starting_cash (${self.broker.starting_cash}) and risk.account_size "
                f"(${self.risk.account_size}) differ by {cash_diff_pct:.0f}%. The risk warnings "
                f"above are computed from account_size; the paper broker's equity is seeded from "
                f"starting_cash. If these are meant to be the same account, one of them is stale."
            )

        return warnings

    def strategy_warnings(self) -> list[str]:
        """Parameter combinations that are legal (``strategy_params`` is a
        free-form dict; nothing here stops the strategy from being
        constructed) but likely mean the strategy can never trade, or was
        told two contradictory things.

        Data-dependent checks -- bar resolution versus the strategy's
        expected timeframe, whether a session realistically has enough bars
        for a warmup window -- need the loaded bars, so they live in
        ``research.preflight.strategy_data_warnings`` instead; this method
        only checks what the config file states about itself.
        """
        warnings: list[str] = []
        name = self.strategy_name
        params = self.strategy_params

        def get(key, default=None):
            return params.get(key, default)

        if name == "ema_crossover":
            fast = get("fast_period", 8)
            slow = get("slow_period", 34)
            if not isinstance(fast, list) and not isinstance(slow, list) and fast >= slow:
                warnings.append(
                    f"ema_crossover: fast_period ({fast}) is not below slow_period ({slow}). "
                    f"A crossover strategy needs the fast average to actually be faster; as "
                    f"configured, 'crossed up'/'crossed down' will rarely or never trigger."
                )

        elif name == "opening_range_breakout":
            min_r = get("min_range_points")
            max_r = get("max_range_points")
            if (
                min_r is not None and max_r is not None
                and not isinstance(min_r, list) and not isinstance(max_r, list)
                and Decimal(str(min_r)) > Decimal(str(max_r))
            ):
                warnings.append(
                    f"opening_range_breakout: min_range_points ({min_r}) is greater than "
                    f"max_range_points ({max_r}). Every opening range is either too small or too "
                    f"large by definition -- this strategy will never take a trade."
                )

            earliest = get("earliest_entry_ct")
            latest = get("latest_entry_ct")
            range_minutes = get("range_minutes", 30)
            session_start = get("session_start_ct", "08:30")
            if (
                earliest and latest and not isinstance(earliest, list) and not isinstance(latest, list)
            ):
                if _hhmm_to_minutes(latest) <= _hhmm_to_minutes(earliest):
                    warnings.append(
                        f"opening_range_breakout: latest_entry_ct ({latest}) is not after "
                        f"earliest_entry_ct ({earliest}). The entry window never opens -- this "
                        f"strategy will never take a trade."
                    )
                elif not isinstance(range_minutes, list) and not isinstance(session_start, list):
                    range_end = _hhmm_to_minutes(session_start) + int(range_minutes)
                    if _hhmm_to_minutes(latest) <= range_end:
                        warnings.append(
                            f"opening_range_breakout: latest_entry_ct ({latest}) is at or before "
                            f"the opening range finishes building ({session_start} + "
                            f"{range_minutes}m). The entry window closes before there is ever a "
                            f"completed range to break out of -- this strategy will never take a "
                            f"trade."
                        )

        elif name == "trend_pullback":
            sessions = get("trading_sessions")
            if isinstance(sessions, list) and sessions and isinstance(sessions[0], list):
                for window in sessions:
                    if len(window) == 2 and _hhmm_to_minutes(window[1]) <= _hhmm_to_minutes(window[0]):
                        warnings.append(
                            f"trend_pullback: trading_sessions window {window} ends at or before "
                            f"it starts. This window will never allow an entry."
                        )

            rsi_long_min = get("rsi_long_min")
            rsi_short_max = get("rsi_short_max")
            if (
                rsi_long_min is not None and rsi_short_max is not None
                and not isinstance(rsi_long_min, list) and not isinstance(rsi_short_max, list)
                and Decimal(str(rsi_long_min)) <= Decimal(str(rsi_short_max))
            ):
                warnings.append(
                    f"trend_pullback: rsi_long_min ({rsi_long_min}) is not above rsi_short_max "
                    f"({rsi_short_max}). The long and short RSI filters overlap, which is almost "
                    f"certainly not intended (typically long needs RSI above ~55, short needs it "
                    f"below ~45)."
                )

        return warnings


def load_settings(path: Path | str) -> Settings:
    """Read and validate a YAML settings file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No settings file at {p}. Copy config.example.yaml to {p.name} and edit it."
        )
    with p.open("r", encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            # yaml.YAMLError is not a ValueError, so left uncaught it skips
            # the CLI's "print a plain message" handler and surfaces as a
            # raw traceback -- exactly the failure mode this file's own
            # docstring says to avoid for a non-programmer editing YAML.
            raise ValueError(f"{p} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(
            f"{p} must contain a YAML mapping (key: value pairs) at the top level, "
            f"got {type(raw).__name__}."
        )
    return Settings.model_validate(raw)
