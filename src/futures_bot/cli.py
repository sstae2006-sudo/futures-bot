"""Command-line entry point.

Usage:
    python -m futures_bot.cli --config config.yaml --check
    python -m futures_bot.cli --config config.yaml --demo
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from .config import load_settings
from .contracts import CME_TZ
from .engine import build_engine
from .journal import setup_logging
from .models import Bar
from .strategy.base import StrategyRegistry
from .strategy import ema_crossover  # noqa: F401  (registers the strategy)


def cmd_check(settings_path: Path) -> int:
    """Validate settings and print the risk profile in dollars."""
    settings = load_settings(settings_path)
    spec = settings.contract_spec

    print(f"Contract      : {spec.symbol} ({spec.name})")
    print(f"  point value : ${spec.point_value}  |  tick ${spec.tick_value}")
    print(f"Mode          : {settings.mode}   Broker: {settings.broker.name}")
    print(f"Strategy      : {settings.strategy_name} {settings.strategy_params}")
    print()
    print(f"Account       : ${settings.risk.account_size}")
    print(f"Risk / trade  : ${settings.risk_per_trade}  ({settings.risk_pct_of_account:.1f}% of account)")
    print(f"Reward / trade: ${settings.reward_per_trade}  ({settings.reward_risk_ratio:.2f}:1)")
    print(f"Daily limit   : ${settings.risk.daily_max_loss} "
          f"({settings.daily_limit_pct_of_account:.1f}% of account)")
    print(f"Losses to halt: {settings.losing_trades_to_hit_limit:.1f}")

    warnings = settings.risk_warnings()
    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f"  ! {w}")
    else:
        print("\nNo risk warnings.")
    return 0


def cmd_demo(settings_path: Path) -> int:
    """Run the engine over synthetic bars to prove the wiring end to end."""
    settings = load_settings(settings_path)
    setup_logging(settings.logging.level, settings.logging.directory)

    strategy_cls = StrategyRegistry.get(settings.strategy_name)
    strategy = strategy_cls(settings.contract_spec, **settings.strategy_params)
    engine = build_engine(settings, strategy)
    engine.start()

    # A rising then falling series, enough to trigger crossings both ways.
    start = datetime(2026, 7, 21, 8, 30, tzinfo=CME_TZ)
    price = Decimal("7500")
    for i in range(120):
        price += Decimal("3") if i < 60 else Decimal("-3")
        bar = Bar(
            timestamp=start + timedelta(minutes=i),
            open=price,
            high=price + Decimal("1.5"),
            low=price - Decimal("1.5"),
            close=price,
            volume=1000,
        )
        engine.on_bar(bar)

    engine.stop(start + timedelta(minutes=120))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="futures_bot")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Validate settings and show risk profile")
    group.add_argument("--demo", action="store_true", help="Run the engine on synthetic bars")
    args = parser.parse_args(argv)

    try:
        if args.check:
            return cmd_check(args.config)
        return cmd_demo(args.config)
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
