"""Contract specifications and CME trading-session arithmetic.

The session logic here is easy to get subtly wrong and expensive when you do.
CME equity index futures do not run on calendar days: the trading day opens at
17:00 CT and runs until 16:00 CT the *next* calendar day, with a maintenance
halt in the hour between. So a position opened at 18:00 CT Monday belongs to
Tuesday's session.

That matters directly for the daily loss limit. Keying the kill switch on the
calendar date would reset it at midnight — in the middle of a live session,
right after a bad evening — and hand the account a fresh loss allowance at the
worst possible moment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

# All CME session times are quoted in Central Time. Use the named zone rather
# than a fixed offset so daylight saving is handled for us.
CME_TZ = ZoneInfo("America/Chicago")

SESSION_OPEN = time(17, 0)   # 5:00 pm CT — new session begins
SESSION_CLOSE = time(16, 0)  # 4:00 pm CT — session ends, halt begins


@dataclass(frozen=True)
class ContractSpec:
    """Everything the engine needs to price and size a contract."""

    symbol: str
    name: str
    point_value: Decimal      # dollars per 1.00 index point
    tick_size: Decimal        # minimum price increment
    currency: str = "USD"

    @property
    def tick_value(self) -> Decimal:
        """Dollars per minimum tick."""
        return self.point_value * self.tick_size

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Snap a price to a valid tick. Brokers reject anything off-tick."""
        ticks = (price / self.tick_size).quantize(Decimal("1"))
        return ticks * self.tick_size

    def points_to_dollars(self, points: Decimal, quantity: int = 1) -> Decimal:
        return points * self.point_value * quantity

    def dollars_to_points(self, dollars: Decimal, quantity: int = 1) -> Decimal:
        return dollars / (self.point_value * quantity)


# Micro E-mini S&P 500 — the contract in the Phase 1 spec.
MES = ContractSpec(
    symbol="MES",
    name="Micro E-mini S&P 500",
    point_value=Decimal("5"),
    tick_size=Decimal("0.25"),
)

# Included because a strategy tuned on one micro index often gets tried on the
# others, and hardcoding MES's numbers would silently mis-price them.
MNQ = ContractSpec(
    symbol="MNQ",
    name="Micro E-mini Nasdaq-100",
    point_value=Decimal("2"),
    tick_size=Decimal("0.25"),
)

M2K = ContractSpec(
    symbol="M2K",
    name="Micro E-mini Russell 2000",
    point_value=Decimal("5"),
    tick_size=Decimal("0.10"),
)

MYM = ContractSpec(
    symbol="MYM",
    name="Micro E-mini Dow",
    point_value=Decimal("0.50"),
    tick_size=Decimal("1.0"),
)

CONTRACTS: dict[str, ContractSpec] = {c.symbol: c for c in (MES, MNQ, M2K, MYM)}


def get_contract(symbol: str) -> ContractSpec:
    key = symbol.strip().upper()
    if key not in CONTRACTS:
        known = ", ".join(sorted(CONTRACTS))
        raise KeyError(f"Unknown contract {symbol!r}. Known contracts: {known}")
    return CONTRACTS[key]


def to_ct(moment: datetime) -> datetime:
    """Convert an aware datetime to Central Time."""
    if moment.tzinfo is None:
        raise ValueError("Expected a timezone-aware datetime")
    return moment.astimezone(CME_TZ)


def in_maintenance_halt(moment: datetime) -> bool:
    """True during the daily 16:00–17:00 CT break, when no trading occurs."""
    ct = to_ct(moment)
    return SESSION_CLOSE <= ct.time() < SESSION_OPEN


def session_date(moment: datetime) -> date:
    """Map a moment to the CME trade date its session belongs to.

    Times at or after 17:00 CT belong to the *next* calendar day's session.
    A moment inside the maintenance halt is attributed to the session that is
    about to open, since that is the one whose limits will apply next.
    """
    ct = to_ct(moment)
    if ct.time() >= SESSION_CLOSE:
        return (ct + timedelta(days=1)).date()
    return ct.date()


def is_weekend_closure(moment: datetime) -> bool:
    """True when the market is shut for the weekend.

    Closed from Friday 16:00 CT until Sunday 17:00 CT.
    """
    ct = to_ct(moment)
    weekday = ct.weekday()  # Monday == 0, Sunday == 6

    if weekday == 4 and ct.time() >= SESSION_CLOSE:  # Friday after the close
        return True
    if weekday == 5:  # all of Saturday
        return True
    if weekday == 6 and ct.time() < SESSION_OPEN:  # Sunday before the reopen
        return True
    return False


def is_market_open(moment: datetime) -> bool:
    """True when the contract is actually tradeable."""
    return not (is_weekend_closure(moment) or in_maintenance_halt(moment))


def session_close_at(moment: datetime) -> datetime:
    """The next 16:00 CT close following ``moment``.

    Used by the force-flat rule: an account that cannot meet overnight
    maintenance margin has to be flat before this time, or the broker will
    liquidate it and charge for the privilege.
    """
    ct = to_ct(moment)
    close_today = ct.replace(
        hour=SESSION_CLOSE.hour, minute=SESSION_CLOSE.minute, second=0, microsecond=0
    )
    if ct.time() >= SESSION_CLOSE:
        return close_today + timedelta(days=1)
    return close_today


def third_friday(year: int, month: int) -> date:
    """Third Friday of a month — the expiry date for quarterly index futures."""
    d = date(year, month, 1)
    # weekday(): Friday == 4
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday + timedelta(days=14)


def next_expiry(reference: Optional[date] = None) -> date:
    """Next quarterly expiry (Mar/Jun/Sep/Dec) on or after ``reference``."""
    today = reference or date.today()
    for month in (3, 6, 9, 12):
        if month < today.month:
            continue
        expiry = third_friday(today.year, month)
        if expiry >= today:
            return expiry
    return third_friday(today.year + 1, 3)


def days_to_expiry(reference: Optional[date] = None) -> int:
    today = reference or date.today()
    return (next_expiry(today) - today).days
