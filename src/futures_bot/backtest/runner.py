"""Historical replay.

Deliberately drives the same :class:`TradingEngine`, :class:`RiskManager`, and
:class:`PaperBroker` that paper and live trading use. A backtester with its own
private copy of the trading logic tests something you will never actually run,
and the divergence is invisible until live results fail to match.

Bars are fed one at a time, and the strategy is handed only the slice up to and
including the current one. There is no mechanism by which it can see a future
bar — not by convention, but because the future bars have not been appended to
the list yet.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..brokers.paper import PaperBroker
from ..config import Settings
from ..context import ContextEngine
from ..engine import ContextMode, TradingEngine
from ..journal import DecisionJournal, LOGGER_NAME
from ..models import Bar, Signal, SignalAction
from ..risk.manager import RiskManager
from ..session import build_session_summaries
from ..state import StateStore
from ..strategy.base import Strategy
from .metrics import BacktestMetrics

log = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class EntryRecord:
    """One acted-on entry decision, buffered by :class:`CountingJournal`.

    Exists so the research package can join entries to closed trades after a
    backtest completes (see `research.features.build_trade_records`) without
    the engine or any strategy knowing that pairing is going to happen. The
    join works because `RiskManager.can_enter` enforces one position at a
    time: acted entries and closed trades are produced in the same order,
    1:1 -- the same invariant `trend_pullback/analytics.py` already relies on
    for its own, strategy-local version of this.
    """

    timestamp: datetime
    side: str  # "long" | "short", i.e. signal.action with the enter_/_ prefix stripped
    reason: str
    metadata: dict = field(default_factory=dict)


class CountingJournal(DecisionJournal):
    """Wraps the journal to tally blocked signals/strategy errors for the
    report, and to buffer acted entry decisions for `research.features`.

    Public (unlike the rest of this module's helpers) because both `backtest`
    callers and `research` callers construct it directly: `research` code
    passes its own instance into `run_backtest(..., journal=...)` so it can
    read `.entries` back afterward, the same pattern already used for
    `run_backtest(..., broker=...)`.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.blocked = 0
        self.strategy_errors = 0
        self.entries: list[EntryRecord] = []

    def decision(self, now, signal, acted, block_reason=None, price=None, session_pnl=None):
        if block_reason is not None:
            self.blocked += 1
        if acted and signal.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            side = "long" if signal.action is SignalAction.ENTER_LONG else "short"
            self.entries.append(
                EntryRecord(timestamp=now, side=side, reason=signal.reason, metadata=dict(signal.metadata))
            )
        super().decision(now, signal, acted, block_reason, price, session_pnl)

    def event(self, now, kind, message, **extra):
        if kind == "strategy_error":
            self.strategy_errors += 1
        super().event(now, kind, message, **extra)


#: Back-compat alias -- this class was private (`_CountingJournal`) before
#: `research` needed to construct it directly.
_CountingJournal = CountingJournal


def _check_chronological(bars: Sequence[Bar]) -> None:
    """Refuse to replay bars that are not in strictly increasing time order.

    Equal adjacent timestamps are rejected too, not just decreasing ones.
    `load_bars`/`load_bars_from_db` already dedupe/sort their own single
    source, but this is the one guard every `bars` sequence passes through
    regardless of where it came from -- including a manually stitched CSV
    (two contracts' exports concatenated across a rollover window) whose
    overlap wasn't caught upstream. Replaying a duplicated moment silently
    doubles whatever signal/trade it produces; an out-of-order one corrupts
    session P&L, VWAP, and warmup state. Neither fails anywhere obvious on
    its own, so this raises loudly instead of producing a wrong-but-
    plausible report.
    """
    for i in range(1, len(bars)):
        if bars[i].timestamp <= bars[i - 1].timestamp:
            relation = "duplicates" if bars[i].timestamp == bars[i - 1].timestamp else "comes before"
            raise ValueError(
                f"Bars are not in strictly chronological order: bar {i} ({bars[i].timestamp}) "
                f"{relation} bar {i - 1} ({bars[i - 1].timestamp}). Refusing to replay -- overlapping "
                f"datasets (e.g. two contracts' data stitched across a rollover) silently duplicate "
                f"trades if allowed through, and session/indicator state assumes strictly increasing "
                f"timestamps."
            )


def run_backtest(
    settings: Settings,
    strategy: Strategy,
    bars: Sequence[Bar],
    journal_dir: Optional[Path] = None,
    broker: Optional[PaperBroker] = None,
    journal: Optional[CountingJournal] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    signal_filter: Optional[Callable[[Signal], Signal]] = None,
    context_mode: ContextMode = ContextMode.OFF,
    context_engine: Optional[ContextEngine] = None,
) -> BacktestMetrics:
    """Replay ``bars`` through the engine and return performance metrics.

    State is written to a throwaway file rather than ``settings.state_file``.
    Sharing it would let a backtest over a losing period trip the kill switch
    that a live bot then reads on its next start — a backtest must never be
    able to halt production.

    ``broker`` is normally left as ``None`` (a fresh :class:`PaperBroker` is
    built from ``settings``); the parameter exists so tests can inject a
    broker double -- e.g. one whose ``flatten`` is deliberately broken -- to
    exercise the "backtest cannot silently fail" guards below without a real
    adapter defect.

    ``journal`` is normally left as ``None`` (a fresh :class:`CountingJournal`
    is built) too; the parameter exists so callers -- chiefly
    `research.features` -- can pass in their own instance and read
    ``.entries`` back off it once this function returns, to join acted entry
    decisions with the trades in the returned metrics.

    ``progress_callback``, if given, is called periodically as
    ``callback(bars_processed, total_bars)`` -- chiefly for
    `api.jobs.JobManager` to report backtest progress to a polling/streaming
    client (see Phase 6B's docs/RESEARCH_WORKSTATION.md). Called at most
    ~100 times regardless of bar count (batched), so it can't itself become
    the bottleneck on a large replay; never called at all when omitted, so
    every existing caller is unaffected.

    ``signal_filter``, if given, is threaded straight into
    :class:`~futures_bot.engine.TradingEngine` -- see its docstring
    (Phase 9's Backtest+AI comparison). ``None`` by default, so every
    existing caller's results are unaffected.

    ``context_mode``/``context_engine`` are threaded straight into
    :class:`~futures_bot.engine.TradingEngine` too -- see ``ContextMode``.
    ``context_mode`` defaults to ``ContextMode.OFF``, so every existing
    caller's results are unaffected; ``backtest.context_comparison`` is what
    runs the same bars twice, once ``OFF`` and once ``ENABLED``, to compare.
    """
    if not bars:
        raise ValueError("No bars to replay.")
    _check_chronological(bars)

    total = len(bars)
    # At most ~100 callback invocations for the whole replay, regardless of
    # how many bars there are -- reporting every single bar on a 130,000-bar
    # backtest would make the callback itself a meaningful fraction of the
    # runtime for no benefit to a progress bar's resolution.
    report_every = max(total // 100, 1)

    with tempfile.TemporaryDirectory(prefix="backtest-state-") as tmpdir:
        store = StateStore(Path(tmpdir) / "state.json")
        risk = RiskManager(settings, store)

        if broker is None:
            broker = PaperBroker(
                contract=settings.contract_spec,
                starting_cash=settings.broker.starting_cash,
                slippage_ticks=settings.broker.slippage_ticks,
                commission_per_side=settings.broker.commission_per_side,
            )

        if journal is None:
            journal = CountingJournal(
                journal_dir or settings.logging.directory,
                settings.logging.log_every_decision,
            )

        engine = TradingEngine(
            settings, strategy, broker, risk, journal,
            signal_filter=signal_filter, context_mode=context_mode, context_engine=context_engine,
        )
        engine.start()

        for index, bar in enumerate(bars):
            try:
                engine.on_bar(bar)
            except Exception as exc:
                # Strategy bugs are already contained by
                # `TradingEngine._safe_signal`; anything that still reaches
                # here is an infrastructure fault (broker, risk manager,
                # journal I/O) with no safe way to keep replaying. Fail with
                # the bar index/timestamp attached rather than an opaque
                # traceback into engine internals, and preserve the original
                # traceback via chaining.
                raise RuntimeError(
                    f"Backtest failed at bar {index} ({bar.timestamp}): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            if progress_callback is not None and (index % report_every == 0 or index == total - 1):
                progress_callback(index + 1, total)

        # Close anything still open on the final bar, so an unrealised
        # position is not quietly excluded from the results.
        if broker.get_position() is not None:
            engine._flatten(bars[-1].timestamp, "backtest ended with position open")

        engine.stop(bars[-1].timestamp)

        if broker.get_position() is not None:
            # `_flatten` swallows BrokerError internally (logs + journals
            # "flatten_failed") so a broken adapter can't crash a run -- but
            # that also means a stuck position would otherwise leave here
            # silently: the metrics below are built from `broker.trades`
            # alone, so an unrealised position's P&L would simply be absent
            # from the report with nothing to say so. Refuse to return a
            # report that quietly omits an open position instead.
            raise RuntimeError(
                "Backtest ended with a position still open and it could not be flattened "
                "(see 'flatten_failed' in decisions.jsonl). Results would silently omit its "
                "P&L, so no metrics are returned."
            )

        return BacktestMetrics(
            trades=list(broker.trades),
            starting_equity=settings.broker.starting_cash,
            ambiguous_bars=broker.ambiguous_bars,
            bars_processed=len(bars),
            blocked_signals=journal.blocked,
            strategy_errors=journal.strategy_errors,
            first_bar=bars[0].timestamp,
            last_bar=bars[-1].timestamp,
            session_summaries=build_session_summaries(store, settings.broker.starting_cash),
        )


def split_bars(
    bars: Sequence[Bar], train_fraction: Decimal = Decimal("0.7")
) -> tuple[list[Bar], list[Bar]]:
    """Chronological train/test split for walk-forward validation.

    Split by time, never randomly. Shuffling price data lets information from
    the future leak into the training set, which is the most flattering
    mistake available in backtesting.
    """
    if not 0 < train_fraction < 1:
        raise ValueError(f"train_fraction must be between 0 and 1, got {train_fraction}")
    cut = int(len(bars) * float(train_fraction))
    return list(bars[:cut]), list(bars[cut:])


from statistics import mean, median


def rolling_walk_forward(
    settings: Settings,
    strategy_factory,
    bars: Sequence[Bar],
    train_fraction: float = 0.70,
    test_fraction: float = 0.15,
):
    """Rolling walk-forward validation.

    Example::

        Train 70%
        Test next 15%
        Shift forward by one test window
                 Train
                      Test
    """
    total = len(bars)
    train_size = int(total * train_fraction)
    test_size = int(total * test_fraction)

    if train_size <= 0 or test_size <= 0:
        raise ValueError("Dataset too small.")

    windows = []
    start = 0
    window = 1

    while start + train_size + test_size <= total:
        test = list(bars[start + train_size : start + train_size + test_size])
        metrics = run_backtest(settings, strategy_factory(), test)
        windows.append(
            {
                "window": window,
                "start": test[0].timestamp,
                "end": test[-1].timestamp,
                "metrics": metrics,
            }
        )
        start += test_size
        window += 1

    return windows


def print_walk_forward_summary(results) -> None:
    if not results:
        print("No walk-forward windows to summarize (dataset too small for the configured split).")
        return

    print()
    print("=" * 70)
    print("ROLLING WALK FORWARD")
    print("=" * 70)

    pfs, pnls, wrs, dds = [], [], [], []
    profitable = 0

    for r in results:
        m = r["metrics"]
        pf, pnl, wr, dd = m.profit_factor, m.net_pnl, m.win_rate, m.max_drawdown
        pf_str = f"{pf:.2f}" if pf is not None else "n/a"
        wr_str = f"{wr:.1%}" if wr is not None else "n/a"
        pfs.append(pf or Decimal("0"))
        pnls.append(pnl)
        wrs.append(wr or Decimal("0"))
        dds.append(dd)
        if pnl > 0:
            profitable += 1

        print(
            f"\nWindow {r['window']}\n"
            f"{r['start'].date()} -> {r['end'].date()}\n"
            f"Trades: {m.trade_count}\n"
            f"Net: ${pnl:,.2f}\n"
            f"PF: {pf_str}\n"
            f"Win Rate: {wr_str}\n"
            f"Drawdown: ${dd:,.2f}\n"
        )

    print("=" * 70)
    print(f"Windows               : {len(results)}")
    print(f"Profitable windows    : {profitable}")
    print(f"Average PF            : {mean(pfs):.2f}")
    print(f"Median PF             : {median(pfs):.2f}")
    print(f"Average win rate      : {mean(wrs):.1%}")
    print(f"Average net profit    : ${mean(pnls):,.2f}")
    print(f"Best window           : ${max(pnls):,.2f}")
    print(f"Worst window          : ${min(pnls):,.2f}")
    print(f"Average drawdown      : ${mean(dds):,.2f}")
    print(f"Consistency score     : {(profitable / len(results)) * 100:.1f}/100")
    print("=" * 70)