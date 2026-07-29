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
PERSISTENCE     Two independent databases (research.db: trades/runs/jobs/
                optimization_trials/ML models/client imports; market_data.db:
                synced bars/contract rolls/gaps), plus state_file (JSON, the
                kill switch) and logs/ (JSONL decision log). Two backends,
                selected by one env var: unset FUTURES_BOT_DATABASE_URL (the
                default, every single-developer setup) means each is a local
                SQLite file, no shared connection pool, each caller opens its
                own connection -- see research/trade_store.py and
                market_data/store.py's module docstrings for why. Set it to a
                Postgres DSN (team-deployment mode, see TEAM_DEPLOYMENT.md)
                and get_market_data_store()/get_store() transparently swap in
                PgMarketDataStore/PgTradeStore instead -- both backed by one
                process-wide pooled SQLAlchemy Engine (db/engine.py), schema
                managed by Alembic (alembic/), bars a TimescaleDB hypertable.
                Every caller (routes, CLI, scheduler, research_server) goes
                through those two factory functions already, so nothing else
                needs to know which backend is actually behind it.
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

## Market Context Engine (complete and integrated into TradingEngine, 2026-07-27)

`futures_bot.context` (`models.py`'s `MarketContext`, `context_engine.py`'s
`ContextEngine`, `session.py`'s `SessionContext`, `volatility.py`'s
`VolatilityContext`, `regime.py`'s `RegimeContext`, `timeframe.py`'s
`TimeframeAlignment`, `structure.py`'s `StructureContext`, `trend.py`'s
`TrendContext`, `liquidity.py`'s `LiquidityContext`, `risk.py`'s
`RiskContext`, `scoring.py`'s `EnvironmentScore`, `analytics.py`'s
`ContextAnalyticsReport`) is a complete, standalone information layer
between market data and the strategy, matching the target shape:

```
MARKET DATA -> CONTEXT ENGINE -> STRATEGY ENGINE -> RISK ENGINE -> EXECUTION
```

**Every dimension `MarketContext` defines a field for is now real** — a
typed, immutable value object (session/regime/volatility/trend/
liquidity/risk state, each an Enum with an `UNKNOWN` member so a
context can always be constructed safely even with nothing known yet,
plus a `confidence_scores` dict and a combined `environment_score`) and
a `ContextEngine` whose `build_context()` wires everything together:
`session.py`'s `classify_session`, `volatility.py`'s
`analyze_volatility`, `regime.py`'s `classify_regime`, `timeframe.py`'s
`classify_timeframe_alignment`, `structure.py`'s `analyze_structure`,
`trend.py`'s `analyze_trend`, `liquidity.py`'s `analyze_liquidity`, and
`risk.py`'s `assess_risk`. Phase 8 (2026-07-27) completed the final
three (trend, liquidity, risk — see their own subsections below),
added configurable scoring weights, and performed a full internal
validation, look-ahead audit, performance benchmark, and architecture
review before considering the engine production-ready as an
independent subsystem — see:

- `docs/CONTEXT_ENGINE_COVERAGE.md` — every dimension's status, test
  count, confidence model, dependencies, integration readiness.
- `docs/CONTEXT_ENGINE_LOOKAHEAD_AUDIT.md` — why each of the eight
  dimensions (plus the combined score) is or isn't susceptible to
  look-ahead bias, module by module.
- `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md` — timing/memory/
  scaling numbers and the one real optimization this phase found and
  fixed (`liquidity.py`).
- `docs/CONTEXT_ENGINE_ARCHITECTURE_REVIEW.md` — the final "is this
  still a pure, unintegrated information layer" check (accurate as of
  Phase 8; superseded by the integration described immediately below).

**Integration into `TradingEngine` (2026-07-27):** the engine now
actually generates and uses `MarketContext`, gated by
`engine.ContextMode` — a three-way switch specifically so "context is
generated and recorded" and "context can influence a decision" are two
separate, independently verifiable guarantees, not one on/off flag:

- **`ContextMode.OFF`** (the default for every existing caller —
  `TradingEngine.__init__`, `engine.build_engine`, and
  `backtest.runner.run_backtest` all default `context_mode` to this) is
  a complete no-op: `ContextEngine.build_context` is never called at
  all. Every existing backtest, paper session, and live session
  continues to run exactly as it did before this integration existed —
  verified directly by `tests/test_engine_context_integration.py`'s
  `TestExistingBehaviorUnchanged` (byte-identical trades against a
  pre-integration-style call).
- **`ContextMode.OBSERVE`** generates exactly one `MarketContext` per
  processed bar and attaches it to every completed trade's new
  `Trade.entry_context` field — but never sets `Strategy.context`, so no
  strategy can read it. Trading decisions are therefore *provably*
  identical to `OFF`, not merely "should be unaffected" — the strategy
  never sees the object, so it cannot possibly act on it.
