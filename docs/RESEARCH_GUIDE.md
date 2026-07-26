# Research Guide

The `research/` package turns finished backtest results into things you can
search, store, and learn from, without ever feeding information back into
how a strategy trades — see [ARCHITECTURE.md](ARCHITECTURE.md) for why that
separation is structural, not a convention.

## Trade database

`research/trade_store.py` is a thin SQLite layer (standard library
`sqlite3`, no new dependency) with two tables:

- **`trades`** — one denormalized row per closed trade: prices, P&L,
  timing, session date, day of week, hour, the strategy's entry reason, and
  its full metadata as JSON. Denormalized on purpose — the target consumer
  is `pandas.read_sql` or an ad hoc `SELECT *` for feature work, where a
  join you have to know to perform is friction, not structure, at this data
  volume.
- **`optimization_trials`** — one row per parameter combination a grid
  search tried, with train and validation figures and its final rank.
  Recording *every* combination, not just the winner, is what makes it
  possible to later ask "how many of these were actually any good?"

Money fields are stored as exact-string `TEXT`, not `REAL` — the same
reason `models.py` uses `Decimal` everywhere: float would reintroduce the
precision risk this codebase otherwise avoids throughout.

```python
from futures_bot.research.trade_store import TradeStore

with TradeStore("research.db") as store:
    store.insert_trades(records)                  # see "Feature extraction" below
    rows = store.fetch_trades(strategy="trend_pullback")
    trials = store.fetch_optimization_trials(batch_id="...")
```

`run_optimization` (below) takes an optional `store=` argument and persists
every trial to it automatically if you pass one.

## Feature extraction

`research/features.py` builds `TradeRecord`s — the row shape `trade_store`
persists — by joining two things a finished backtest run has:

- `broker.trades`, the authoritative list of closed round turns, and
- the strategy's own entry decisions (captured as `EntryRecord`s by
  `backtest.runner.CountingJournal` during the run).

The join is *positional*, not by timestamp: because
`RiskManager.can_enter` enforces one open position at a time, entries and
trades are produced in the same strict order with no interleaving possible,
so the Nth entry always corresponds to the Nth closed trade.
`trend_pullback/analytics.py` proved this pattern out for one strategy
first; `research/features.py` generalizes it to any strategy.

```python
from futures_bot.research.features import build_trade_records

records = build_trade_records(
    run_id="run-001", contract="MES", strategy="trend_pullback",
    strategy_params={...}, trades=metrics.trades, entries=journal.entries,
)
```

## ML dataset export

Two CSV writers, both in `research/features.py` (and, for
`trend_pullback` specifically, mirrored in `strategy/trend_pullback/analytics.py`):

- **`write_trade_log_csv`** — human-facing, exact: Decimal values kept as
  strings, one row per trade. For reviewing what actually happened.
- **`write_ml_dataset_csv`** — numeric-friendly: rounded floats,
  categoricals as plain strings, one column per feature. Because different
  strategies expose different keys in `Signal.metadata` (an EMA crossover's
  entry context looks nothing like `trend_pullback`'s RSI/ADX/ATR/VWAP
  snapshot), the feature columns are the *union* of every metadata key seen
  across the records being exported — a record from a strategy that didn't
  set a given key just gets a blank cell there.

These are library functions today, not wired to a `--trade-log`/`--dataset`
CLI flag — call them directly from a script after a backtest, the way
`trend_pullback`'s own test suite (`tests/test_trend_pullback_analytics.py`)
does.

## Optimizer

`research/optimizer.py` is the one grid-search implementation in this
codebase (as of Phase 4 — `optimize.py` at the repo root is a thin CLI
wrapper around it, not a second implementation; see its module docstring).
`run_optimization`:

1. Expands `strategy_params` into every combination where a list-valued
   entry is a sweep dimension (`expand_param_grid`) — scalar entries stay
   fixed on every combo.
2. Runs every combination on a **training** slice (`train_fraction`,
   default 70%) and ranks by `score_key` (default: net P&L).
3. Re-tests the top `top_n` on the **validation** slice the search never
   saw, or across several rolling walk-forward windows if `rolling=True`.
