# Research Workstation (Phase 6B)

Phase 6A built a research dashboard: run a backtest, see the result. Phase
6B upgrades it into a workstation: long operations run in the background
with live progress, trades carry MAE/MFE and market-regime labels, the
optimizer shows *where* a parameter region is robust rather than just its
single best point, and findings can be tracked as named experiments instead
of living only in your head. Everything here is additive — see
[docs/RESEARCH_INTERFACE.md](RESEARCH_INTERFACE.md) for the Phase 6A
architecture this builds on; nothing described there changed.

## New features

### 1–2. Background jobs and real-time progress

`POST /api/jobs/{backtest,optimizer,compare,report}` queue work on a
process-wide thread pool (`api/jobs.py`) and return immediately with a job
id. `GET /api/jobs/{id}` polls status; `GET /api/jobs/{id}/stream` opens a
Server-Sent Events connection that pushes one frame per progress update and
closes when the job reaches `completed`/`failed`. The **Jobs** page lists
history and (via SSE) shows a live-updating progress bar for an in-flight
job.

Progress itself comes from two small, additive hooks:

* `backtest.runner.run_backtest(..., progress_callback=...)` — called
  roughly every 1% of bars processed (never more than ~100 times
  regardless of dataset size).
* `research.optimizer.run_optimization(..., progress_callback=...)` —
  called after each parameter combination, carrying the current best
  score/params seen so far.

Both parameters default to `None` and are only used by the new job path —
`services.run_backtest_job`/`run_optimizer_job` (and therefore
`POST /api/backtest/run`/`POST /api/optimizer/run`, the Phase 6A synchronous
endpoints) call the exact same underlying functions unmodified.

**Why threads, not `asyncio`:** the whole `services.py` call chain is
synchronous, CPU-bound Python (not I/O-bound) — rewriting it as `async def`
top to bottom would touch every layer for a single-user local tool where a
4-worker thread pool is already enough concurrency. See `api/jobs.py`'s
module docstring for the full reasoning, and `api/store.py`'s (Phase 6A) for
why every job worker opens its own `TradeStore` connection rather than
sharing one across threads.

### 3. Advanced trade analytics (MAE/MFE)

`api/analytics.py::compute_excursions` re-scans the bar window between each
trade's entry and exit (bars the backtest already replayed, no engine
changes needed) to compute:

* **MFE** (maximum favorable excursion) — the best price ever available
  before exit, in points.
* **MAE** (maximum adverse excursion) — the worst price ever available.
* **Efficiency** — realized points ÷ MFE points: how much of the available
  favorable move the exit actually captured.

`GET /api/trades/analytics` groups stored trades into three views:

* **Best entries** — highest efficiency.
* **Poor exits** — lowest efficiency among trades that *had* a real
  favorable move available (the entry was fine; the exit gave it back).
* **Missed opportunities** — losing/scratch trades where a meaningful
  favorable move (>= 2 points) existed before it reversed.

The Trade Explorer's pill filters switch between these views and the full
trade list; the detail panel shows MAE/MFE/efficiency for any selected
trade.

### 4. Market regime analysis

`api/regime.py` classifies every trade at entry time along three axes,
purely for grouping — none of it feeds back into any trading decision:

* **Trend** — bullish/bearish/sideways, from the percentage move over the
  last 20 closes before entry.
* **Volatility** — low/medium/high, from where that bar's ATR(14) falls
  relative to the *whole dataset's* ATR distribution (terciles) — "high"
  is always relative to this contract/period, not a fixed absolute number.
* **Session** — open (08:30–09:30 CT) / morning (09:30–11:00) / lunch
  (11:00–13:00) / close (13:00–16:00) / overnight.

`GET /api/regime/performance?strategy=X` groups a strategy's stored trades
by each label and reports net P&L, win rate, and average efficiency per
bucket. The **Market Regime** page renders this as three tables — "when
does this strategy actually work?" made queryable instead of eyeballed.

### 5. Optimizer heatmap

`ParameterHeatmap` (frontend-only, `frontend/src/components/ParameterHeatmap.tsx`)
reads the *full* trial set from `GET /api/optimizer/results/{batch_id}`
(every combination tried, not just the top-N ranked ones — `TradeStore`
already persisted all of them in Phase 3) and renders a colored grid for
any two swept parameters: average training net P&L per cell, red (worst) to
green (best). A wide patch of similar color is a robust region; one bright
cell surrounded by dull ones is an isolated spike — the same "isolated
spike" signature `research.safety.check_parameter_fitting` already flags
in text, shown here visually. No new backend endpoint was needed for this
— the data was already there.

### 6. Research experiment tracking

`research/trade_store.py`'s new `experiments` table (additive, alongside
Phase 6A's `runs`/`reports`) stores a name, hypothesis, strategy, dataset,
parameters, an optional link to a backtest run, and free-text notes.
`POST /api/experiments`, `GET /api/experiments[/{id}]`,
`PATCH /api/experiments/{id}/notes`. The **Experiments** page is a simple
log: write down what you're testing and why *before* you run it, link the
run once it exists, and record what you actually learned.

### 7. Dashboard intelligence

`services.generate_insights()` is rule-based, not a language model — every
insight is a direct, mechanical read of numbers this API already computes
(commission drag, weekday/session P&L skew, the latest optimizer's
train-vs-validation collapse), the same figures `backtest.metrics` and
`research.safety` already surface elsewhere. An insight always names the
strategy and the number behind it (see `api/services.py`'s
`_commission_drag_insight`/`_weekday_skew_insight`/`_session_skew_insight`/
`_overfit_insight` for the exact rules and thresholds). Nothing here can
produce a finding that doesn't trace back to a specific query result —
that constraint is deliberate: a "insight" that can't be traced to a number
is a bug, not a feature. `GET /api/insights`, rendered at the top of the
Dashboard.