- **`ContextMode.ENABLED`** does everything `OBSERVE` does, **plus**
  sets `Strategy.context` — but only for a strategy whose own
  `uses_context` class attribute is `True`. Every bundled strategy
  defaults to `False`, so running an existing, unmodified strategy in
  `ENABLED` mode is still decision-identical to `OFF`/`OBSERVE`; only a
  strategy that has explicitly opted in can ever see or act on
  `self.context`.

**Where this sits in `TradingEngine.on_bar`:** a new step 0, before the
four steps the module docstring already describes — built once per bar,
immediately after the bar is appended to `self.bars`, so every
subsequent step (including a forced flatten that skips the strategy
entirely) sees the same reading:

```
0. Build MarketContext (ContextMode.OFF: skipped entirely)
1. Resolve resting protective orders
2. Forced exits (risk.must_flatten)
3. Ask the strategy (Strategy.context set here, ENABLED + opted-in only)
4. Act, subject to risk
```

`self.bars` is a bounded `deque`, which does not support the slice
indexing several `context/` modules rely on (`liquidity.py`/
`volatility.py`'s trailing windows) — `_build_market_context` converts
it to a `list` once per bar before calling `ContextEngine.build_context`;
every classifier already only reads a trailing slice of whatever it's
given, so this changes nothing about correctness, only compatibility
with the container type. `_build_market_context` is also wrapped in a
broad `except`, so a defect in `context/` can never crash a live/paper/
backtest run — the same defensive posture `_safe_signal` already takes
toward strategy code.

**Attaching context to trades:** `Trade` gained a new, purely-additive
`entry_context: Optional[MarketContext] = None` field (a
`TYPE_CHECKING`-guarded forward reference in `models.py`, avoiding a real
import cycle — the same pattern `context/models.py` already uses for its
own forward references). Neither broker (`PaperBroker`/`TradovateBroker`)
ever sets it — brokers stay entirely unaware of `context/`, per
requirement #6 ("no broker logic changes"), verified directly by a test
inspecting `brokers/paper.py`'s own imports. `TradingEngine` captures the
entry-time `MarketContext` in `_handle_signal` (right after a successful
`submit_bracket`) and attaches it in `_record_trade` — the single shared
closing path for every trade, regardless of *why* it closed (a resting
stop/target resolving, a risk-forced flatten, a strategy exit) — via
`dataclasses.replace`, since `Trade` is frozen. That replacement is also
written back into `PaperBroker.trades[-1]`: `dataclasses.replace` returns
a *new* object, and without writing it back, the enriched copy would only
have existed in `_record_trade`'s local scope — gone the moment it
returns, while `backtest.runner.run_backtest` (which builds
`BacktestMetrics.trades` from `list(broker.trades)`) would still have
read the original, un-enriched trade. This was caught and fixed during
this integration's own manual verification before being trusted.

**Same execution path for backtesting, paper trading, and live
trading — no duplicate pipeline:** `context_mode`/`context_engine` are
threaded through exactly two call sites — `engine.build_engine` (used by
`cli.py`'s live/paper path and `research_server/paper_trader.py`) and
`backtest.runner.run_backtest` (used by every backtest caller, including
`api/services.py`) — both of which construct the same `TradingEngine`
every mode/caller shares. There is no second replay loop and no
context-aware fork of the engine; `ContextMode` is a constructor
argument, not a different code path.

**A/B comparison (`backtest/context_comparison.py`):** given the same
strategy factory/settings/bars, `compare_context_impact` runs
`ContextMode.OBSERVE` (the baseline — decision-identical to `OFF`, but
every trade carries its context, which the comparison needs) and
`ContextMode.ENABLED` (may differ, if the strategy opted in) through the
*same* `run_backtest`, then diffs the two trade lists. Each changed trade
is classified `UNCHANGED`/`REMOVED_BY_CONTEXT`/`ADDED_BY_CONTEXT`/
`ENTERED_DIFFERENTLY`/`EXITED_DIFFERENTLY` and carries the
`MarketContext`/`EnvironmentScore` that explains it. See that module's
own docstring for a documented caveat: only the *first* point where the
two runs diverge is guaranteed to be directly explained by the
strategy's own context rule — once one run skips a trade the other took,
the two runs' open-position timelines can drift apart, so later changes
may be downstream consequences of that first divergence rather than each
independently explained.

**Performance impact:** `ContextMode.OFF` adds zero measurable overhead
(no call is made at all). `OBSERVE`/`ENABLED` add one
`ContextEngine.build_context` call per bar, whose cost scales with
`len(self.bars)` (bounded by the engine's existing bar-retention window,
not backtest length) — see
`docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md` for the underlying
per-call numbers; a full-length backtest run in `OBSERVE`/`ENABLED`
mode is measurably slower than `OFF`; a future caller that wants context
in a very long replay should be aware of this rather than surprised by
it.

**Context Scoring System (`scoring.py`, Phase 7 2026-07-27, made
configurable Phase 8 2026-07-27):** `score_environment` combines every
dimension already on a built `MarketContext` into a single
`EnvironmentScore` — a 0-100 reading of how favorable current
conditions look for a systematic strategy to operate in *generally*
(clear trend, normal volatility, a liquid session, confirmed structure,
ample liquidity, manageable risk). **This is not a directional
(bullish/bearish) signal, and information only — it does not decide
trades**; `EnvironmentScore` carries no broker/risk-manager/engine
reference of any kind, verified directly by a test that inspects the
module's own imports. Six dimensions each contribute a signed value
scaled by a maximum weight — **now a field on `ScoringConfig`, not a
hardcoded constant** (`trend_weight`/`volatility_weight`/
`session_weight`/`structure_weight`/`liquidity_weight`/`risk_weight`),
supporting future weighting experimentation with zero code changes:
construct a different `ScoringConfig` and pass it to
`score_environment`/`with_environment_score`/`ContextEngine`'s own
`scoring_config` constructor argument. `DEFAULT_SCORING_CONFIG` holds
the values this phase's own worked example was built against (Trend
20, Volatility 15, Session 10, Structure 20, Liquidity 15, Risk -10 —
`20 + 15 + 10 + 20 + 15 - 10 == 70`); calling `score_environment`/
`ContextEngine(...)` with no config argument reproduces every
pre-Phase-8 test's behavior exactly (verified directly by
`tests/test_context_scoring.py`'s `TestConfigurableScoring`). The total
is clamped to `[0, 100]`. A dimension with no data (`UNKNOWN`, or its
sub-context missing) contributes exactly `0.0` and is left out of both
`reasons` and the `confidence` fraction, regardless of which config is
in effect — never a fabricated guess. `confidence` is the fraction of
the six dimensions that actually had data, independent of whether the
score itself is high or low. Because the score is computed from the
*rest* of an already-built `MarketContext`, `ContextEngine.build_context`
constructs the object in two steps — the base `MarketContext(...)` call,
then `scoring.with_environment_score` (a `dataclasses.replace`) to fill
in the one field that depends on everything else.

**Trend State (`trend.py`, Phase 8 2026-07-27):** `analyze_trend`
classifies pure trend *direction* (`TrendState`: BULLISH/BEARISH/
NEUTRAL/UNKNOWN) — a simpler, standalone reading than `regime.py`'s
volatility-coupled `MarketRegime` composite, available with far less
history (just 2 closes for direction; `regime.py` needs enough bars for
ATR too, or it returns `UNKNOWN` outright). Reuses
`research.regime.classify_trend` (the same function `regime.py`/
`timeframe.py` already reuse) for direction, and `strategy.indicators.adx`
plus `regime.py`'s own `ADX_TRENDING_THRESHOLD`/`ADX_CONFIDENCE_SCALE`
constants for confidence — the "how strong is this direction" scale
matches `regime.py`'s exactly rather than a second, subtly different one.

**Liquidity State (`liquidity.py`, Phase 8 2026-07-27):**
`analyze_liquidity` classifies relative volume (`LiquidityState`:
THIN/NORMAL/DEEP/UNKNOWN) — current bar's volume vs. a trailing average,
reusing `strategy.indicators.sma` for the average (the same primitive
`strategy/trend_pullback/strategy.py` uses for its own, strategy-local
`volume_ratio` analytics field — reusing that strategy's specific code
directly would create an inappropriate `context/` → `strategy/`
dependency, so this module instead reuses the underlying generic
primitive and builds a new, general-purpose classifier from it).
Genuinely new classification logic (no existing liquidity/relative-
volume classifier anywhere in this codebase), following the same
trailing-window-ratio *shape* `volatility.py` already established for
this package. Optimized during Phase 8's performance benchmark to
convert only the trailing `lookback` bars to `Decimal`, not the entire
history — see `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`.

**Risk State (`risk.py`, Phase 8 2026-07-27):** `assess_risk` is a
**pure composite of two already-computed signals** —
`volatility_state` and `market_regime` — exactly what this method's own
Phase-1 stub docstring anticipated ("likely a composite of the other
classifications above ... decided once real thresholds exist for those
inputs"). No new market-data analysis, no bars read at all: `assess_risk`
takes only the two enum values and returns a `RiskState` (LOW/ELEVATED/
HIGH/UNKNOWN) plus a confidence that's higher when driven directly by
`volatility_state` and lower when falling back to `market_regime` alone
(only relevant when volatility itself is `UNKNOWN`). Unrelated to, and
never consulted by, `risk.manager.RiskManager` — naming collision only,
verified by a test inspecting the module's own imports.

**Multi-Timeframe Context (`timeframe.py`, 2026-07-27):**
`classify_timeframe_alignment` combines trend direction across five
canonical timeframes (`TIMEFRAME_ORDER`: `1m`/`5m`/`15m`/`1h`/`1d`) into
one `TimeframeAlignment` (`alignment`: a dict of timeframe → `TrendState`
for whichever timeframes had data; `alignment_score`: the magnitude,
`[0.0, 1.0]`, of a rank-weighted average direction — 1.0 means every
present timeframe agrees, 0.0 means no data or a perfect split). Reuses
`research/regime.py`'s `classify_trend` per timeframe — the same
function `regime.py` already uses for its own single-timeframe trend
signal — rather than inventing a second trend definition; its
"sideways" maps onto `TrendState.NEUTRAL` (`context/models.py`'s
existing enum, previously only referenced by the still-stubbed
`trend_state` field). A caller supplies one bar series per timeframe
via `bars_by_timeframe`, entirely independent of `ContextEngine`'s own
`symbol`/`timeframe` — a caller wanting its own timeframe counted
includes it under the matching key itself.

Look-ahead safety here is stricter than any single-stream classifier in
this package: it is realistic for a caller to hand over a coarser
timeframe's series where the *last* bar is still forming (e.g. at 09:05,
a 1-hour series' 09:00 bar has opened but not closed) even though its
timestamp alone looks like "at or before now". A plain `bar.timestamp <=
now` check would wrongly accept that bar. `timeframe.py` instead knows
each timeframe's actual duration and only keeps a bar once
`bar.timestamp + duration <= timestamp` — its close time has genuinely
passed — before handing anything to `classify_trend`; verified directly
by a dedicated test that constructs exactly that in-progress-bar
scenario. A timeframe missing from the mapping, empty, or with fewer
than two completed bars is simply left out of `alignment` — never a
fabricated direction or an error.

**Market Structure Context (`structure.py`, 2026-07-27):**
`analyze_structure` detects price structure from confirmed swing points:
higher-highs/higher-lows (`TrendState.BULLISH` structure) or
lower-highs/lower-lows (`TrendState.BEARISH`), the nearest support/
resistance levels around the current price, and distance from them.
Strictly descriptive — `StructureContext` carries no broker/risk-
manager/engine reference of any kind, and this module never generates a
trade or overrides a strategy's own signal (the same hard boundary every
other file in `context/` is held to). No existing equivalent to reuse in
this codebase (same disclosure `regime.py` gives for liquidity/risk) —
genuinely new work, though it reuses `TrendState` (`context/models.py`,
already used by `timeframe.py`) rather than a fourth bullish/bearish/
neutral vocabulary.

A swing high/low is confirmed via a standard fractal definition: a bar's
high (low) must be strictly greater (less) than every high (low) within
`DEFAULT_SWING_WINDOW` (3) bars on *both* sides. This does require
looking at bars chronologically after a candidate swing point — but
since every bar this module ever sees is already-completed history (the
same "bars up to and including the bar that just closed" convention
every classifier in this package holds callers to), this is confirmation
*lag*, not a look-ahead violation: those later bars are themselves
already in the past relative to `timestamp`. The practical effect is
that the most recent few bars simply won't have a confirmed swing near
them yet, verified directly by a dedicated test. `support`/`resistance`
are the nearest confirmed swing low/high bracketing the current price
(falling back to the most recent swing if price has broken through every
level); `distance_to_support`/`distance_to_resistance` are the plain
price differences. Fewer than `2 * DEFAULT_SWING_WINDOW + 1` bars, or no
confirmed swings at all, returns `UNKNOWN`/`None` throughout, never an
exception.

**Market Regime Detection (`regime.py`, 2026-07-27):** `classify_regime`
classifies overall market behavior into one of five mutually exclusive
`MarketRegime` values — `TRENDING_UP`, `TRENDING_DOWN`, `RANGING`,
`HIGH_VOLATILITY`, `LOW_VOLATILITY` (redefined this phase from Phase 1's
placeholder set `TRENDING`/`RANGING`/`VOLATILE`; confirmed zero usages
outside `context/`'s own tests before changing it, same discipline as
`SessionPhase`'s Phase 2a rename). Combines three signals, each reused
rather than re-derived: `strategy.indicators.adx` for trend *strength*
(the conventional ADX ≥ 25 "actually trending" threshold, Wilder's own
convention, not tuned for this codebase), `research/regime.py`'s
`classify_trend` for trend *direction* (bullish/bearish/sideways —
already look-ahead-safe, already used for this exact purpose elsewhere),
and this package's own `volatility.analyze_volatility` for the
volatility signal (inheriting its look-ahead safety for free). Priority
when signals disagree, documented explicitly rather than left implicit:
extreme volatility dominates trend/range labeling; otherwise a strong,
directional ADX reading wins; otherwise low volatility is its own label;
otherwise the default is `RANGING`. `confidence` is always in `[0.0,
1.0]` via a small, documented formula per branch (e.g. trending
confidence is `min(1.0, adx / 50.0)` — chosen so the task's own worked
example, ADX 39, lands on exactly 0.78) — no parameter optimization this
phase, every threshold is either reused from elsewhere in this codebase
or an unmodified textbook default.

**Volatility Context (`volatility.py`, 2026-07-27):** `analyze_volatility`
reuses `strategy/indicators.py`'s `atr_series` (the same Wilder's-smoothing
ATR every strategy already uses) rather than re-deriving true-range math.
`current_atr` is the last value of that series; `average_atr` is the mean
of a trailing window of ATR values ending at that same last value (default
20, a documented/overridable constant, not a magic number); `volatility_ratio
= current_atr / average_atr` is classified into `VolatilityState` via fixed
thresholds (`<0.75` LOW, `[0.75,1.25)` NORMAL, `[1.25,2.0)` HIGH, `>=2.0`
EXTREME — chosen so the task's own worked example, ratio 1.5, lands on
HIGH). `realized_volatility` (stdev of simple close-to-close returns over
the same trailing window, unannualized) is new — no prior equivalent
existed. Missing/insufficient history (fewer than `atr_period + 1` bars)
returns every numeric field as `None` with `state=UNKNOWN`, never an
exception or a fabricated value.

Deliberately **not** reused as-is: `research/regime.py`'s
`classify_volatility`/`compute_regimes`, even though it already does
ATR-based volatility bucketing. It computes its low/high tercile cutoffs
with `sorted()` over the *entire* `bars` series passed to it, up front —
correct for its own post-hoc, read-only trade-labeling use case, but not
look-ahead-safe if reused verbatim for real-time classification "as of
timestamp T" (a trade at T would be labeled relative to volatility that
hasn't happened yet). `analyze_volatility` instead only ever reads a
*trailing* window ending at the last bar it's given, so a caller that
(per this codebase's established convention — see `Strategy.on_bar` and
`ContextEngine.build_context`) only ever passes bars up to "now" can never
leak a future ATR value into the result — verified directly by
`tests/test_context_volatility.py`'s `TestNoFutureDataLeakage` (a
truncated-history reading is provably unaffected by bars appended after
it). The output shape also differs on purpose: a ratio-based four-state
result, not tercile buckets over a whole dataset.

