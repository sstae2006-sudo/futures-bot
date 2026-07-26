# Research Intelligence Interface

Phase 6A: a web-based research workstation for this project's quant
workflow — run backtests, compare strategies, sweep parameters, and
inspect trades without touching the terminal. **This is an internal
research tool, not a customer-facing product, and it cannot place a live
order.** See [Safety](#safety) below.

## Setup

Two processes: the FastAPI backend and the React frontend. Run both from
the repo root.

### Backend

```bash
pip install -e .             # installs fastapi + uvicorn along with the rest
python -m futures_bot.api --port 8000
```

Serves on `http://127.0.0.1:8000`. Interactive API docs (Swagger UI) are
auto-generated at `http://127.0.0.1:8000/docs`. The backend reads
`config.yaml` and CSV datasets relative to whatever directory it's started
from — the same convention `python -m futures_bot.cli` already uses — so
run it from the repo root, same as the CLI.

Research data (backtest/optimizer run history, trades, reports) persists to
`research.db` (SQLite) in the working directory, overridable via
`FUTURES_BOT_RESEARCH_DB`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Serves on `http://localhost:5173` and talks to the backend at
`http://127.0.0.1:8000` by default (override with `VITE_API_BASE_URL` in a
`.env` file under `frontend/`). CORS is wide open on the backend for local
development — see [Safety](#safety).

### Tests

```bash
pytest                          # backend, including 55 API-specific tests
cd frontend && npm test         # frontend component tests (Vitest)
cd frontend && npm run build    # typechecks + production bundle
```

## Architecture

```
React SPA (frontend/)
   |  fetch() over HTTP, JSON
   v
FastAPI app (futures_bot/api/app.py)
   |  routes/*.py -- thin, one router per resource area
   v
services.py -- the actual work, independently callable/tested
   |
   +--> backtest.runner.run_backtest / split_bars      (existing, untouched)
   +--> research.optimizer.run_optimization             (existing, untouched)
   +--> research.comparison.compare_strategies           (existing, untouched)
   +--> backtest.html_report.generate_html_report        (existing, untouched)
   |
   v
research.trade_store.TradeStore (SQLite)
   -- extended (additively) with `runs` and `reports` tables this phase;
      `trades` and `optimization_trials` are the same tables Phase 3 built.
```

**The API never bypasses the trading engine.** Every backtest the API runs
goes through the exact same `TradingEngine`/`RiskManager`/`PaperBroker` a
`python -m futures_bot.cli --backtest` run does — `services.py` calls
`backtest.runner.run_backtest` directly, the same function `cli.py` calls.
There is no second, API-specific backtesting code path to drift out of sync
with the one the CLI and the rest of the test suite already exercise.

**The frontend never touches trading logic.** `frontend/src/api.ts` is a
thin `fetch` wrapper; every function in it maps to exactly one backend
route. No strategy logic, no risk calculation, no metric formula lives in
the frontend — it only requests data already computed by the backend and
displays it.

### Why a `runs` table was added

`research.trade_store.TradeStore` (Phase 3) already had `trades` and
`optimization_trials` tables, but nothing that let a dashboard list "every
backtest run" with its own headline metrics (net P&L, Sharpe, max drawdown,
caveats) without recomputing them from raw trades on every request, or
represent a run that produced zero trades at all. The `runs` table (see
`research/trade_store.py`'s schema) is purely additive — every existing
`TradeStore` method and its tests are untouched; `tests/test_research_trade_store.py`
covers the ~12 new methods (`insert_run`, `complete_run`, `fail_run`,
`fetch_runs`, `fetch_run`, and the matching `reports` table methods)
alongside the original suite.

### A real bug found and fixed while building this

`services.get_performance`'s equity curve needed each trade's *entry
reason and indicator snapshot* (RSI, ADX, ATR, VWAP — the Trade Explorer's
"Market Context" panel), which `BacktestMetrics.trades` alone doesn't carry.
The fix: every backtest run through the API is wired with a
`backtest.runner.CountingJournal`, the same class `research.features`
already uses for exactly this purpose, and `research.features.build_trade_records`
joins its captured entries to the closed trades positionally — the
established, already-tested pattern from Phase 3, not a new one invented
for this API.

### Thread safety

FastAPI runs synchronous route handlers in a worker thread pool even with a
single `uvicorn` process — `TradeStore` wraps one `sqlite3.Connection`,
which `sqlite3` restricts to the thread that created it. `api/store.py`
opens a fresh `TradeStore` per call rather than caching one; `sqlite3.connect()`
against an existing file is fast, so this is not a meaningful overhead for
a single-user local tool. See that module's docstring for the full
reasoning and what a higher-throughput deployment would need instead.

## API endpoints

Full interactive reference: `http://127.0.0.1:8000/docs`. Summary:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness check |
| GET | `/api/system/overview` | Dashboard home stats |
| GET | `/api/logs` | Run history + journalled strategy events |
| GET | `/api/strategies` | Every registered strategy + its parameter schema |
| GET | `/api/strategies/{name}` | One strategy's parameter schema |
| GET | `/api/datasets` | CSV files available in the working directory |
| POST | `/api/backtest/run` | Run a backtest (optionally walk-forward) |
| GET | `/api/backtests` | List backtest/walk-forward run history |
| GET | `/api/backtests/{run_id}` | One run's full detail |
| GET | `/api/trades` | Filterable trade list (run/strategy/side/outcome) |
| GET | `/api/performance/{run_id}` | Equity curve, drawdown, trade statistics |
| POST | `/api/compare/run` | Run several strategies on the same data |
| POST | `/api/optimizer/run` | Grid search with train/validation split |
| GET | `/api/optimizer/results/{batch_id}` | Every trial from one optimizer batch |
| GET | `/api/walk-forward/{run_id}/verdict` | Green/yellow/red overfit read on one run |
| POST | `/api/report/generate` | Rebuild and persist an HTML report for a run |
| GET | `/api/reports` | List generated reports |
| GET | `/api/reports/{report_id}/view` | Serve a report's HTML |
| GET | `/api/ml/dataset` | ML dataset readiness (feature columns, label counts) |

All POST bodies and error responses are typed (Pydantic) and documented in
`/docs`. Client errors (bad dataset name, unknown strategy, path traversal
attempt) return HTTP 400 with a `{"detail": "..."}` body; validation errors
on the request shape itself return HTTP 422 (FastAPI's default).

## Workflows

**Run a backtest and read the result:** Backtest Launcher → pick strategy
and dataset (parameters pre-fill from the strategy's own constructor
defaults, introspected live — see `api/introspection.py`) → Run → the
result view shows P&L stats, equity curve, drawdown, and the same caveats
`backtest.report.plain_english_summary` puts in the terminal/HTML reports.

**Judge a strategy honestly:** check "walk-forward" before running, or use
the Optimizer page with a parameter sweep — either way, read the
Validation columns, not the Training ones, and check the Overfit Verdict
badge (green/yellow/red; see `services.overfit_verdict`, whose thresholds
mirror `research/safety.py`'s existing checks rather than inventing new
ones).

**Understand a specific trade:** Trade Explorer → filter by strategy/side/
outcome → click a row → the detail panel shows the entry signal's reason
string and every indicator value the strategy's `Signal.metadata` carried
at entry time (RSI, ADX, ATR, VWAP, band distance, whatever that specific
strategy records).

**Compare strategies head to head:** Strategy Comparison → pick a dataset
and (optionally) a subset of strategies → overlaid equity curves and a
ranked table, exactly `research.comparison.compare_strategies`'s output.

## Safety

- **Nothing here can place, modify, or cancel a real order.** `api/` never
  imports `brokers.tradovate` or `feeds.massive` — enforced by
  `tests/test_api_routes.py::TestNoUnsafeTradingControls`, which both
  scans the OpenAPI schema for route paths that look like a trading action
  and statically checks (via `ast`) that no file under `api/` imports
  either module. If a future change needs live-trading data through this
  API, that test failing is the intended trip-wire.
- **CORS is wide open** (`allow_origins=["*"]`) for local development
  convenience. This is appropriate for a tool meant to run on localhost or
  inside a trusted network, not for exposing this API on the public
  internet. Before doing that: add an allow-list, add authentication (none
  exists today — see Phase 6B), and put it behind a reverse proxy with TLS.
- **No authentication exists.** Anyone who can reach the API can run
  backtests/optimizations (CPU cost, no financial risk) and read every
  stored trade/report. Fine for a single developer's machine; not fine for
  a shared or internet-reachable deployment.
- **Path traversal is blocked** on every dataset-selecting endpoint
  (`api/services.py::_resolve_dataset` rejects anything but a bare
  filename) — covered by `TestDatasets::test_rejects_path_traversal` and
  `TestBacktestRoutes::test_run_backtest_path_traversal_dataset_is_400`.

## Known gaps (this pass)

- **Backtests run synchronously.** A request blocks until the backtest
  finishes. Phase 4's incremental indicators keep even a full-history
  backtest to single-digit seconds, so this is usually fine, but there's
  no progress bar and no way to cancel a long-running optimizer sweep once
  started.
- **No MAE/MFE per trade.** The generic `Trade`/`TradeRecord` model this
  API's trade explorer reads doesn't carry maximum adverse/favorable
  excursion — only `trend_pullback`'s own strategy-local analytics
  (`strategy/trend_pullback/analytics.py`) track that today.
- **No parameter-sensitivity heatmap.** The optimizer page shows a ranked
  trial table (train vs. validation, matching `research.optimizer`'s own
  report shape); `research.reporting.parameter_sensitivity` already
  computes per-parameter score grouping and isn't wired into this API yet.
- **No auth**, as noted above.

## Phase 6B candidates

1. **Background jobs + progress streaming** for long optimizer sweeps
   (WebSocket or Server-Sent Events), instead of a blocking POST.
2. **Parameter-sensitivity heatmap and correlation charts** on the
   Optimizer and ML Research pages, built on data the backend (via
   `research.reporting.parameter_sensitivity`) already computes.
3. **MAE/MFE and R-multiple per trade**, generalizing `trend_pullback`'s
   entry-context tracking (`_OpenTradeState.update_excursion`) so every
   strategy's trades carry it, not just that one.
4. **Authentication** and a same-origin (not wildcard) CORS policy, if
   this is ever run somewhere other than a single developer's machine.
5. **ML dataset CSV export button**, wiring `research.features.write_ml_dataset_csv`
   directly into the ML Research page instead of Python-only access.
6. **Live paper-trading dashboard** — a *read-only* view onto a running
   `--live` process's state (`decisions.jsonl`, current position, session
   P&L) would fit this same research-workstation idea without violating
   the "no unsafe trading controls" rule, since it would only ever display
   what `cli.cmd_live` is already doing, never control it.
