# Autonomous Research Server (Phase 8B)

Phase 8A built a self-syncing local market-data database with contract
auto-detection and rollover. Phase 8B is the orchestration layer on top of
it: turn on `research_server.enabled` in `config.yaml` and the platform
keeps its own data current, paper-trades several strategies at once,
runs research on a nightly schedule, and surfaces (never applies) findings
about drift and better parameters — with no one clicking anything after
the API process boots.

Everything here is additive and **opt-in, off by default**
(`research_server.enabled: false`). Anyone running the plain research
dashboard sees zero behavior change unless they deliberately turn it on.

## Enabling it

```yaml
research_server:
  enabled: true
  paper_strategies: [ema_crossover, trend_pullback]  # strategy names to auto-paper-trade concurrently
  resolution: 5min
  poll_seconds: 30
  data_sync_products: [MES]        # product codes MarketDataScheduler keeps synced
  nightly_job_hour_ct: 2            # 24h Central Time
  weekly_report_weekday: 6          # Sunday (Monday=0 .. Sunday=6)
```

With `MASSIVE_API_KEY` set in the environment, the API auto-starts three
background systems on boot (FastAPI's lifespan handler in `api/app.py`)
and stops them cleanly on shutdown. If the key isn't set, autonomous mode
logs a warning and simply doesn't start — the rest of the dashboard still
boots normally. The **Research Server** page can also start/stop it by
hand for the current process regardless of the config flag.

## What actually runs

### 1. Market data sync

The exact same `market_data.scheduler.MarketDataScheduler` from Phase 8A
— not a second instance. The Research Server and the **Market Data**
dashboard page's manual controls share one singleton, so starting the
research server never conflicts with (or silently duplicates) a scheduler
someone already started by hand; stopping the research server only stops
the scheduler if the research server was the one that started it.

### 2. Multi-strategy autonomous paper trading (`research_server/paper_trader.py`)

`AutonomousPaperTrader` runs one `TradingEngine` + `PaperBroker` +
`RiskManager` **per strategy** in `paper_strategies`, each with its own
isolated risk/kill-switch state (its own state file) — one halted strategy
never affects another — but all of them share **one** live bar poll, so N
strategies cost one Massive API call per cycle, not N.

Contract auto-detection reuses Phase 8A directly:
`MassiveContractsClient.active_contract` picks the front-month ticker at
start and is re-checked once a day; nobody ever types a specific expiry
symbol into config, and a detected roll is logged through the same
`contract_rolls` history the data scheduler writes to.

Safety is structural: `research_server.paper_strategies` has no field
anywhere that names a broker, so this code path can never construct
anything other than the paper broker — the same guard
`api.live_session.LiveSessionManager.start()` uses is checked here too,
belt and suspenders.

Trade persistence reuses `live_trade_journal.LiveTradeJournal` (moved to a
shared top-level module in this phase, alongside `research.regime`, so
neither the manual Live Session nor the autonomous trader has to depend on
the other) — one `runs` row (`kind="live"`) per strategy, and (new in this
phase) each live trade gets a market-regime label
(`research.regime.compute_regimes`), which `insights.py`'s regime-drift
check needs.

**Simplification, stated plainly**: `Settings.strategy_params` is a single
dict tied to `Settings.strategy_name`. Whichever entry in
`paper_strategies` matches `strategy_name` runs with those params; every
other entry runs with its own class defaults. A richer per-strategy
parameter config is a natural follow-up, not built here.

### 3. Nightly (and weekly) research (`research_server/nightly_jobs.py`)

Submits research through the **existing** job system
(`api.jobs`/`api.services`) rather than reinventing execution — every
submission targets dataset `db:{contract}:{resolution}` (Phase 8A), so
nightly research always runs against whatever's freshly synced.

Once a day, at `nightly_job_hour_ct` CT, for each `paper_strategies`
entry: a backtest, a (non-rolling) optimizer sweep, and a walk-forward
run, followed by a generated report for the walk-forward result. On
`weekly_report_weekday`, also a full comparison across every configured
strategy.

Idempotent by construction: a background thread wakes every ~60s and
compares the current CT date against an in-memory `last_run_date` — a
match means today's batch already went out. A restart before the trigger
hour just re-arms; a restart after it already fired doesn't resubmit,
because the results already live in `jobs`/`runs`.

The optimizer step sweeps whatever `strategy_params` a strategy already
has configured — the same "list-valued entries are swept, scalars stay
fixed" rule the manual Optimizer page already uses. No grid is
auto-generated.

### 4. Findings (`research_server/insights.py`)

Same rule Phase 6B's dashboard insights already follow: every finding is
a direct read of a number already computed and stored, never a new
statistical model. Computed on demand by `GET /api/research-server
/findings`, not a background job — there's no persisted "discoveries" log
to keep consistent.

- **Degradation** — the strategy's latest completed live run's expectancy
  vs. its own best historical backtest/walk-forward expectancy. Flags only
  the clear case: live negative while the historical best was positive.
- **Regime drift** — live trades occurring in a trend/volatility/session
  combination the strategy's historical trades never covered.
- **Recommendation** — the latest nightly optimizer's best-found params
  vs. what's currently configured. Not applied automatically -- see
  "Acting on a recommendation" below for the (opt-in, review-gated) way to
  actually deploy one.

Every finding now also carries a `details` dict (Phase 10.2) -- structured
data behind the message (e.g. a recommendation's `run_id`,
`current_params`, `recommended_params`, `train_net_pnl`) rather than just
the rendered sentence. The dashboard's "Recent discoveries" list is
clickable; clicking a finding opens a detail window built from this data.

### Acting on a recommendation

A `recommendation` finding's detail window offers two additional actions,
**only when its strategy is config.yaml's own `strategy_name`** (only the
active strategy has a config-file `strategy_params` slot -- see
"Simplification, stated plainly" above; a recommendation for any other
`paper_strategies` entry is shown with an explanation instead of these
controls):