`session.py`'s seven `SessionPhase` values (`OVERNIGHT`, `PRE_MARKET`,
`OPENING_RANGE`, `MORNING_SESSION`, `LUNCH_SESSION`, `POWER_HOUR`,
`MARKET_CLOSE`) reuse three existing conventions rather than inventing new
boundaries: 08:30 CT as the RTH open (agreed upon identically by
`research/regime.py`'s own bucket table and
`strategy/opening_range_breakout.py`'s `session_start_ct` default),
`research/regime.py`'s exact RTH bucket boundaries (reused verbatim, not
re-derived), and `contracts.py`'s exact `SESSION_OPEN`/`SESSION_CLOSE`/
`in_maintenance_halt` (`MARKET_CLOSE` *is* the maintenance halt). The one
new boundary, `PRE_MARKET`'s start, has no existing precedent to reuse and
is a documented, overridable parameter (default 08:00 CT), not a hardcoded
literal. Weekends/holidays aren't an eighth "closed" phase — they classify
as `OVERNIGHT` with `is_market_open=False` and `liquidity_expectation="NONE"`
as the unambiguous "actually closed" signal.

A real bug was found and fixed while building this: the first
implementation measured `minutes_since_open` using `contracts.session_date()`,
which attributes a maintenance-halt moment (16:00–17:00 CT) to the
*upcoming* session — the wrong reference point for elapsed-minutes math
during the halt itself (it produced `minutes_since_open=0` at 16:30 CT
instead of 30). Fixed by computing session start directly (the most recent
17:00 CT at or before the moment), independent of `session_date()`'s
kill-switch-oriented semantics. Covered by a dedicated regression test
(`tests/test_context_session.py`).

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

