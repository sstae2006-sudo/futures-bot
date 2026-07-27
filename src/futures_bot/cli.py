"""Command-line entry point.

Usage:
    python -m futures_bot.cli --config config.yaml --check
    python -m futures_bot.cli --config config.yaml --demo
    python -m futures_bot.cli --config config.yaml --backtest data/MES_real.csv --walk-forward
    python -m futures_bot.cli --config config.yaml --backtest data/MES_real.csv --report
    python -m futures_bot.cli --config config.yaml --backtest data/MES_real.csv --html-report out.html
    python -m futures_bot.cli --config config.yaml --optimize data/MES_real.csv [--top 10] [--rolling]
    python -m futures_bot.cli --config config.yaml --compare data/MES_real.csv [--strategies a,b,c]
    python -m futures_bot.cli --config config.yaml --live --live-symbol MESH6 [--resolution 5min] [--poll-seconds 30]

    # Phase 8A: local market-data pipeline (MASSIVE_API_KEY required except --verify-data).
    python -m futures_bot.cli --sync-data --product MES [--resolution 5min]
    python -m futures_bot.cli --backfill --product MES --data-start 2024-07-21 --data-end 2026-07-23
    python -m futures_bot.cli --verify-data --product MES
    python -m futures_bot.cli --repair-gaps --product MES
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import uuid4

from . import __version__
from .config import load_settings
from .contracts import CME_TZ
from .engine import build_engine
from .journal import setup_logging
from .models import Bar
from .strategy.base import StrategyRegistry
# `strategy/__init__.py` imports every bundled strategy class, registering
# each with `StrategyRegistry` as a side effect -- one import here (not a
# separately maintained per-strategy list) is what lets --demo/--backtest/
# --optimize/--compare all find any of them by name. Previously duplicated
# as an identical 2-line import across this file, `api/services.py`, and
# `research/optimizer.py`; a strategy added to only one of those (rather
# than to `strategy/__init__.py` itself) used to work in that caller and
# raise "Unknown strategy" in the other two -- an easy miss.
from . import strategy as _strategy  # noqa: F401

from .backtest.data import is_db_dataset, load_bars, load_bars_from_db, parse_db_dataset
from .backtest.runner import run_backtest
from .backtest.report import format_report
from .backtest.html_report import generate_html_report
from .research.comparison import compare_strategies, format_leaderboard
from .research.optimizer import format_optimization_report, run_optimization
from .research.preflight import strategy_data_warnings
from .research.reporting import format_advanced_report
from .research.trade_store import TradeStore
from .research.trade_store import default_db_path as default_research_db_path
from .market_data.store import MarketDataStore, default_db_path
from .market_data.sync import backfill as md_backfill
from .market_data.sync import repair_gaps as md_repair_gaps
from .market_data.sync import sync_incremental as md_sync_incremental
from .market_data.sync import verify as md_verify
from .market_data.validation import render_report as md_render_validation_report
from .market_data.validation import validate_database as md_validate_database


def cmd_check(settings_path: Path) -> int:
    settings = load_settings(settings_path)
    spec = settings.contract_spec

    print(f"futures-bot v{__version__}  --  config: {settings_path}")
    print(f"Contract      : {spec.symbol} ({spec.name})")
    print(f"  point value : ${spec.point_value}  |  tick ${spec.tick_value}")
    print(f"Mode          : {settings.mode}   Broker: {settings.broker.name}")
    print(f"Strategy      : {settings.strategy_name} {settings.strategy_params}")
    print()
    print(f"Account       : ${settings.risk.account_size}")
    print(f"Risk / trade  : ${settings.risk_per_trade}")
    print(f"Daily limit   : ${settings.risk.daily_max_loss}")

    warnings = settings.risk_warnings() + settings.strategy_warnings()

    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f"  ! {w}")
    else:
        print("\nNo risk warnings.")

    return 0


def cmd_demo(settings_path: Path) -> int:
    settings = load_settings(settings_path)
    setup_logging(settings.logging.level, settings.logging.directory)

    strategy_cls = StrategyRegistry.get(settings.strategy_name)
    strategy = strategy_cls(settings.contract_spec, **settings.strategy_params)
    engine = build_engine(settings, strategy)
    engine.start()

    start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
    price = Decimal("7500")

    for i in range(120):
        price += Decimal("3") if i < 60 else Decimal("-3")
        engine.on_bar(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=price,
                high=price + Decimal("1.5"),
                low=price - Decimal("1.5"),
                close=price,
                volume=1000,
            )
        )

    engine.stop(start + timedelta(minutes=120))
    return 0


def _build_strategy(settings):
    strategy_cls = StrategyRegistry.get(settings.strategy_name)
    return strategy_cls(settings.contract_spec, **settings.strategy_params)


def _print_data_warnings(data_report) -> None:
    """`--optimize`/`--compare` run many backtests over the same loaded file
    and don't go through `format_report` (which has its own "DATA QUALITY"
    section) -- printed once, up front, instead."""
    warnings = data_report.warnings()
    if warnings:
        print("DATA QUALITY")
        for w in warnings:
            print(f"  ! {w}")
        print()


def _print_strategy_warnings(settings, bars) -> None:
    """Config- and data-aware sanity checks: contradictory parameters,
    a bar resolution the strategy wasn't designed for, a warmup window
    longer than the dataset -- see `Settings.strategy_warnings` and
    `research.preflight.strategy_data_warnings`. Never blocks a run."""
    warnings = settings.strategy_warnings() + strategy_data_warnings(settings, bars)
    if warnings:
        print("STRATEGY / DATA WARNINGS")
        for w in warnings:
            print(f"  ! {w}")
        print()


def _load_dataset(dataset: str):
    """Resolves either a bare CSV filename or a Phase 8A market-data
    pseudo-dataset (``"db:MES:5min"``) to the same ``(list[Bar],
    DataQualityReport)`` shape -- the one place `--backtest`/`--optimize`/
    `--compare` decide which source they're reading from, so the rest of
    each command doesn't need to know or care."""
    if is_db_dataset(dataset):
        product_code, resolution = parse_db_dataset(dataset)
        return load_bars_from_db(product_code, resolution)
    return load_bars(Path(dataset))


