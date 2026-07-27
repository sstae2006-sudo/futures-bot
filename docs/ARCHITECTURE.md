# Architecture

## System layers

The repo grew from a CLI backtester (Phases 1-5) into that plus a FastAPI
research API and React dashboard sitting on top of it (Phases 6-8B). Both
are real, independently deployable products (see
[../deploy/DEPLOYMENT.md](../deploy/DEPLOYMENT.md)) built from the same
core:

```
FRONTEND        React + TypeScript dashboard (Vite). Thin: every page calls
  |             the API and renders the response, no business logic of its
  |             own (frontend/src/api.ts mirrors backend routes 1:1).
  v
API             FastAPI. Routes -> services -> the same core engine/research
  |             modules the CLI calls directly. Adds three things the CLI
  |             doesn't have: a background job system (long-running
  |             backtests/optimizer sweeps run off the request thread,
  |             api/jobs.py), a market-data pipeline that keeps a local
  |             SQLite mirror of vendor bars in sync (market_data/), and an
  |             opt-in autonomous mode that composes both into a
  |             self-maintaining research server (research_server/).
  |             (futures_bot.api.app.create_app)
  v
CORE ENGINE     Everything below "The pipeline" heading in this doc --
  |             config, engine, strategy, risk, broker, journal. Used
  |             identically by the CLI (futures_bot.cli) and by every API
  |             route; neither the API nor the dashboard reimplements any
  |             of it.
  v
RESEARCH LAYER  Reads finished results: optimizer, comparison, walk-forward,
  |             ML dataset, insights. (futures_bot.research.*)
  v
PERSISTENCE     Two independent SQLite databases (research.db: trades/runs
                /jobs/optimization_trials; market_data.db: synced bars/
                contract rolls/gaps), plus state_file (JSON, the kill
                switch) and logs/ (JSONL decision log). No shared
                connection pool -- see research/trade_store.py and
                market_data/store.py's module docstrings for why each
                caller opens its own connection instead.
```

**Dependency direction is one-way, top to bottom.** The core engine has no
idea the API or dashboard exist -- it's imported by them, never the other
way around. `research_server/` (the autonomous layer) mostly can't import
from `api/` either, for the same reason one layer down shouldn't need to
know about the layer above it; `research_server/nightly_jobs.py` is the one
documented exception, since it reuses `api.jobs`/`api.services`' job-
submission functions rather than duplicate them. A change to a strategy or
the risk manager can never be caused by a dashboard change; a dashboard bug
can never corrupt a backtest.

**Two ways to run this, not one.** `python -m futures_bot.cli --live` runs
the core engine directly, no API/dashboard involved at all -- that's the
only path that can place a real order (`broker.name: tradovate`). Every
API route, including the dashboard's paper-trading **Live Session** page
and the autonomous **Research Server**, is structurally restricted to the
paper broker; see `api/live_session.py`'s module docstring for how that's
enforced at runtime, not just by convention.

## The pipeline

The core engine layer above, in more detail -- the part every mode
(backtest, optimize, paper session, live) drives identically:

```
DATA            CSV bars (historical) or a polled live feed (--live)
  |             (futures_bot.backtest.data.load_bars / futures_bot.feeds.*)
  v
CONFIG          YAML settings, validated (pydantic), risk stated in dollars
  |             (futures_bot.config.Settings)
  v
ENGINE          Drives one bar at a time, in a fixed order (see below)
  |             (futures_bot.engine.TradingEngine)
  v
STRATEGY        Looks at price history, proposes a Signal. Nothing else.
  |             (futures_bot.strategy.base.Strategy)
  v
RISK MANAGER    Approves or blocks the proposal
  |             (futures_bot.risk.manager.RiskManager)
  v
BROKER          Fills orders, resolves stops/targets, tracks the position
  |             (futures_bot.brokers.paper.PaperBroker, or a real adapter
  |              like futures_bot.brokers.tradovate.TradovateBroker)
  v
JOURNAL         Every decision — trade, hold, and block — logged with a reason
  |             (futures_bot.journal.DecisionJournal)
  v
RESEARCH LAYER  Reads finished results: optimizer, comparison, ML dataset
  |             (futures_bot.research.*)
  v
REPORTS         Terminal report, HTML report, advanced report
                (futures_bot.backtest.report / html_report / reporting)
```