**Reuse, don't duplicate — final state as of Phase 8:** every dimension
that could reuse existing logic does. `research/regime.py`'s
`classify_trend` is reused by three modules now (`regime.py`,
`timeframe.py`, `trend.py`); `strategy.indicators.adx` by two
(`regime.py`, `trend.py`); `strategy.indicators.atr_series` by one
(`volatility.py`, which `regime.py` then reuses in turn rather than
recomputing ATR itself); `strategy.indicators.sma` by one
(`liquidity.py`). `risk.py` reuses no market data at all — it's a pure
composite of `volatility_state`/`market_regime`, both already real.
Only `structure.py` (swing/support-resistance) and `liquidity.py`
(relative-volume classification) are genuinely new logic, and both say
exactly why in their own module docstrings.

**Shared-computation reuse (Platform Verification Phase 2, 2026-07-27):**
"reused logic" above was always true at the *algorithm* level (`adx`/
`analyze_volatility` each have exactly one implementation), but
`ContextEngine.build_context` was independently *invoking* `adx()` from
both `regime.py` and `trend.py`, and `analyze_volatility()` from both
itself and (internally) `regime.py` — two calls per bar for each,
confirmed by `cProfile` to be ~90% of context-generation CPU time (see
`docs/PLATFORM_VERIFICATION_PHASE1.md`). `regime.classify_regime`/
`trend.analyze_trend` now accept optional `precomputed_volatility`/
`precomputed_adx` arguments (sentinel-defaulted so every caller that
doesn't pass them — every existing test, every future standalone use —
is unaffected); `build_context` computes both exactly once per bar and
threads them through. See `docs/PLATFORM_VERIFICATION_PHASE2.md` for the
full before/after measurement.