def _write_html_report(path: Path, metrics, settings, data_report=None) -> None:
    html = generate_html_report(metrics, settings, data_report=data_report)
    path.parent.mkdir(parents=True, exist_ok=True)
    # encoding="utf-8" explicitly: the title line embeds a Unicode em dash,
    # and Windows' open() defaults to the system codepage, not UTF-8 --
    # the same class of bug just fixed in DataQualityReport.warnings().
    path.write_text(html, encoding="utf-8")
    print(f"HTML report written to {path}")


def cmd_backtest(
    settings_path: Path, dataset: str, walk_forward: bool, report: bool, html_report: Optional[Path]
) -> int:
    settings = load_settings(settings_path)
    setup_logging("WARNING", settings.logging.directory)

    bars, data_report = _load_dataset(dataset)

    if not bars:
        print(f"No bars loaded from {dataset}", file=sys.stderr)
        return 1
    _print_strategy_warnings(settings, bars)

    if walk_forward:
        print("Running walk-forward backtest...")

        split = int(len(bars) * 0.7)
        train = bars[:split]
        test = bars[split:]

        print(f"Training bars : {len(train)}")
        print(f"Testing bars  : {len(test)}")

        train_metrics = run_backtest(settings, _build_strategy(settings), train)
        test_metrics = run_backtest(settings, _build_strategy(settings), test)

        print("\nTRAIN RESULTS")
        # Data quality is a property of the whole loaded file, not
        # specifically the train slice -- shown once, on the first report,
        # rather than duplicated on both.
        print(format_report(train_metrics, settings, data_report=data_report))

        print("\nTEST RESULTS")
        print(format_report(test_metrics, settings))

        if report:
            print()
            print(format_advanced_report(test_metrics))

        if html_report:
            # The out-of-sample half is the honest number -- see
            # `research/optimizer.py`'s "judge only by validation" framing.
            _write_html_report(html_report, test_metrics, settings, data_report)
    else:
        metrics = run_backtest(settings, _build_strategy(settings), bars)
        print(format_report(metrics, settings, data_report=data_report))

        if report:
            print()
            print(format_advanced_report(metrics))

        if html_report:
            _write_html_report(html_report, metrics, settings, data_report)

    return 0


def _format_eta(elapsed_seconds: float, done: int, total: int) -> str:
    if done == 0:
        return "unknown"
    remaining = (elapsed_seconds / done) * (total - done)
    if remaining < 60:
        return f"{remaining:.0f}s"
    if remaining < 3600:
        return f"{remaining / 60:.1f}m"
    return f"{remaining / 3600:.1f}h"