## Architecture

```
Existing Phase 6A pieces (unchanged):
  React SPA -> FastAPI routes -> services.py -> backtest/research packages -> TradeStore

Phase 6B additions:
  api/jobs.py          Background execution + progress (thread pool + jobs table)
  api/analytics.py     MAE/MFE computation from replayed bars
  api/regime.py        Trend/volatility/session classification
  routes/jobs.py        POST/GET /api/jobs/*, SSE stream
  routes/experiments.py POST/GET/PATCH /api/experiments/*
  routes/trades.py     + /api/trades/analytics, /api/regime/performance  (extended)
  routes/system.py     + /api/insights  (extended)

research/trade_store.py: additive schema only --
  - `trades` gained 6 nullable columns (mfe_points, mae_points, efficiency,
    regime_trend, regime_volatility, regime_session) via an ALTER TABLE
    migration in ensure_schema(), not a new table -- old rows read back
    with None for these, no data loss, no version flag needed.
  - New tables: `jobs`, `experiments`.

research/features.py: `TradeRecord` gained the same 6 fields (all
Optional, defaulting to None) and `build_trade_records` gained optional
`excursions`/`regimes` parameters -- every existing caller that doesn't
pass them is unaffected (tests/test_research_features.py,
tests/test_trend_pullback_analytics.py's own independent
build_trade_records both still pass unmodified).

backtest/runner.py `run_backtest` and research/optimizer.py
`run_optimization`: each gained one optional `progress_callback`
parameter, `None` by default, called nowhere unless a caller supplies it.
```

## Research workflow with the new tools

1. **Form a hypothesis** on the Experiments page before running anything --
   "I expect VWAP reversion to do better in high volatility." Save it.
2. **Run the backtest in the background** from the Backtest Launcher
   (checkbox: "Run in background with live progress") so you can watch
   progress rather than stare at a blocked request, and so you can queue a
   second one while the first runs.
3. **Check Market Regime** for that strategy -- does the volatility
   breakdown actually support the hypothesis, or refute it?
4. **Check Trade Explorer's "Poor exits" / "Missed opportunities" views**
   if the strategy's entries look right but P&L doesn't match -- MAE/MFE
   will show whether the exit logic is the problem.
5. **Sweep parameters with the Optimizer**, in the background, and read the
   heatmap before trusting the single best cell -- a robust region beats a
   lucky point every time (see [docs/RESEARCH_GUIDE.md](RESEARCH_GUIDE.md)'s
   overfitting section, which this heatmap is a visual companion to).
6. **Go back to the Experiment** and record what you actually found in the
   notes field, whether it confirmed the hypothesis or not -- a null result
   recorded is still research; an unrecorded result is just a thing that
   happened once.
7. **Glance at the Dashboard's insights** periodically -- they're the same
   underlying checks, surfaced without you having to go looking.

## Testing

`pytest`: 61 new backend tests this phase (jobs: 10 unit + 12 route;
analytics: 6; regime: 11; trade-store schema/jobs/experiments: 26;
services-level integration for all of the above; progress-callback
regression tests for both `run_backtest` and `run_optimization`) — **538
total, all passing**, including every Phase 1–6A test unmodified.

`npm test` (frontend): 10 new component tests (`JobProgressBar`,
`ParameterHeatmap`) — **26 total, all passing**. `npm run build` typechecks
and bundles cleanly.

## Known gaps / honest limitations

- **No auth**, same as Phase 6A — still a localhost/trusted-network tool.
- **The optimizer's `Confidence`/`Warnings` summary (from `research.safety`)
  isn't retrievable after a background job completes** — only the trial
  table and heatmap are, since the `SafetyReport` itself isn't persisted to
  the `runs` table (only `BacktestMetrics.caveats()` is). The synchronous
  `POST /api/optimizer/run` path still shows it in full. Phase 6C candidate:
  persist the safety report's confidence/findings alongside the run.
- **Session-skew insight compares only the worst session**, not a full
  per-session significance test — it's a heuristic pointer, not a
  statistical claim, and is worded that way in `_session_skew_insight`.
- **The job thread pool is process-local and in-memory** (`_MAX_WORKERS =
  4`); restarting the API process loses track of any job still `running`
  (its row stays `running` forever in `jobs` — a stuck-looking row, not
  silent data loss, since the underlying backtest/optimizer result, if it
  had completed, would still be in `runs`). A production job queue (e.g.
  persisted task state with a supervisor) is a Phase 6C-or-later concern if
  this tool ever needs to survive process restarts mid-job.

## Recommended next phase

1. **Persist optimizer confidence/findings** to `runs` so background
   optimizer jobs show the full safety report, closing the gap above.
2. **ML model training**, building on Phase 6A's `/api/ml/dataset` and this
   phase's regime/efficiency labels as features — the dataset is now rich
   enough (MAE/MFE, regime labels, entry indicator snapshots) to train a
   real classifier on win/loss, not just export a CSV.
3. **Job cancellation** — `POST /api/jobs/{id}/cancel`, useful once
   optimizer sweeps get large enough that a mis-configured grid is worth
   stopping early rather than waiting out.
4. **Correlation/feature-importance view** on the ML Research page (a
   Phase 6A-noted gap, more valuable now that regime labels give it more to
   correlate against).