**Configuration system (Phase 8):** `scoring.ScoringConfig` centralizes
every scoring weight; see "Context Scoring System" above.
`ContextEngine.__init__`'s `scoring_config` parameter (default `None` →
`scoring.DEFAULT_SCORING_CONFIG`) is the only other configuration
surface in the engine — every classifier's own tunables
(`atr_period`, `swing_window`, `average_lookback`, etc.) remain plain
keyword arguments on their respective `analyze_*`/`classify_*`
functions, documented and overridable per-call, not centralized (they
were never asked to be, and centralizing them would mean threading
per-dimension config through `ContextEngine` for no requested benefit).

**Validation guarantees (Phase 8, Part 3):** no circular imports (every
`context/` submodule imports standalone), no duplicated ATR/ADX/SMA/
trend-direction math, no duplicated CME calendar/session/regime/
volatility-state definitions, deterministic output for identical input
(no wall-clock reads, no randomness anywhere in the package), missing
data always handled safely, `UNKNOWN` states always carry zero
confidence and no fabricated reason/value, and every confidence value
across every dimension stays in `[0.0, 1.0]` — all encoded as executable
tests in `tests/test_context_engine_validation.py`, not just claimed.
The dependency direction between `context/` and the trading side is now
**one-directional by design, not absent**: `engine.py` imports `context/`
for real (the integration point); `context/` still has, and must always
have, zero reference back to `engine.py`/`risk/manager.py`/`brokers/` —
verified in both directions by
`tests/test_context_engine_validation.py`'s
`TestModulesRemainIndependentFromTheTradingSide` and
`tests/test_engine_context_integration.py`'s `TestNoCircularImports`.
`strategy/base.py`'s own reference is `TYPE_CHECKING`-only (never a real
import), verified directly by `tests/test_context.py`.

