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

        return warnings


def load_settings(path: Path | str) -> Settings:
    """Read and validate a YAML settings file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"No settings file at {p}. Copy config.example.yaml to {p.name} and edit it."
        )
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Settings.model_validate(raw)