4. Builds a `SafetyReport` (see below) for the winner.

```python
from futures_bot.research.optimizer import run_optimization, format_optimization_report

result = run_optimization(settings, "opening_range_breakout",
                           {"range_minutes": [15, 30, 60]}, bars, top_n=10)
print(format_optimization_report(result))
```

**The rule that makes this trustworthy: judge only by the validation
number.** The training number is, by construction, the best result found by
searching — it will always look better than the strategy actually is,
because it's the peak of however many combinations were tried.

## Walk-forward validation

Two forms, both in `backtest/runner.py`:

- **Static split** (`split_bars`) — one chronological cut, train on the
  first `train_fraction`, test on the rest. `--backtest --walk-forward` and
  the optimizer's default validation both use this.
- **Rolling** (`rolling_walk_forward`) — a sliding train/test window walked
  forward across the whole validation period, producing several independent
  out-of-sample windows instead of one. More expensive (`top_n` times as
  many backtests when used inside `--optimize --rolling`), but a single
  static split can get lucky or unlucky depending on exactly where the cut
  falls; several windows average that out.

Both split **chronologically, never randomly** — shuffling price data lets
information from the future leak into the training set, which is the most
flattering mistake available in backtesting.

## Overfitting detection

`research/safety.py` is a set of checks that don't test correctness —
they're judgment calls about whether to trust a result, the same category
`config.py`'s risk warnings live in. Every check returns a `Finding`
(message + whether it's severe enough to force low confidence on its own);
`build_safety_report` combines whichever checks have data available into
one `SafetyReport` with a derived `confidence`: **High** with zero
findings, **Medium** with only non-severe ones, **Low** the moment a single
finding is severe.

| Check | Flags |
| --- | --- |
| `check_trade_counts` | Too few training or validation trades to mean anything |
| `check_degradation` | Training profitable but validation isn't, or expectancy fell hard from training to validation |
| `check_unrealistic_gains` | Zero losing trades, a suspiciously high profit factor, one trade carrying most of the profit |
| `check_parameter_fitting` | The winner is an isolated spike in the grid rather than part of a robust, profitable region |
| `check_commission_sensitivity` | The edge disappears (or shrinks a lot) if commission doubles |

The optimizer runs all of these automatically for its winning configuration
and prints the result as `format_optimization_report`'s "Confidence" /
"Warnings" section.

## Why backtests can fail

Not "fail" as in crash — `backtest.runner.run_backtest` refuses to return a
report that would be silently wrong (a stuck open position, an
out-of-chronological-order bar file, a strategy that stopped returning
valid `Signal`s). "Fail" as in *look profitable and not be*:

- **Too few trades.** Below `MIN_TRADES_FOR_SIGNIFICANCE` (30), win rate
  and expectancy are mostly noise — the same backtest on a slightly
  different sample of the same market could look very different.
  `BacktestMetrics.caveats()` states this explicitly whenever it applies.
- **One trade carrying the result.** If the single best trade produced more
  than ~40% of net profit, remove it and the strategy looks materially
  different — the result is really about catching one move, not about a
  repeatable edge.
- **Ambiguous fills resolved optimistically.** When a bar's OHLC range
  covers both the stop and the target, there's no way to know which was hit
  first from the data alone. This codebase always resolves that as the
  stop — the alternative is the most common way a backtest reports profits
  that couldn't have happened.
- **Commission and slippage ignored or underestimated.** `check_commission_sensitivity`
  exists because an edge that's mostly a function of today's cost
  assumptions isn't an edge.
- **Overfitting from the search itself.** The more parameter combinations
  tried, the more likely the best in-sample result is luck rather than
  signal — `check_parameter_fitting` and the "N combinations tried" count
  in every optimizer report exist so this is visible, not hidden.
- **Being in-sample at all.** Every report ends with a reminder that a
  strategy tuned on the same data it's measured on will always look better
  than it trades. This is the default state of a plain `--backtest` run
  with no walk-forward split — treat it as a smoke test, not a verdict.

See [TRADING_WORKFLOW.md](TRADING_WORKFLOW.md) for the order these tools
are meant to be used in so none of the above sneaks past you.
