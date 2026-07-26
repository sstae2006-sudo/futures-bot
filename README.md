# futures-bot

Backtesting, optimization, paper-trading, and live-trading framework for a
single Micro E-mini futures contract (MES/MNQ/M2K/MYM). Risk controls,
session handling, order lifecycle, decision logging, four reference
strategies, a grid-search optimizer with honest out-of-sample validation,
terminal/HTML reports that tell you when *not* to trust their own headline
number, and (as of Phase 5) a live data feed and a Tradovate broker adapter.

**Educational tool, not financial advice.** The bundled strategies are
standard published setups, not a discovered edge — see
[docs/STRATEGY_GUIDE.md](docs/STRATEGY_GUIDE.md). **The Tradovate adapter can
place real orders and has not been verified against a live or demo account**
(no credentialed account was available while building it) — read
[docs/USER_MANUAL.md](docs/USER_MANUAL.md#going-live-read-this-first) in
full before setting `broker.name: tradovate`.

## Documentation

| Doc | Covers |
| --- | --- |
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | Install, configure, run demos/backtests/optimization/comparisons, read reports |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The pipeline, why strategies can't trade directly, incremental-indicator performance |
| [docs/STRATEGY_GUIDE.md](docs/STRATEGY_GUIDE.md) | Every bundled strategy: concept, parameters, strengths, weaknesses |
| [docs/RESEARCH_GUIDE.md](docs/RESEARCH_GUIDE.md) | Trade database, ML feature export, the optimizer, overfitting detection |
| [docs/TRADING_WORKFLOW.md](docs/TRADING_WORKFLOW.md) | The order to actually use all of this in |
| [docs/RESEARCH_INTERFACE.md](docs/RESEARCH_INTERFACE.md) | The web research dashboard: setup, architecture, API reference |
| [docs/RESEARCH_WORKSTATION.md](docs/RESEARCH_WORKSTATION.md) | Phase 6B: background jobs, MAE/MFE, market regime, optimizer heatmap, experiments |
| [docs/RESEARCH_SERVER.md](docs/RESEARCH_SERVER.md) | Phase 8B: opt-in autonomous mode -- auto data sync, multi-strategy paper trading, nightly research, drift/recommendation findings |
| [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md) | Host-agnostic deployment guide, Dockerfile, systemd unit |

Phase 7 added a dashboard-controlled paper-trading session: `POST/GET
/api/live/{start,stop,status,stream}` (`api/live_session.py`) drive the same
`cli.cmd_live` engine loop from the **Live Session** page, so you can
start/watch/stop a paper session without a terminal. Paper-only, enforced at
runtime (refuses before constructing a broker if `broker.name != paper`) —
real trading is still `python -m futures_bot.cli --live` only.

Phase 7a closed the gap that left a live session's trades stranded in
`decisions.jsonl`: a `runs` row (`kind="live"`) opens when a session starts,
and `LiveTradeJournal` persists each closed trade to the same `TradeStore`
a backtest uses the moment it closes — so a paper session's fills show up in
Trade Explorer, the Dashboard, and Market Regime immediately, and survive an
API process restart instead of vanishing with the in-memory snapshot.

Phase 8A added a local market-data pipeline (`market_data/`): a SQLite
database (`market_data.db`, `MarketDataStore`) that becomes a growing,
self-maintaining alternative to hand-pulled CSVs. `contracts_client.py`
auto-detects the front-month contract via Massive's Contracts API and rolls
to the next one automatically; `sync.py` backfills history (resolving the
correct contract per sub-window, so a multi-quarter pull needs no manual
stitching), syncs incrementally (resume-from-coverage, idempotent —
`INSERT OR IGNORE` makes re-fetching an overlapping window a no-op), and
detects/repairs gaps (`contracts.is_market_open` tells a real gap from an
ordinary weekend/holiday/maintenance closure). `MarketDataScheduler` is a
background thread that keeps it current automatically while the market's
open. New CLI commands: `--sync-data`, `--backfill`, `--verify-data`,
`--repair-gaps`. Any dataset named `db:PRODUCT:RESOLUTION` (e.g.
`db:MES:5min`) is a drop-in alternative to a CSV path everywhere one was
accepted before (CLI `--backtest`/`--optimize`/`--compare`, the API's
`BacktestRunRequest.dataset`) — see the **Market Data** dashboard page for
coverage, gaps, sync history, and scheduler control.

Phase 8B added an opt-in autonomous mode (`research_server.enabled: false`
by default) that composes everything above into a self-maintaining
research server: `research_server/paper_trader.py` paper-trades several
strategies concurrently off one shared bar poll (isolated risk/kill-switch
per strategy, contract auto-detection/rollover reused directly from Phase
8A), `research_server/nightly_jobs.py` submits backtests/optimizer runs/
walk-forward/reports through the existing job system on a nightly (and
weekly) schedule against whatever the data scheduler just synced, and
`research_server/insights.py` surfaces — never applies — degradation,
regime-drift, and better-parameter findings. `api/app.py`'s FastAPI
lifespan starts all of it on boot when enabled; see the **Research
Server** dashboard page and [docs/RESEARCH_SERVER.md](docs/RESEARCH_SERVER.md).

## Quick start

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml
python -m futures_bot.cli --config config.yaml --check     # validate settings, see risk in dollars
python -m futures_bot.cli --config config.yaml --demo      # confirm the engine is wired correctly
pytest                                                       # 538 tests
```

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv --walk-forward
python -m futures_bot.cli --config config.yaml --optimize data/your_data.csv --top 10
python -m futures_bot.cli --config config.yaml --compare data/your_data.csv
python -m futures_bot.cli --config config.yaml --live --live-symbol MESH6   # paper by default; see docs/USER_MANUAL.md
```

Full walkthrough, including how to read the reports: [docs/USER_MANUAL.md](docs/USER_MANUAL.md).

**Prefer a browser to a terminal?** There's a web research workstation —
backtest launcher, trade explorer (with MAE/MFE and market-regime labels),
strategy comparison, an optimizer with a parameter heatmap, background jobs
with live progress, and experiment tracking, all over a read-only REST API
that can't place a live order:

```bash
pip install -e . && python -m futures_bot.api                 # backend, :8000
cd frontend && npm install && npm run dev                      # frontend, :5173
```

See [docs/RESEARCH_INTERFACE.md](docs/RESEARCH_INTERFACE.md) and
[docs/RESEARCH_WORKSTATION.md](docs/RESEARCH_WORKSTATION.md).

## Status

**Built and tested** (538 backend tests, 26 frontend tests)

- Contract specs (MES/MNQ/M2K/MYM) with correct tick and point values
- CME session arithmetic — trade dates, maintenance halt, weekend closure
- Risk manager: daily loss kill switch, trade cap, trading-hours filter, force-flat
- Durable state — the kill switch survives a restart
- Paper broker with adverse slippage and conservative fill resolution
- Tradovate broker adapter (REST, bracket/OSO orders, ratchet-only stop
  modification, async fill reconciliation) — see the live-trading warning
  above before using it for anything beyond a supervised demo walkthrough
- Live bar feed (`--live`), polling a data vendor for genuinely closed bars
  only, driving the same engine a backtest uses
- Web research workstation: FastAPI backend + React frontend for running
  backtests, comparing strategies, exploring trades, and sweeping optimizer
  parameters from a browser — read-only w.r.t. any real account, see
  [docs/RESEARCH_INTERFACE.md](docs/RESEARCH_INTERFACE.md). As of Phase 6B:
  background jobs with live SSE progress, MAE/MFE and market-regime
  analytics per trade, an optimizer parameter heatmap, research experiment
  tracking, and rule-based dashboard insights — see
  [docs/RESEARCH_WORKSTATION.md](docs/RESEARCH_WORKSTATION.md)
- Structured decision journal (every decision, not just trades)
- Validated settings file with risk warnings in dollars, plus config/data
  sanity warnings (contradictory parameters, a bar resolution the strategy
  wasn't designed for, a warmup window longer than the dataset)
- Engine wiring strategy → risk → broker (backtest, paper, and demo all
  drive the same engine — see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md))
- Backtest runner: CSV loading, replay, metrics, walk-forward split (static and rolling)
- Four reference strategies, all using incremental (O(1)-per-bar) indicators
  as of Phase 4 — `ema_crossover`, `opening_range_breakout`, `vwap_reversion`,
  `trend_pullback`
- Shared indicator library: SMA, EMA, RSI, ADX, ATR, VWAP (+bands), both
  batch and incremental forms
- One grid-search optimizer (`research/optimizer.py`) with train/validation
  split, rolling walk-forward validation, and an automated overfitting/safety
  report; `optimize.py` is a thin multi-strategy CLI wrapper around it
- Self-contained HTML backtest report (equity curve, drawdown, sortable
  trade table, plain-English metric explanations)
- Extended metrics: Sharpe, Sortino, R-multiples, streaks, weekday/hour/month
  breakdowns, equity curve CSV export
- Trade database (SQLite) and ML-ready feature dataset export
- Multi-strategy comparison leaderboard

**Not built yet**

- An IBKR broker adapter — `broker.name: ibkr` validates but `build_engine`
  raises `NotImplementedError` if selected; only `paper` and `tradovate` are real
- WebSocket/push order updates for Tradovate — the adapter is REST-polling
  only today (see `brokers/tradovate.py`'s module docstring for why that
  was the deliberate first choice)
- A GUI/web frontend — everything here is CLI + generated HTML reports

## Plugging in a new strategy

Subclass `Strategy`, register it, point the config at it:

```python
from futures_bot.strategy.base import Strategy, StrategyRegistry

@StrategyRegistry.register("my_strategy")
class MyStrategy(Strategy):
    warmup_bars = 20

    def on_bar(self, bars, position):
        if position is None and some_condition(bars):
            return self.enter_long("Condition met: ...")
        return self.hold("No setup.")
```

The strategy only decides. It never places orders, sizes positions, or
checks the clock — the engine does that after the risk manager approves.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for why that boundary is
structural rather than a convention, and
[docs/STRATEGY_GUIDE.md](docs/STRATEGY_GUIDE.md) for what the four bundled
strategies do.

Two rules: never index past `bars[-1]` (that's lookahead bias — a backtest
profitable in ways that don't survive a live market), and always give a
reason, including on holds — the decision journal depends on being able to
show *why* a trade was or wasn't taken.

## Design decisions worth knowing

**Decimal, not float.** Futures P&L is exact tick arithmetic. Float drift is
the difference between "stop hit" and "stop missed".

**Session dates aren't calendar dates.** CME equity index futures run 17:00 CT
to 16:00 CT the next day. A position opened 18:00 Monday belongs to Tuesday's
session. Keying the daily loss limit on the calendar date would reset it at
midnight — mid-session, right after a bad evening.

**The kill switch persists to disk.** A bot that hits its limit, crashes, and
restarts with a clean slate has a speed bump, not a kill switch.

**Stops rest at the broker, not in memory.** If this process dies, the
protective order survives it.

**A real broker's fills are polled, not assumed.** Unlike the paper
simulator, a live stop/target can fill between calls with nothing to notice
unless something asks. `Broker.poll_closed_trade` exists so a real fill
still reaches the kill switch and the journal — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for why this was a genuine gap
until Phase 5, not just a nice-to-have.

**Ambiguous bars resolve against you.** When a bar's range covers both stop and
target, OHLC can't say which came first. This assumes the stop. Assuming the
target is the most common way a backtest reports profits that never appear.

**Every entry has a validation number to check against.** `--optimize` and
`--optimize --rolling` both report training and out-of-sample figures
side by side, plus an automated confidence rating — the goal throughout is
making it hard to accidentally trust an overfit result. See
[docs/RESEARCH_GUIDE.md](docs/RESEARCH_GUIDE.md).

More design rationale, including the Phase 4 move to incremental indicators
and its measured before/after speedup, lives in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Deployment

See [deploy/DEPLOYMENT.md](deploy/DEPLOYMENT.md) for a host-agnostic guide
covering **two separate deployable products**: the Phase 5 CLI-only
live-trading bot (`Dockerfile`, `futures-bot.service`) and the Phase 6-8B
research API + dashboard (`Dockerfile.api`, `futures-bot-api.service`,
serving the built React app from the same process/port as the API — see
`api/app.py`'s `_maybe_mount_frontend`). Neither one talks to a real broker
except the CLI bot, and only if `broker.name: tradovate`.

Short version for the CLI bot: this needs a process that stays running (so
not Vercel/Lambda), a persistent volume for the state file, a restart
policy with a failure ceiling, and a correct system clock. The persistent
volume is the one people skip — without it, a container restart wipes the
kill switch and hands the account a fresh loss allowance the same day.

Short version for the research API/dashboard: **it has no authentication in
front of it** — `python -m futures_bot.api` refuses to bind anything but
`127.0.0.1`/`::1`/`localhost` unless you pass `--allow-network-exposure`
(see `api/__main__.py`), and that flag is a confirmation, not a substitute
for a reverse proxy or VPN. Read DEPLOYMENT.md's "No authentication" section
before running this anywhere but your own machine.

A real broker adapter (Tradovate) exists as of Phase 5, but read the
live-trading warning near the top of this file and
[docs/USER_MANUAL.md](docs/USER_MANUAL.md#going-live-read-this-first) in
full before pointing it at a funded account.

## Account sizing

At an S&P around 7,500, one MES is roughly $37,500 notional and $5/point.
On a small account, an ordinary session's range can be the whole account.
Run `--check` with the *real* account size, not the example config's
numbers — the risk warnings are calibrated to catch exactly the mistake of
carrying example numbers into a much smaller real account, and will tell
you directly (in dollars) if the configured risk per trade or daily loss
limit is out of proportion to `risk.account_size`.