def cmd_optimize(
    settings_path: Path, dataset: str, top_n: int, rolling: bool,
    batch_id: Optional[str] = None, jobs: Optional[int] = None,
) -> int:
    """Sweeps ``settings.strategy_params`` -- any list-valued entry there is
    a sweep dimension, exactly the shape Phase 3's spec asks for -- and
    reports the best configuration with training vs. validation figures and
    a "do not trust this result because..." safety assessment.

    Every trial is persisted to the research DB as it completes (see
    `research.optimizer.run_optimization`'s docstring on continuous
    checkpointing), which is what makes resume possible: interrupt a long
    exhaustive sweep, then rerun with ``--batch-id`` set to the id printed
    at the start, and combos already completed are restored instead of
    re-run. ``jobs`` (``None`` -> every CPU core) is threaded straight
    through as ``max_workers``.
    """
    settings = load_settings(settings_path)
    setup_logging("WARNING", settings.logging.directory)

    bars, data_report = _load_dataset(dataset)
    if not bars:
        print(f"No bars loaded from {dataset}", file=sys.stderr)
        return 1
    _print_data_warnings(data_report)
    _print_strategy_warnings(settings, bars)

    resolved_batch_id = batch_id or uuid4().hex[:12]
    print(f"Batch: {resolved_batch_id} (resume with --batch-id {resolved_batch_id} if interrupted)")

    store = TradeStore(default_research_db_path())
    start_time = time.monotonic()

    def on_progress(done: int, total: int, best: Optional[dict]) -> None:
        elapsed = time.monotonic() - start_time
        percent = (done / total * 100) if total else 100.0
        eta = _format_eta(elapsed, done, total)
        best_str = ""
        if best is not None:
            params_str = ", ".join(f"{k}={v}" for k, v in best["params"].items())
            best_str = f" -- best so far: {params_str} (score {best['score']:.2f})"
        line = f"\r{done}/{total} ({percent:.1f}%) ETA {eta}{best_str}"
        # Trailing spaces overwrite whatever a previous, longer line left
        # behind -- cheap and good enough for a `\r`-updated status line,
        # no need for real terminal-width detection.
        print(line + "          ", end="", file=sys.stderr)

    try:
        try:
            result = run_optimization(
                settings, settings.strategy_name, settings.strategy_params, bars,
                top_n=top_n, rolling=rolling, store=store, batch_id=resolved_batch_id,
                progress_callback=on_progress, max_workers=jobs,
            )
        except KeyboardInterrupt:
            # Every combo that had already completed was persisted the
            # moment it finished (see run_optimization's docstring), so
            # nothing here needs to flush anything -- this is purely about
            # not leaving an overnight run's interruption as a raw
            # traceback, and making the resume path obvious instead of
            # something the user has to already know about.
            print(file=sys.stderr)
            print(
                f"Interrupted. Progress up to this point is saved -- resume with:\n"
                f"  --batch-id {resolved_batch_id}",
                file=sys.stderr,
            )
            return 130  # conventional exit code for SIGINT
    finally:
        print(file=sys.stderr)  # newline after the live-updating progress line
        store.close()

    if result.combos_cached:
        print(f"({result.combos_cached}/{result.combos_tried} combination(s) restored from a prior run.)")
    print(format_optimization_report(result))
    return 0


def cmd_compare(settings_path: Path, dataset: str, strategy_names: Optional[list[str]]) -> int:
    settings = load_settings(settings_path)
    setup_logging("WARNING", settings.logging.directory)

    bars, data_report = _load_dataset(dataset)
    if not bars:
        print(f"No bars loaded from {dataset}", file=sys.stderr)
        return 1
    _print_data_warnings(data_report)

    names = strategy_names or StrategyRegistry.names()
    entries = compare_strategies(settings, bars, [(name, {}) for name in names])
    print(format_leaderboard(entries))
    return 0