Data, config, and the engine are the spine every run shares. Strategy, risk,
and broker are swapped independently of each other — a new strategy doesn't
touch the risk manager, a new broker adapter doesn't touch any strategy.
Everything from the journal down is read-only with respect to what already
happened: nothing past that point can change a trade that has already
closed.

## Market Context Engine (foundation, 2026-07-27 — not wired in yet)

`futures_bot.context` (`models.py`'s `MarketContext`, `context_engine.py`'s
`ContextEngine`) is the foundation for a future layer between market data and
the strategy, matching the target shape:

```
MARKET DATA -> CONTEXT ENGINE -> STRATEGY ENGINE -> RISK ENGINE -> EXECUTION
```

**What exists today:** a typed, immutable `MarketContext` value object
(session/regime/volatility/trend/liquidity/risk state, each an Enum with an
`UNKNOWN` member so a context can always be constructed safely even with
nothing known yet, plus a `confidence_scores` dict) and a `ContextEngine`
whose `build_context()` wires everything together but whose six
`_classify_*` methods are **stubs that all return `UNKNOWN`**. No indicator
math, no regime detection, nothing that could influence a trade — this
phase is data-shape and integration-point only, deliberately not yet
implemented per the phase's own scope.

**The exact integration point, when a future phase wires it in:**
`engine.TradingEngine.on_bar` (the single chokepoint both live/paper
trading *and* every backtest run through — `backtest/runner.py` replays
bars through this same engine, not a second loop) currently goes straight
from step 2 (`risk.must_flatten`) to step 3 (`strategy.on_bar`). A
`ContextEngine` would build a `MarketContext` from `self.bars` right
between those two steps and make it available to the strategy — most
likely as an additional argument threaded through `Strategy.on_bar`
alongside `bars`/`position`, decided in whichever phase actually does the
wiring. **The context engine provides information; it must never gain a
reference to the broker or risk manager, the same hard boundary that
already keeps a `Strategy` from placing its own orders** (see "Why
strategies cannot execute trades directly" below).

**Reuse, don't duplicate, when real classification is implemented:**
`research/regime.py` already classifies session/trend/volatility —
`classify_session` (CME RTH buckets), `classify_trend` (start-to-end %
move over a lookback), `classify_volatility` (ATR terciles) — but applies
them **after** a trade closes, for analytics (`GET
/api/regime/performance`), never in the live decision path.
`strategy/indicators.py` already has the primitives a real
`ContextEngine` would need (`atr`, `adx` for trend-vs-range strength,
`ema_series`, `rsi`, `vwap_bands`). The next phase should call these
existing functions from `_classify_regime`/`_classify_volatility`/
`_classify_trend`/`_classify_session` rather than re-deriving the same
math a second time — that duplication risk is exactly why this phase
stops at stubs instead of guessing at real thresholds.

**Not addressed this phase, by design:** no database persistence (a
schema change needs explicit approval per `CLAUDE.md` section 8, and
there's no trading/analytics need for one yet), no `liquidity_state`/
`risk_state` real classification (no existing equivalent to reuse — a
genuinely new future phase), no change to `Strategy`, `TradingEngine`,
or `RiskManager`.

## Why strategies cannot execute trades directly

`Strategy.on_bar` returns a `Signal` — a decision, not an order. The engine
is the only thing that ever calls `broker.submit_bracket`, and only after
`RiskManager.can_enter` has approved the attempt (`engine.py`'s docstring
calls this "strategy proposes, risk disposes").

This is a hard boundary, not a convention the strategy could route around:
`Strategy` has no reference to a `Broker` at all — its `__init__` takes a
`ContractSpec`, not a broker. A strategy that *could* place its own order
could also place one after the daily loss limit was hit, or outside the
trading window, or with a stop the risk manager never got to check. The
kill switch, the trade cap, and the trading-hours filter would all become
suggestions a strategy could ignore, whether by bug or by a parameter
someone forgot was there. Keeping the broker connection entirely on the
engine's side of the line means every entry — in a backtest, in paper
mode, or eventually live — passes through the same gate, and there's no
second code path where it doesn't.

The same separation is why `stop_loss`/`take_profit` on a `Signal` are
*requests*, not commands: `engine._bracket_prices` snaps them to a valid
tick and falls back to `config.yaml`'s `risk.stop_loss_points` /
`take_profit_points` if the strategy didn't supply one. A strategy can
suggest where its stop should sit (`trend_pullback`'s ATR-scaled stop, or
`opening_range_breakout`'s optional `stop_at_range_opposite`), but it
cannot skip having one.

## A real broker's fills happen asynchronously -- the engine has to poll for them

`PaperBroker` resolves a stop/target fill synchronously, inside `on_bar`,
against the very bar that triggered it -- there's no gap where a position
could close without the engine finding out immediately. A real broker isn't
like that: a resting stop or target at Tradovate can fill at any moment,
independent of whatever this process happens to be doing, and a plain REST
adapter (see `brokers/tradovate.py`'s module docstring for why this one is
REST-only) has no push notification for it.

`Broker.poll_closed_trade(now)` exists for exactly this gap. `TradingEngine`
calls it once per bar for any broker that isn't `PaperBroker` (and once more
after any explicit `flatten()`, since that returns a `Fill`, not a `Trade`)
-- if a tracked position has gone flat at the broker since the last check,
the adapter reconstructs a `Trade` from the exchange's own fill history and
hands it back so `RiskManager.record_trade` and the decision journal see
it. Skipping this is a real safety defect, not a cosmetic one: without it,
a stop-loss hit at the broker would never reach the daily-loss kill switch,
which would keep believing the account was flatter than it actually was.
The default implementation is a no-op specifically so this can't be
forgotten silently -- a new adapter that never overrides it will pass every
backtest-style test and then quietly never record a single live trade.

## Why backtests use the same engine

`backtest.runner.run_backtest` constructs a real `TradingEngine`,
`RiskManager`, and `PaperBroker` — the same classes `paper`/`live` mode
build in `engine.build_engine` — and just feeds them historical bars one at
a time instead of live ones. Its module docstring puts the reason plainly:
*"A backtester with its own private copy of the trading logic tests
something you will never actually run."* If the backtest had a separate,
simplified version of the risk checks or the fill logic, a bug fixed in one
copy could still be live in the other, and a strategy that looks safe in a
backtest could behave differently in paper mode for reasons that have
nothing to do with the strategy itself.

Two consequences of this that are easy to miss:

- **Backtests write state to a throwaway file**, never to
  `settings.state_file`. Sharing it would let a backtest over a losing
  historical stretch trip the kill switch that a live/paper run then reads
  on its next start.
- **A strategy cannot look ahead**, in a backtest or otherwise, because
  `on_bar` receives `bars[:i+1]` at bar `i` — the future bars have not been
  appended to the list yet. This isn't a rule the strategy has to follow;
  it's structurally impossible to violate from inside `on_bar`.

## Why research is separated

`research/` (the optimizer, the comparison leaderboard, the trade database,
the ML feature export, the safety/overfitting checks) only ever *reads*
finished results — a list of closed `Trade`s, a `BacktestMetrics` object,
an `OptimizationTrial`. Nothing in `research/` can change what a strategy
decides, what the risk manager allows, or what price a trade filled at;
`backtest.runner` doesn't import anything from `research` at all (the one
place a research function needs to run a backtest —
`research.safety.check_commission_sensitivity` — imports `run_backtest`
locally, to avoid even a module-level dependency in that direction).

This matters for the same reason strategies can't trade directly: the
moment a "research" step could feed back into what a strategy does *during*
a backtest, it becomes possible — by design or by accident — to build
something that quietly uses information from outside the bars it's
supposed to only be looking at up to and including the current one. Keeping
research strictly downstream of a finished run is what makes the
train/validation split in `research.optimizer` mean anything: the search
loop cannot reach back and change how the validation slice was evaluated,
because it never touches evaluation at all — it only ever ranks results
that already exist.

## Performance: incremental vs. batch indicators

Every strategy needs the same handful of running values (an EMA, a
session-anchored VWAP, an opening range) recomputed as each new bar closes.
There are two ways to get there:

- **Batch** (`strategy/indicators.py`'s `ema_series`, `vwap_bands`,
  `session_bars`): pure functions over the full bar history, re-run from
  scratch on every call. Simple, and correct as a *definition* — every
  incremental implementation in this codebase is tested against these.
- **Incremental** (`strategy/indicators.py`'s `IncrementalEMA`,
  `IncrementalSessionVWAP`; `strategy/trend_pullback/rolling.py`'s
  `RollingIndicators`): a small amount of running state, updated in O(1)
  per bar.

Calling a batch function once per bar makes a backtest O(n²) in bar count —
fine for a few hundred bars, and the reason a multi-month 5-minute backtest
used to slow down disproportionately as the dataset grew. As of Phase 4,
`ema_crossover`, `opening_range_breakout`, and `vwap_reversion` all use the
incremental versions (`trend_pullback` already did). Measured on real MES
data (`ema_crossover`, naive full-rescan vs. incremental, same backtest):

| Bars | Naive | Incremental | Speedup |
| ---: | ---: | ---: | ---: |
| 2,000 | 2.52s | 0.46s | 5.5x |
| 4,000 | 10.24s | 0.94s | 10.9x |
| 8,000 | 40.53s | 2.22s | 18.3x |
| 16,000 | 158.29s | 4.33s | 36.6x |

The naive time roughly quadruples every time the bar count doubles
(quadratic); the incremental time roughly doubles (linear) — and the
speedup keeps growing with dataset size, which is the signature of fixing
an O(n²) algorithm rather than just making one path faster.

This only works because of a hard rule the incremental classes' docstrings
state explicitly: bars must be fed in chronological order, exactly once
each, starting from the first bar of the series being tracked. That's true
of every real usage path (`TradingEngine.on_bar`, `backtest.runner`) by
construction — a bar is appended to `self.bars` and `on_bar` is called
immediately after, once. It is *not* automatically true of a unit test that
calls a strategy's `on_bar` directly with an arbitrary bars slice — see
`tests/test_strategies.py` and `tests/test_incremental_indicators.py` for
what that means for writing tests against these strategies.

## Design decisions worth knowing

**Decimal, not float**, throughout. Futures P&L is exact tick arithmetic;
float drift on a small account is the difference between "stop hit" and
"stop missed" (`models.py`'s docstring).

**Session dates aren't calendar dates.** CME equity index futures run
17:00 CT to 16:00 CT the next day (`contracts.py`). A position opened
18:00 CT Monday belongs to Tuesday's session. Every session-anchored
calculation in this codebase (the daily loss limit, VWAP, the opening
range) resets on that boundary, not midnight.

**The kill switch persists to disk** (`state.py`). A bot that hits its
daily loss limit, crashes, and restarts with a clean slate has a speed
bump, not a kill switch.

**Ambiguous bars resolve against you.** When a bar's OHLC range covers both
the stop and the target, there's no way to know from the data which was
hit first. The paper broker assumes the stop — assuming the target is the
most common way a backtest reports profits that couldn't have happened.