**Known limitations:** no database persistence (a schema change needs
explicit approval per `CLAUDE.md` section 8, and isn't decided); four
dimensions (`volatility`/`regime`/`trend`/`timeframe`) plus `structure`
are O(n) in the number of bars passed per call — now a real, measured
cost in `ContextMode.OBSERVE`/`ENABLED` (see "Performance impact"
above and `docs/CONTEXT_ENGINE_PERFORMANCE_BENCHMARK.md`), bounded by
`TradingEngine`'s existing bar-retention window rather than backtest
length, but non-zero; `TrendState` and `MarketRegime` are two
independent trend readings that can legitimately disagree (by design,
not a bug — see `trend.py`'s docstring); `ScoringConfig`'s default
weights are illustrative, not derived from any backtest of which
dimensions actually predict performance; `backtest/context_comparison.py`'s
trade diff has a path-dependence caveat (see that module's own
docstring) — only the first divergence between two runs is guaranteed
to be directly explained by the strategy's own context rule.

**Integration status:** complete for the mechanism (`ContextMode`,
`Trade.entry_context`, `Strategy.context`/`uses_context`, the A/B
comparison framework) — see "Integration into `TradingEngine`" above.
**Not decided:** whether/how `EnvironmentScore` should influence
position sizing or trade filtering for any *specific* bundled strategy
(a strategy-level decision for each strategy to make individually, not
something `context/` or the engine should ever decide on a strategy's
behalf), and whether `MarketContext`/`EnvironmentScore` snapshots get
persisted to a database for research (a schema change, needs its own
explicit approval per `CLAUDE.md` section 8).

## Team Collaboration Platform (Active Work Registry MVP + SIL Phase 2 "Workflow Integration" + User Registration & Organization Management, all 2026-07-28)

A layer alongside (not inside) the trading pipeline above -- it coordinates
*people and AI sessions working on this codebase*, not market data or
trades. Lives in `accounts/` (users/organizations/roles/permissions, no
backend auth yet), `collaboration/` (everything below), and
`frontend/src/session.tsx` (a frontend-only "current user" concept),
surfaced through `api/routes/accounts.py`/`collaboration.py` and Mission
Control's `CollaborationWorkspace` panel plus the Welcome/Register/
Profile/Organization Settings/Team Members pages.

```
accounts/store.py, pg_store.py        users / organizations tables. Four
  |                                    fixed roles (owner/admin/member/
  |                                    viewer). Every user gets an
  |                                    auto-generated api_key at creation
  |                                    (fbot_-prefixed) -- a placeholder
  |                                    for future auth, not checked
  |                                    against anything today. Profile
  |                                    fields (timezone, preferred AI
  |                                    model, default branch prefix,
  |                                    notification preferences) stored
  |                                    even where nothing consumes them
  |                                    yet.
  v
accounts/permissions.py               A flat role->capability table
                                       (manage_organization/manage_members/
                                       manage_work/view). Not enforced
                                       server-side -- there's no request-
                                       level identity to check it against
                                       yet -- consulted by the frontend
                                       only, to decide what to show/hide.
                                       Mirrored by hand in
                                       frontend/src/types.ts's ROLE_
                                       CAPABILITIES rather than fetched
                                       over the network.

frontend/src/session.tsx              A frontend-only "current user"
                                       concept: a user id in localStorage,
                                       resolved into a real user/org via
                                       GET /api/users/{id}/me. Explicitly
                                       a UX convenience (don't make me
                                       re-pick myself every visit), never
                                       a security boundary -- anyone with
                                       devtools access can change the
                                       stored id. RequireSession gates
                                       every in-app route behind having
                                       registered/picked an account once.

collaboration/store.py, pg_store.py   work_items / work_item_activity tables.
  |                                   Lifecycle: planned -> claimed ->
  |                                   in_progress -> testing ->
  |                                   ready_for_review -> merged -> completed
  |                                   (advisory, not enforced -- a review can
  |                                   send work backward). owner_type
  |                                   (human/ai) distinguishes an AI
  |                                   session's own claimed work. org_id
  |                                   (nullable) scopes a work item to one
  |                                   organization -- None means "no
  |                                   filter" everywhere (a session-less
  |                                   caller like the CLI still sees
  |                                   everything), a real org_id keeps two
  |                                   unrelated orgs sharing one instance
  |                                   from warning each other about
  |                                   coincidental file-path matches.
  v
collaboration/overlap.py (V1)         Exact file-path overlap between a
collaboration/overlap_v2.py (V2)      proposed task and every other active
                                       item. V1: file paths only, four risk
                                       buckets. V2 (additive, V1 untouched):
                                       also compares Python/TS imports, API
                                       route paths, DB table names, frontend
                                       component names, config files, and
                                       title/description keywords -- one
                                       explainable 0-100 confidence score
                                       with a factor breakdown, never a
                                       black-box number. Both are warn-only:
                                       neither ever blocks a claim, a task,
                                       or a merge.
  v
collaboration/git_info.py             Live, read-only git introspection
                                       (current branch, ahead/behind vs a
                                       base branch, last commit) via `git`
                                       subprocess calls. Nothing persisted --
                                       recomputed on every request, so it
                                       can't go stale. Best-effort: a
                                       detached HEAD or missing base branch
                                       degrades to `None`/explanatory notes,
                                       never an exception.
  v
collaboration/merge_readiness.py      One explainable 0-100 score from
                                       Overlap V2's risk, branch age,
                                       behind-base count, and change size.
                                       test_status is always the literal
                                       "unknown" -- no CI integration exists
                                       to read a real pass/fail from, and
                                       guessing would be worse than admitting
                                       that gap.
  v
collaboration/timeline.py             Merges work_item_activity with real
                                       git commits into one searchable,
                                       filterable feed -- Mission Control's
                                       "Recent Activity" / project timeline.
```

**Why warn-only, everywhere.** Every check in this layer (V1/V2 overlap,
merge readiness, the pre-work-check) produces information, never a lock or
a block. A small team (or a small team plus AI sessions) needs coordination
signals, not a gate that can wrongly stop real work -- see `overlap.py`'s
own docstring for the original rationale, which every later addition here
deliberately preserved rather than "upgraded" into something stricter.

**Why `test_status` is always `"unknown"`.** Reading a real pass/fail would
need actual CI integration (a workflow-run API, or executing the suite and
blocking on it) -- a separate, larger effort. Reporting a guess dressed up
as a real status would be worse than admitting the gap; see
`merge_readiness.py`'s own docstring.

**What's still out of scope**, in increasing order of effort: real
authentication for `accounts/`/`collaboration/` (every user now has an
`api_key`, but nothing checks it against anything -- every route is still
reachable by anyone who can reach the port, and the frontend's "current
user" is just a `localStorage` value; validating `api_key` as a bearer
token is the smallest next step given it already exists, full session/
login semantics the larger version); a true architecture/dependency
graph (Overlap V2's import/route/table signals are real but shallow --
still not a graph that understands *indirect* impact); AI-assisted semantic
merge conflict resolution; a persistent AI-worker execution layer (this
package tracks and coordinates work, it does not run it -- an "AI worker"
today is a Claude Code session following CLAUDE.md section 6's step 7, not
a daemon claiming work on its own); a distributed worker network. See
ROADMAP.md's "Future" section for the fuller list.

## SIL Phase 4 "Intelligent Automation Layer" (2026-07-29)

Built directly on the Collaboration Platform above -- no redesign, no new
schema pattern beyond one additive column. Two pieces: aggregation (the
context bundle, the local validation command, the documentation draft
assistant -- read existing systems, never write) and two background
schedulers (the git-watcher, the maintenance job -- write, but narrowly:
only ever create/discard their own `is_draft=True` work items).

```
collaboration/context_bundle.py       build_context_bundle() -- one call
                                       aggregating active work items,
                                       similar past work (keyword-matched
                                       against every work item ever, not
                                       just active ones), recent commits,
                                       branch info, Overlap V2 warnings,
                                       and relevant KNOWN_ISSUES/ROADMAP
                                       excerpts (plain substring search,
                                       not semantic). Pure aggregation of
                                       systems above -- no new persisted
                                       index, no architecture graph.
                                       POST /api/collaboration/context-
                                       bundle, `work_item_cli.py context`.

collaboration/git_watcher.py          GitWatcherScheduler -- same daemon-
  |                                   thread shape (threading.Lock+Event)
  |                                   market_data/scheduler.py::
  |                                   MarketDataScheduler already
  |                                   establishes. Every cycle: reads
  |                                   git_info.changed_files() (git status
  |                                   --porcelain --untracked-files=all),
  |                                   subtracts files covered by any
  v                                   REAL (non-draft) active work item,
work_items.is_draft                   and drafts exactly one work item for
                                       what's left -- self-healing (a
                                       changed file set that grows/shrinks
                                       discards the old draft and creates
                                       a fresh one) and idempotent (an
                                       unchanged set is a no-op). Never
                                       touches a non-draft item; never
                                       auto-approves. approve_draft_work_
                                       item/discard_draft_work_item are
                                       the only ways a draft's `is_draft`
                                       flag changes.

collaboration/maintenance.py          MaintenanceScheduler -- same shape,
                                       longer interval. Discards drafts
                                       untouched for
                                       automation.stale_draft_days
                                       (inclusive at the cutoff) and
                                       checks DB connectivity via
                                       db.health.check_database_health()
                                       (imported lazily -- see
                                       KNOWN_ISSUES.md ISSUE-032 for why
                                       that matters here specifically).
                                       Only ever reads
                                       fetch_draft_work_items() as discard
                                       candidates -- a real item's age is
                                       never relevant.

collaboration/git_sync.py             GitSyncScheduler (2026-07-29) -- same
                                       shape again, pull-only. Skips
                                       entirely on a dirty working tree
                                       (never touches uncommitted work),
                                       fetches the configured remote, and
                                       fast-forwards ONLY when local HEAD
                                       is a real ancestor of the
                                       remote-tracking ref -- a diverged
                                       history (unpushed local commits) is
                                       reported, never merged/rebased/
                                       forced. Never pushes, under any
                                       code path -- separate opt-in
                                       (automation.git_sync_enabled) from
                                       enabled above, since this is the
                                       one automation scheduler that
                                       writes to the real working tree,
                                       not just draft metadata.

tools/local_validate.py               Maps uncommitted changes (git_info.
                                       changed_files(), reused) to their
                                       likely test files -- most follow
                                       test_<module>.py or
                                       test_<package>_<module>.py, but not
                                       all do. Falls back to the FULL
                                       backend suite when the mapping is
                                       incomplete, rather than silently
                                       under-testing. Also runs
                                       tsc-b/oxlint/vitest when
                                       frontend/src changed.

tools/draft_changelog.py              Drafts a CHANGELOG.md-style entry
                                       from every commit + completed/
                                       merged work item since CHANGELOG.md's
                                       own last commit (git_info.
                                       last_commit_touching/commits_since).
                                       Writes to .changelog_draft.md
                                       (gitignored) for a human to review
                                       and rewrite into this repo's
                                       narrative prose style -- never
                                       edits CHANGELOG.md itself.
```

**Why all three schedulers default to disabled.** `automation.enabled`
and `automation.git_sync_enabled` in `config.yaml` both default `False`
-- same "zero behavior change unless deliberately opted into" convention
`research_server.enabled`/`live_feed` already establish, and for a
concrete, specific reason here: this codebase's test suite calls
`create_app()` (directly, or via `TestClient`) well over a thousand
times across its ~1600 tests. If any scheduler defaulted to running,
every one of those calls would start a real daemon thread hitting this
repo's own real `git status`/`git fetch` and writing real `is_draft` rows
(or, for git-sync, attempting a real merge) into whatever repo/database
that test happened to be pointed at -- nondeterministic test pollution,
not a hypothetical. `git_sync_enabled` is checked independently of
`enabled` (not nested under it) precisely so turning on one doesn't
silently turn on the other -- they have different risk profiles (draft
metadata writes vs. a real working-tree merge).

**Why a draft is never auto-approved.** SIL Phase 4's own automation
rules (safe, reversible, logged, explainable, configurable, never
destructive) rule it out by design -- a draft is inert until a human or
an AI-assisted session explicitly reviews it via
`approve_draft_work_item`/`discard_draft_work_item` (API or
`work_item_cli.py approve-draft`/`discard-draft`). The dedup/supersede
logic in `git_watcher.py::_reconcile` only ever operates on
`is_draft=True` items for exactly this reason -- a real, approved work
item is structurally unreachable from that code path.

**Five real bugs found and fixed while building this**, all caught
before shipping (three via the very tests being written, one via
`local_validate.py`'s own first real use, one via running the suite
under an interpreter lacking the `db` extra) -- see KNOWN_ISSUES.md
ISSUE-029 through ISSUE-033 for full detail on each.

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