def cmd_live(
    settings_path: Path,
    live_symbol: str,
    resolution: str,
    poll_seconds: int,
    max_iterations: Optional[int] = None,
) -> int:
    """Runs the engine against a live-polled bar feed until interrupted.

    ``max_iterations`` exists only so tests can run this deterministically a
    fixed number of times; real use (the CLI's default, ``None``) loops
    until Ctrl+C.

    This is the one command in this CLI that can act on a real account --
    everything else only ever reads historical data or runs synthetic bars.
    See ``brokers/tradovate.py``'s module docstring for the safety
    checklist to run through before pointing ``--config`` at anything other
    than ``broker.name: paper`` with ``TRADOVATE_ENV=demo``.
    """
    settings = load_settings(settings_path)
    setup_logging(settings.logging.level, settings.logging.directory)

    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        print(
            "Error: MASSIVE_API_KEY environment variable is not set. The live feed's credential "
            "is read from the environment, never from --config -- see docs/USER_MANUAL.md.",
            file=sys.stderr,
        )
        return 1

    # Local import: pulls in `requests`, which nothing else in this module
    # needs -- same reasoning as the Tradovate broker's local import in
    # engine.build_engine.
    from .feeds.massive import MassiveBarFeed

    strategy = _build_strategy(settings)
    engine = build_engine(settings, strategy)
    feed = MassiveBarFeed(symbol=live_symbol, api_key=api_key, resolution=resolution)

    print(
        f"Starting live run | contract={settings.contract} strategy={settings.strategy_name} "
        f"broker={settings.broker.name} mode={settings.mode} feed_symbol={live_symbol} "
        f"resolution={resolution} poll={poll_seconds}s"
    )
    warnings = settings.risk_warnings() + settings.strategy_warnings()
    for w in warnings:
        print(f"WARNING: {w}")
    if settings.broker.name != "paper":
        print(
            f"WARNING: broker.name is {settings.broker.name!r}, not 'paper' -- this run can place "
            f"real orders. Ctrl+C flattens and stops cleanly at any time."
        )

    engine.start()
    iterations = 0
    try:
        while max_iterations is None or iterations < max_iterations:
            try:
                new_bars = feed.poll_new_bars()
            except RuntimeError as exc:
                print(f"Feed error (will retry next poll): {exc}", file=sys.stderr)
                new_bars = []

            for bar in new_bars:
                engine.on_bar(bar)
                print(f"  bar {bar.timestamp:%Y-%m-%d %H:%M} close={bar.close}")

            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                time.sleep(poll_seconds)
    except KeyboardInterrupt:
        print("\nInterrupted -- flattening and shutting down...")
    finally:
        engine.stop()

    return 0


def _require_massive_api_key() -> Optional[str]:
    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        print(
            "Error: MASSIVE_API_KEY environment variable is not set. The data pipeline's credential "
            "is read from the environment, never from --config -- see docs/USER_MANUAL.md.",
            file=sys.stderr,
        )
        return None
    return api_key


