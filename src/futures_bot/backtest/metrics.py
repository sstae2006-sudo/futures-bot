"""Backtest performance metrics.

The headline number is the least useful thing a backtest produces. A strategy
can show a healthy total profit while being untradeable — because it took nine
trades, or because commission ate most of the edge, or because one outlier
carried the whole result.

So this module reports the figures that expose those cases alongside the P&L,
and :meth:`BacktestMetrics.caveats` states in plain language where the result
should not be trusted. A backtest is evidence, not proof, and how much evidence
depends mostly on how many trades produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from ..models import Side, Trade
from ..session import SessionSummary

#: Below this, win rate and expectancy are dominated by noise.
MIN_TRADES_FOR_SIGNIFICANCE = 30
#: Below this, treat conclusions as provisional.
PREFERRED_TRADE_COUNT = 100


def _safe_div(a: Decimal, b: Decimal) -> Optional[Decimal]:
    return a / b if b else None


@dataclass
class BacktestMetrics:
    trades: list[Trade] = field(default_factory=list)
    starting_equity: Decimal = Decimal("0")
    ambiguous_bars: int = 0
    bars_processed: int = 0
    blocked_signals: int = 0
    #: Bars on which the strategy raised or returned something that wasn't a
    #: valid Signal, and the engine suppressed it into a safe HOLD. See
    #: `TradingEngine._safe_signal`. Should be 0 for a healthy strategy.
    strategy_errors: int = 0
    first_bar: Optional[datetime] = None
    last_bar: Optional[datetime] = None
    #: One row per simulated trading day -- see `session.build_session_summaries`.
    #: Empty unless the caller (`backtest.runner.run_backtest`) populated it.
    session_summaries: list[SessionSummary] = field(default_factory=list)

    # --- Totals ---

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def gross_pnl(self) -> Decimal:
        return sum((t.gross_pnl for t in self.trades), Decimal("0"))

    @property
    def total_commission(self) -> Decimal:
        return sum((t.commission for t in self.trades), Decimal("0"))

    @property
    def net_pnl(self) -> Decimal:
        return sum((t.net_pnl for t in self.trades), Decimal("0"))

    @property
    def ending_equity(self) -> Decimal:
        return self.starting_equity + self.net_pnl

    # --- Win / loss structure ---

    @property
    def wins(self) -> list[Trade]:
        return [t for t in self.trades if t.net_pnl > 0]

    @property
    def losses(self) -> list[Trade]:
        return [t for t in self.trades if t.net_pnl < 0]

    @property
    def scratches(self) -> list[Trade]:
        return [t for t in self.trades if t.net_pnl == 0]

    @property
    def win_rate(self) -> Optional[Decimal]:
        if not self.trades:
            return None
        return Decimal(len(self.wins)) / Decimal(len(self.trades)) * 100

    @property
    def gross_wins(self) -> Decimal:
        return sum((t.net_pnl for t in self.wins), Decimal("0"))

    @property
    def gross_losses(self) -> Decimal:
        """Positive magnitude of losing trades."""
        return abs(sum((t.net_pnl for t in self.losses), Decimal("0")))

    @property
    def profit_factor(self) -> Optional[Decimal]:
        """Gross wins divided by gross losses. Below 1.0 loses money."""
        return _safe_div(self.gross_wins, self.gross_losses)

    @property
    def expectancy(self) -> Optional[Decimal]:
        """Average net P&L per trade — the number that actually compounds."""
        if not self.trades:
            return None
        return self.net_pnl / Decimal(len(self.trades))

    @property
    def average_win(self) -> Optional[Decimal]:
        if not self.wins:
            return None
        return self.gross_wins / Decimal(len(self.wins))

    @property
    def average_loss(self) -> Optional[Decimal]:
        if not self.losses:
            return None
        return self.gross_losses / Decimal(len(self.losses))

    @property
    def largest_win(self) -> Optional[Decimal]:
        return max((t.net_pnl for t in self.wins), default=None)

    @property
    def largest_loss(self) -> Optional[Decimal]:
        return min((t.net_pnl for t in self.losses), default=None)

    @property
    def max_consecutive_losses(self) -> int:
        worst = current = 0
        for t in self.trades:
            current = current + 1 if t.net_pnl < 0 else 0
            worst = max(worst, current)
        return worst

    @property
    def max_consecutive_wins(self) -> int:
        worst = current = 0
        for t in self.trades:
            current = current + 1 if t.net_pnl > 0 else 0
            worst = max(worst, current)
        return worst

    @property
    def average_trade_duration(self) -> Optional["object"]:
        """Average holding time as a timedelta, or None with no trades."""
        if not self.trades:
            return None
        total = sum((t.exit_time - t.entry_time for t in self.trades), timedelta())
        return total / len(self.trades)

    # --- Risk-adjusted return ---

    def _trade_returns(self) -> list[Decimal]:
        """Net P&L per trade as a plain list -- the input to Sharpe/Sortino."""
        return [t.net_pnl for t in self.trades]

    @staticmethod
    def _stdev(values: Sequence[Decimal]) -> Optional[Decimal]:
        if len(values) < 2:
            return None
        mean = sum(values) / Decimal(len(values))
        variance = sum((v - mean) ** 2 for v in values) / Decimal(len(values) - 1)
        return variance.sqrt()

    @property
    def sharpe_ratio(self) -> Optional[Decimal]:
        """Mean trade P&L / stdev of trade P&L, unannualized.

        Computed per trade rather than per day, since a backtest over an
        intraday strategy has a very different number of trades than
        trading days, and per-trade is the figure that lines up with the
        rest of this report (expectancy, profit factor). Not annualized --
        multiplying by sqrt(trades/year) invites false precision when the
        underlying trade count is small, which the caveats already flag
        directly.
        """
        returns = self._trade_returns()
        sd = self._stdev(returns)
        if not sd or sd == 0:
            return None
        mean = sum(returns) / Decimal(len(returns))
        return mean / sd

    @property
    def sortino_ratio(self) -> Optional[Decimal]:
        """Like Sharpe, but only penalizes downside variance.

        A strategy with occasional large wins and otherwise steady small
        gains looks worse than it should under Sharpe, which treats upside
        swings as equally undesirable as downside ones -- Sortino doesn't.
        """
        returns = self._trade_returns()
        if len(returns) < 2:
            return None
        mean = sum(returns) / Decimal(len(returns))
        downside = [r for r in returns if r < 0]
        if not downside:
            return None
        downside_variance = sum(r ** 2 for r in downside) / Decimal(len(returns))
        downside_dev = downside_variance.sqrt()
        if downside_dev == 0:
            return None
        return mean / downside_dev

    # --- R-multiples ---

    def r_multiple(self, trade: Trade, risk_per_trade: Decimal) -> Optional[Decimal]:
        """This trade's P&L expressed as a multiple of the risk taken to open it."""
        if risk_per_trade == 0:
            return None
        return trade.net_pnl / risk_per_trade

    def average_r_multiple(self, risk_per_trade: Decimal) -> Optional[Decimal]:
        if not self.trades or risk_per_trade == 0:
            return None
        multiples = [self.r_multiple(t, risk_per_trade) for t in self.trades]
        valid = [m for m in multiples if m is not None]
        return sum(valid) / Decimal(len(valid)) if valid else None

    def expectancy_r(self, risk_per_trade: Decimal) -> Optional[Decimal]:
        """Expectancy in R -- (win_rate * avg_win_R) - (loss_rate * avg_loss_R).

        The form that generalizes across position sizes and account sizes:
        an expectancy of 0.3R means the same thing whether it was earned
        risking $50 or $500 a trade.
        """
        if not self.trades or risk_per_trade == 0:
            return None
        wins_r = [self.r_multiple(t, risk_per_trade) for t in self.wins]
        losses_r = [self.r_multiple(t, risk_per_trade) for t in self.losses]
        win_rate = Decimal(len(self.wins)) / Decimal(len(self.trades))
        loss_rate = Decimal(len(self.losses)) / Decimal(len(self.trades))
        avg_win_r = sum(wins_r) / Decimal(len(wins_r)) if wins_r else Decimal("0")
        avg_loss_r = abs(sum(losses_r) / Decimal(len(losses_r))) if losses_r else Decimal("0")
        return win_rate * avg_win_r - loss_rate * avg_loss_r

    # --- Time-based breakdowns ---

    def _group_pnl_by(self, key_fn) -> dict:
        buckets: dict = {}
        for t in self.trades:
            key = key_fn(t)
            buckets.setdefault(key, []).append(t.net_pnl)
        return {k: sum(v) for k, v in buckets.items()}

    @property
    def pnl_by_weekday(self) -> dict:
        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        raw = self._group_pnl_by(lambda t: t.entry_time.strftime("%A"))
        return {day: raw[day] for day in order if day in raw}

    @property
    def pnl_by_hour(self) -> dict:
        """Keyed by the entry hour in the trade's own timestamp, whatever zone
        the caller fed in -- callers wanting CT should pass CME-localized bars."""
        raw = self._group_pnl_by(lambda t: t.entry_time.hour)
        return dict(sorted(raw.items()))

    @property
    def pnl_by_month(self) -> dict:
        raw = self._group_pnl_by(lambda t: t.entry_time.strftime("%Y-%m"))
        return dict(sorted(raw.items()))

    # --- Risk ---

    @property
    def equity_curve(self) -> list[Decimal]:
        """Equity after each closed trade, starting from the opening balance."""
        curve = [self.starting_equity]
        for t in self.trades:
            curve.append(curve[-1] + t.net_pnl)
        return curve

    @property
    def drawdown_curve(self) -> list[Decimal]:
        """Running peak-to-current decline (<= 0) after each point in
        `equity_curve` -- the single computation `max_drawdown` (collapsed
        to the worst value) and both the HTML and API drawdown charts (the
        full per-point series) all read off, instead of each re-walking the
        peak-tracking loop independently. Previously three separate
        implementations of the identical algorithm; kept numerically
        consistent by inspection, but with no shared home, an edge-case fix
        to one would have had no way to reach the other two."""
        curve = self.equity_curve
        if not curve:
            return []
        peak = curve[0]
        out = []
        for value in curve:
            peak = max(peak, value)
            out.append(value - peak)
        return out

    @property
    def max_drawdown(self) -> Decimal:
        """Largest peak-to-trough decline in equity, in dollars.

        The figure that decides whether a strategy is survivable rather than
        merely profitable — a system that nets $2,000 after being $1,500 down
        needs an account that could sit through the $1,500.
        """
        curve = self.drawdown_curve
        return abs(min(curve, default=Decimal("0")))

    @property
    def max_drawdown_pct(self) -> Optional[Decimal]:
        if not self.starting_equity:
            return None
        return self.max_drawdown / self.starting_equity * 100

    @property
    def return_pct(self) -> Optional[Decimal]:
        if not self.starting_equity:
            return None
        return self.net_pnl / self.starting_equity * 100

    @property
    def commission_drag_pct(self) -> Optional[Decimal]:
        """Commission as a share of gross profit — how much the broker took."""
        if self.gross_pnl <= 0:
            return None
        return self.total_commission / self.gross_pnl * 100

    @property
    def largest_win_share_pct(self) -> Optional[Decimal]:
        """What fraction of net profit came from the single best trade."""
        if self.net_pnl <= 0 or not self.wins:
            return None
        best = max(t.net_pnl for t in self.wins)
        return best / self.net_pnl * 100

    # --- Breakdowns ---

    @property
    def exit_reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for t in self.trades:
            key = t.exit_reason.split("(")[0].strip()
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    @property
    def by_side(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for side in (Side.LONG, Side.SHORT):
            subset = [t for t in self.trades if t.side is side]
            if not subset:
                continue
            side_wins = [t for t in subset if t.net_pnl > 0]
            out[side.value] = {
                "count": len(subset),
                "net_pnl": sum((t.net_pnl for t in subset), Decimal("0")),
                "win_rate": Decimal(len(side_wins)) / Decimal(len(subset)) * 100,
            }
        return out

    # --- Honesty ---

    def write_equity_curve_csv(self, path) -> None:
        """One row per trade: index, timestamp, and running equity after it closed."""
        import csv
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        curve = self.equity_curve
        with p.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["trade_number", "exit_time", "equity"])
            writer.writerow([0, "", curve[0]])
            for i, trade in enumerate(self.trades, start=1):
                writer.writerow([i, trade.exit_time.isoformat(), curve[i]])

    def caveats(self) -> list[str]:
        """Reasons this result should not be taken at face value."""
        out: list[str] = []

        if self.strategy_errors:
            out.append(
                f"The strategy failed to produce a valid decision on {self.strategy_errors} "
                f"bar(s) (see 'strategy_error' events in decisions.jsonl) and was forced to hold "
                f"instead. This is a bug in the strategy, not a market outcome -- fix it before "
                f"trusting this result at all."
            )

        if self.trade_count == 0:
            out.append("No trades were taken. Nothing here can be evaluated.")
            return out

        if self.trade_count < MIN_TRADES_FOR_SIGNIFICANCE:
            out.append(
                f"Only {self.trade_count} trades. Below about "
                f"{MIN_TRADES_FOR_SIGNIFICANCE}, win rate and expectancy are mostly noise — "
                f"this result would look quite different on another sample of the same market."
            )
        elif self.trade_count < PREFERRED_TRADE_COUNT:
            out.append(
                f"{self.trade_count} trades is enough to be suggestive, not enough to be "
                f"convincing. Treat conclusions as provisional until there are "
                f"{PREFERRED_TRADE_COUNT}+."
            )

        share = self.largest_win_share_pct
        if share is not None and share > 40:
            out.append(
                f"The single best trade produced {share:.0f}% of net profit. "
                f"Remove that one trade and the strategy looks materially different, "
                f"which makes the result dependent on catching one move again."
            )

        drag = self.commission_drag_pct
        if drag is not None and drag > 30:
            out.append(
                f"Commission consumed {drag:.0f}% of gross profit "
                f"(${self.total_commission} of ${self.gross_pnl}). "
                f"Costs this large mean small edges will not survive live."
            )

        if self.ambiguous_bars:
            out.append(
                f"{self.ambiguous_bars} bar(s) touched both stop and target, so the fill order "
                f"could not be determined from OHLC. Each was resolved as a loss. Finer-grained "
                f"data would settle these; assume the true result sits somewhere above this one."
            )

        if self.gross_losses == 0 and self.wins:
            out.append(
                "No losing trades at all. On real data that almost always indicates a data or "
                "logic problem rather than a strategy without risk."
            )

        if self.max_drawdown > 0 and self.starting_equity:
            if self.max_drawdown_pct and self.max_drawdown_pct > 30:
                out.append(
                    f"Peak-to-trough drawdown reached ${self.max_drawdown} "
                    f"({self.max_drawdown_pct:.0f}% of starting equity). The account has to be "
                    f"able to sit through that, and so does the person watching it."
                )

        if self.blocked_signals > self.trade_count:
            out.append(
                f"{self.blocked_signals} signals were blocked by risk rules versus "
                f"{self.trade_count} taken. The result reflects the risk settings at least as "
                f"much as the strategy — check decisions.jsonl for what was declined and why."
            )

        out.append(
            "This is an in-sample result unless the period was held out deliberately. "
            "A strategy tuned on the same data it is measured on will always look better "
            "than it trades."
        )
        return out