- **Test More** — runs a real backtest with the currently configured
  params and one with the recommended params, side by side, as a
  background job (`kind="params_comparison"`, same job/SSE infrastructure
  as everything else) — before anyone commits to anything.
- **Deploy** — behind an explicit confirm step, rewrites config.yaml's
  `strategy_params` for that strategy. A full byte-for-byte backup of the
  file (`config_backups/`, gitignored) is taken first — the round-trip
  through `yaml.safe_load`/`yaml.safe_dump` does **not** preserve comments
  or exact formatting, so the backup, not the round-trip, is what makes
  this reversible. Every deploy/rollback is logged to the
  `config_deployments` table (append-only, mirrors `model_deployments`'
  own "currently active = latest row" convention) and shown in a
  Deployment History list with a one-click **Undo this change**, which
  restores the exact prior file from its backup.

## Dashboard

`GET /api/research-server/status` → uptime, market connection, data sync
status, active paper strategies (position, session P&L, halted?), the
nightly job scheduler's last run, plus manual `POST /start`, `/stop`, and
`/nightly/run-now` (bypasses the trigger-hour check, for testing or an
on-demand batch). The **Research Server** page renders all of it and
reuses the same `StatTile`/`Badge`/`Panel` components every other
dashboard page does.

## Known gaps / honest limitations

- **No per-strategy parameter override** for `paper_strategies` beyond
  whichever one matches `strategy_name` — see "Simplification" above.
- **The nightly batch runs strategies serially**, waiting for each
  strategy's walk-forward job (and its report) before starting the next
  strategy's jobs — simple and safe, but not the fastest possible
  schedule for a large `paper_strategies` list.
- **Findings are recomputed on every dashboard load**, not cached or
  tracked over time — a finding that was true yesterday and isn't anymore
  simply stops appearing, with no history of what was once flagged.
- **Autonomous mode is single-process, embedded in the API** (FastAPI
  lifespan), the same model `MarketDataScheduler`/`LiveSessionManager`
  already use — there's no separate headless-server process, and running
  more than one API process against the same config would double-trade.

## Testing

`pytest`: `test_research_server_paper_trader.py` (multi-strategy lifecycle,
the structural paper-only guard, per-strategy `runs` persistence, shared
feed), `test_research_server_nightly_jobs.py` (job counts per batch, the
weekly-comparison trigger, once-per-day idempotency, one real happy-path
backtest→report run), `test_research_server_insights.py` (all three
finding types, built directly against `TradeStore` fixtures),
`test_research_server_orchestrator.py` (composes all three subsystems,
shares the Market Data singleton correctly), `test_api_research_server.py`
(HTTP-level route wiring). `npx vitest run` covers the frontend page.

A real deadlock bug (`AutonomousPaperTrader.stop()`/`ResearchServer.stop()`
calling `self.status()` — which re-acquires the same lock — while still
holding it) and a real dataset-resolution gap (`run_optimizer_job`,
`generate_report`, and `run_compare` never got the `db:PRODUCT:RESOLUTION`
support `_load_request_bars` got in Phase 8A) were both caught by direct
verification against the real API and by this phase's own tests, not
just written and assumed correct — see `api/services.py`'s new
`_load_dataset` for the fix to the second one.