def cmd_sync_data(product: str, resolution: str) -> int:
    """Incremental sync: detect today's front-month contract, fetch forward
    from wherever the local DB's coverage already ends. See
    `market_data.sync.sync_incremental` -- this is the same operation
    `MarketDataScheduler` calls on a timer; running it by hand is just one
    manual cycle of that."""
    api_key = _require_massive_api_key()
    if not api_key:
        return 1

    store = MarketDataStore(default_db_path())
    try:
        result = md_sync_incremental(store, api_key, product, resolution)
    except Exception as exc:  # noqa: BLE001 -- reported below, not a traceback
        print(f"Error: sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    if result.rolled:
        print(f"Contract rolled: {result.rolled[0] or '(none)'} -> {result.rolled[1]}")
    print(f"Synced {product} {resolution}: {result.bars_fetched} new bar(s) (run {result.run_id}).")
    return 0


def cmd_backfill(product: str, resolution: str, start: datetime, end: datetime) -> int:
    """Populates history over [start, end], resolving the correct
    front-month contract for every sub-window automatically -- see
    `market_data.sync.backfill`."""
    api_key = _require_massive_api_key()
    if not api_key:
        return 1

    store = MarketDataStore(default_db_path())
    try:
        result = md_backfill(store, api_key, product, resolution, start, end)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: backfill failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    print(
        f"Backfilled {product} {resolution} {start:%Y-%m-%d} -> {end:%Y-%m-%d}: "
        f"{result.bars_fetched} new bar(s) across {len(set(result.contracts_used))} contract(s) "
        f"({', '.join(sorted(set(result.contracts_used))) or 'none'}) (run {result.run_id})."
    )
    return 0


def cmd_verify_data(product: str, resolution: str) -> int:
    """Scans the local DB for unexpected gaps -- no API key needed, this
    only reads what's already stored. See `market_data.sync.verify`."""
    store = MarketDataStore(default_db_path())
    try:
        report = md_verify(store, product, resolution)
    finally:
        store.close()

    print(f"{product} {resolution}: {report.bars_stored} bar(s) stored.")
    if report.earliest and report.latest:
        print(f"  Range: {report.earliest.isoformat()} -> {report.latest.isoformat()}")
    if report.new_gaps:
        print(f"  {len(report.new_gaps)} new gap(s) detected this run:")
        for gap_start, gap_end in report.new_gaps[:10]:
            print(f"    {gap_start.isoformat()} -> {gap_end.isoformat()}")
        if len(report.new_gaps) > 10:
            print(f"    (+{len(report.new_gaps) - 10} more)")
    print(f"  {report.total_open_gaps} total unresolved gap(s). Run --repair-gaps to attempt to fill them.")
    return 0


def cmd_repair_gaps(product: str, resolution: str) -> int:
    """Re-fetches every unresolved gap `--verify-data` has recorded. See
    `market_data.sync.repair_gaps`."""
    api_key = _require_massive_api_key()
    if not api_key:
        return 1

    store = MarketDataStore(default_db_path())
    try:
        report = md_repair_gaps(store, api_key, product, resolution)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: repair failed: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    print(
        f"{product} {resolution}: attempted {report.gaps_attempted} gap(s), "
        f"resolved {report.gaps_resolved}, recovered {report.bars_recovered} bar(s)."
    )
    if report.gaps_attempted > report.gaps_resolved:
        still_open = report.gaps_attempted - report.gaps_resolved
        print(f"  {still_open} gap(s) still open after re-fetching -- likely a real market closure, not a missed sync.")
    return 0


def cmd_validate_db() -> int:
    """Whole-database data-integrity scan -- no API key needed, strictly
    read-only. See `market_data.validation.validate_database` and
    docs/DATABASE_VALIDATION.md."""
    report = md_validate_database()
    print(md_render_validation_report(report))
    return 1 if not report.passed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="futures_bot",
        description=(
            "Backtesting, optimization, and paper-trading framework for a single Micro E-mini "
            "futures contract. Educational tool, not financial advice -- see docs/USER_MANUAL.md."
        ),
    )
    parser.add_argument("--version", action="version", version=f"futures-bot {__version__}")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Validate --config and print the risk profile.")
    group.add_argument("--demo", action="store_true", help="Run the engine on synthetic bars to confirm wiring.")
    group.add_argument(
        "--backtest", type=str, metavar="CSV_OR_DB",
        help="A CSV path, or a Phase 8A market-data dataset like 'db:MES:5min'.",
    )
    group.add_argument(
        "--optimize", type=str, metavar="CSV_OR_DB",
        help="Sweep strategy_params from --config. Accepts a CSV path or 'db:MES:5min'.",
    )
    group.add_argument(
        "--compare", type=str, metavar="CSV_OR_DB",
        help="Run every registered strategy and rank them. Accepts a CSV path or 'db:MES:5min'.",
    )
    group.add_argument(
        "--live", action="store_true",
        help="Run the engine against a live-polled bar feed (paper or a real broker, per --config).",
    )
    group.add_argument(
        "--sync-data", action="store_true",
        help="Data pipeline: incrementally sync one product's bars into the local market-data DB.",
    )
    group.add_argument(
        "--backfill", action="store_true",
        help="Data pipeline: backfill historical bars for a product/date range into the local market-data DB.",
    )
    group.add_argument(
        "--verify-data", action="store_true",
        help="Data pipeline: scan the local market-data DB for coverage gaps.",
    )
    group.add_argument(
        "--repair-gaps", action="store_true",
        help="Data pipeline: re-fetch previously detected gaps in the local market-data DB.",
    )
    group.add_argument(
        "--validate-db", action="store_true",
        help="Data pipeline: run the full read-only data-integrity validator against the local "
             "market-data DB. Exits nonzero if any check fails. See docs/DATABASE_VALIDATION.md.",
    )

    parser.add_argument("--walk-forward", action="store_true", help="With --backtest: train/test split.")
    parser.add_argument("--report", action="store_true", help="With --backtest: also print the advanced report.")
    parser.add_argument(
        "--html-report", type=Path, default=None, metavar="PATH",
        help="With --backtest: write a self-contained HTML report to this path.",
    )
    parser.add_argument("--top", type=int, default=10, help="With --optimize: how many combos to validate.")
    parser.add_argument("--rolling", action="store_true", help="With --optimize: rolling walk-forward validation.")
    parser.add_argument(
        "--batch-id", type=str, default=None, metavar="ID",
        help="With --optimize: resume a prior run -- combos already completed under this id are restored "
             "instead of re-run. Omit to start fresh (a fresh id is generated and printed).",
    )
    parser.add_argument(
        "--jobs", type=int, default=None, metavar="N",
        help="With --optimize: how many worker processes to run combos in parallel across. "
             "Default: every available CPU core.",
    )
    parser.add_argument(
        "--strategies", type=str, default=None,
        help="With --compare: comma-separated strategy names (default: every registered strategy).",
    )
    parser.add_argument(
        "--live-symbol", type=str, default=None, metavar="SYMBOL",
        help="With --live: the data vendor's contract symbol, e.g. 'MESH6'. Required with --live.",
    )
    parser.add_argument(
        "--resolution", type=str, default="5min",
        help="With --live: bar resolution to poll for, e.g. '5min', '1h'. Default: 5min.",
    )
    parser.add_argument(
        "--poll-seconds", type=int, default=30,
        help="With --live: seconds between polls for new bars. Default: 30.",
    )
    parser.add_argument(
        "--product", type=str, default="MES",
        help="With --sync-data/--backfill/--verify-data/--repair-gaps: the generic product symbol "
             "(e.g. 'MES'), not a specific expiry ticker -- the pipeline detects the active contract itself.",
    )
    parser.add_argument(
        "--data-start", type=str, default=None, metavar="YYYY-MM-DD",
        help="With --backfill: start of the date range to backfill (required).",
    )
    parser.add_argument(
        "--data-end", type=str, default=None, metavar="YYYY-MM-DD",
        help="With --backfill: end of the date range to backfill (required).",
    )

    args = parser.parse_args(argv)

    try:
        if args.check:
            return cmd_check(args.config)

        if args.demo:
            return cmd_demo(args.config)

        if args.backtest:
            return cmd_backtest(args.config, args.backtest, args.walk_forward, args.report, args.html_report)

        if args.optimize:
            return cmd_optimize(args.config, args.optimize, args.top, args.rolling, args.batch_id, args.jobs)

        if args.compare:
            names = args.strategies.split(",") if args.strategies else None
            return cmd_compare(args.config, args.compare, names)

        if args.live:
            if not args.live_symbol:
                print("Error: --live requires --live-symbol (e.g. --live-symbol MESH6).", file=sys.stderr)
                return 1
            return cmd_live(args.config, args.live_symbol, args.resolution, args.poll_seconds)

        if args.sync_data:
            return cmd_sync_data(args.product, args.resolution)

        if args.backfill:
            if not args.data_start or not args.data_end:
                print("Error: --backfill requires --data-start and --data-end (YYYY-MM-DD).", file=sys.stderr)
                return 1
            start = datetime.strptime(args.data_start, "%Y-%m-%d").replace(tzinfo=CME_TZ)
            end = datetime.strptime(args.data_end, "%Y-%m-%d").replace(tzinfo=CME_TZ) + timedelta(days=1) - timedelta(seconds=1)
            return cmd_backfill(args.product, args.resolution, start, end)

        if args.verify_data:
            return cmd_verify_data(args.product, args.resolution)

        if args.repair_gaps:
            return cmd_repair_gaps(args.product, args.resolution)

        if args.validate_db:
            return cmd_validate_db()

    except (FileNotFoundError, ValueError, KeyError) as exc:
        # These are the well-understood, user-facing error classes: a
        # missing/malformed config file, a bad settings value (including
        # pydantic's ValidationError, which subclasses ValueError), or an
        # unregistered strategy name. Printed as a plain message rather than
        # a traceback because the settings file is meant to be edited by
        # someone who isn't debugging Python.
        #
        # Anything else (RuntimeError from a backtest invariant failing,
        # bugs, etc.) is deliberately NOT caught here -- it should surface
        # with its full traceback rather than being flattened into a
        # one-line message that hides where it came from.
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
