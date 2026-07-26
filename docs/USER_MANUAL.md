# User Manual

## What this software does

futures-bot is a research, paper-trading, and (as of Phase 5) live-capable
framework for a single Micro E-mini futures contract (MES, MNQ, M2K, or
MYM), usable either as a CLI or through a web dashboard. It:

- Runs a strategy against historical bars and reports realistic performance
  (commission, slippage, a daily loss kill switch, one position at a time).
- Sweeps a strategy's parameters and reports which combinations survive
  data the search never saw.
- Compares every bundled strategy head-to-head under identical risk/session
  settings.
- Produces terminal and self-contained HTML reports, both ending with the
  specific reasons a given result should not be trusted at face value.
- Runs the same engine against a live-polled bar feed (`--live`) with either
  the paper broker or a real Tradovate account, and can place real bracket
  orders when configured to. **Read the safety section below before ever
  pointing this at a funded account.**
- Keeps a local, self-syncing SQLite mirror of historical bars (the
  **Market Data** pipeline), so backtests/optimization don't depend on
  hand-pulled CSVs — see [RESEARCH_INTERFACE.md](RESEARCH_INTERFACE.md).
- Runs a **dashboard-controlled paper-trading session** (the **Live
  Session** page) and, opt-in, a fully **autonomous research server** that
  paper-trades several strategies at once and runs nightly research
  against fresh data unattended — see
  [RESEARCH_SERVER.md](RESEARCH_SERVER.md). Both are paper-only, enforced
  at runtime, not just by convention.

## What it does not do

- **It does not tell you a strategy is good.** Every report actively argues
  against trusting its own headline number until enough evidence has
  accumulated — see [RESEARCH_GUIDE.md](RESEARCH_GUIDE.md) on overfitting.
- **It is not financial advice.** The bundled strategies are standard,
  published setups (opening range breakout, VWAP reversion, EMA crossover,
  trend pullback), not a proven edge. Backtest before trusting any of them
  with real money, and even then, treat the output as evidence, not proof.
- **IBKR is not implemented.** `broker.name: ibkr` is accepted by config
  validation but `build_engine` raises `NotImplementedError` if you actually
  select it — only `paper` and `tradovate` are real adapters today.
- **The dashboard has no authentication.** Anyone who can reach the API
  port can start/stop paper-trading sessions and read every stored trade —
  fine on localhost, not fine exposed to a network without a reverse proxy
  or VPN in front. See [../deploy/DEPLOYMENT.md](../deploy/DEPLOYMENT.md)'s
  "No authentication" section before running it anywhere else.

## Going live: read this first

As of Phase 5, `--live` can place real orders through a Tradovate account.
The adapter (`brokers/tradovate.py`) was written against Tradovate's public
REST API documentation but has **not** been exercised against a real
account by anyone building this — there was no credentialed, network-
reachable Tradovate account available while it was written, only a mocked
HTTP test suite (`tests/test_tradovate_broker.py`) that checks the adapter
builds the requests it *intends* to, not that Tradovate's servers respond
the way it assumes.

Before setting `broker.name: tradovate` for anything beyond a supervised
walkthrough:

1. Set `TRADOVATE_ENV=demo` (the default) and demo-account credentials —
   see `brokers/tradovate.py`'s module docstring for the full environment
   variable list. Never put credentials in `config.yaml`.
2. Run one cycle by hand and watch Tradovate's own UI the whole time:
   connect, place one bracket order with a wide stop/target, confirm it
   looks exactly as expected, move the stop once, confirm it actually
   moved, then flatten and confirm flat.
3. Only after that manual walkthrough matches expectations should `--live`
   run unattended against the demo account — and only after it's run
   unattended on demo for a meaningful stretch, with the daily-loss kill
   switch verified to actually halt it, should `TRADOVATE_ENV=live` ever be
   set.

`--live`'s startup banner prints a warning whenever `broker.name` isn't
`paper`, specifically so this can't happen by accident from a config typo.

## Installation

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml
```

`config.example.yaml` documents every setting inline. Copy it rather than
editing it directly, so your local settings never collide with an upstream
change to the example.

## Configuration

`config.yaml` has five sections:

| Section | Controls |
| --- | --- |
| `risk` | Stop/target distance, daily loss kill switch, trade cap, contracts per trade, account size |
| `session` | Trading-hours window, force-flat time before the close |
| `broker` | Which broker adapter, slippage, commission, starting cash |
| `logging` | Log level, where `decisions.jsonl` is written |
| `strategy_name` / `strategy_params` | Which strategy, and its parameters |

Validate a config and see the risk profile stated in dollars before running
anything:

```bash
python -m futures_bot.cli --config config.yaml --check
```

This also prints two kinds of warning, neither of which blocks the run:

- **Risk warnings** — a legal but dangerous configuration (too much risked
  per trade, a stop tight enough to eat normal noise, commission eating a
  large share of the target). These are about your capital; the tool's job
  is only to make sure the number was seen before it was risked.
- **Strategy warnings** — a parameter combination that is internally
  contradictory (an entry window that closes before it opens, a min/max
  range that can never both pass) and will very likely produce zero trades.

## Running the demo

Confirms the engine, risk manager, broker, and strategy are wired together
correctly, using synthetic bars generated in-process — no CSV needed:

```bash
python -m futures_bot.cli --config config.yaml --demo
```

## Running the dashboard

Everything below this point also works as a web dashboard instead of the
CLI — backtests, the optimizer, comparisons, reports, trade analysis, the
market-data pipeline, plus two things the CLI doesn't have: a
dashboard-controlled paper-trading **Live Session** and an opt-in
autonomous **Research Server**. It's a separate product from the CLI/live
bot (same core engine underneath) — see
[../deploy/DEPLOYMENT.md](../deploy/DEPLOYMENT.md) for what "separate"
means for deployment.

Local dev, two processes:

```bash
pip install -e .
python -m futures_bot.api                 # API on http://127.0.0.1:8000

cd frontend && npm install && npm run dev  # dashboard on http://127.0.0.1:5173
```

Open `http://127.0.0.1:5173`. The dashboard talks to the API cross-origin
(CORS is wide open for exactly this local-dev case — see `api/app.py`'s
module docstring). For a single-process deployment that builds the
dashboard and serves it from the API itself, see DEPLOYMENT.md.

**This has no authentication.** It's meant for localhost or a trusted
private network, not the public internet — see DEPLOYMENT.md before
running it anywhere else. Full setup, page-by-page tour, and the API
reference: [RESEARCH_INTERFACE.md](RESEARCH_INTERFACE.md).

## Running backtests

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv
```

Your CSV needs `timestamp,open,high,low,close,volume` (or a common vendor
spelling — `Date`, `O`, `H`, `L`, `C`, `Vol` are all recognized). Naive
timestamps (no timezone) are read as Central Time, since that is what CME
session boundaries are defined against.

**Walk-forward** (a 70/30 chronological split — train on the first 70%,
report the last 30% the strategy never saw):

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv --walk-forward
```

**Advanced report** (weekday × hour heatmap, best/worst hours and days):

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv --report
```

**Self-contained HTML report** (equity curve, drawdown chart, sortable
trade table — one file, opens in any browser, no internet needed):

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv --html-report out.html
```

## Optimization

Any `strategy_params` entry in `config.yaml` that is a YAML list becomes a
sweep dimension. Everything else stays fixed. For example:

```yaml
strategy_params:
  range_minutes: [15, 30, 60]
  min_range_points: 2
```

sweeps `range_minutes` across three values while holding `min_range_points`
at 2.

```bash
python -m futures_bot.cli --config config.yaml --optimize data/your_data.csv --top 10
```

Every combination is scored on a 70% training slice; the top `--top`
combinations are then re-tested on the held-out 30%. **Judge only by the
validation numbers** — the training numbers reward overfitting by
construction. Add `--rolling` to validate each top candidate across several
walk-forward windows instead of one static split, for a more robust (but
slower) estimate.

For a quick, config-free sweep across *all four* bundled strategies at
once with sensible default grids:

```bash
python tools/optimize.py data/your_data.csv --top 15
```

This is a thin wrapper around the same optimizer — see
[RESEARCH_GUIDE.md](RESEARCH_GUIDE.md) for what it does under the hood.

## Comparing strategies

Runs every registered strategy (or a comma-separated subset) under the
*same* risk/session/broker settings and ranks them:

```bash
python -m futures_bot.cli --config config.yaml --compare data/your_data.csv
python -m futures_bot.cli --config config.yaml --compare data/your_data.csv --strategies ema_crossover,vwap_reversion
```

## Running live

**Read [Going live: read this first](#going-live-read-this-first) before
this section.**

```bash
export MASSIVE_API_KEY=your-data-vendor-key
python -m futures_bot.cli --config config.yaml --live --live-symbol MESH6 --resolution 5min --poll-seconds 30
```

This polls the same data vendor `fetch_mes_data.py` pulls historical CSVs
from (`feeds/massive.py`), waits for each bar's window to fully close (a
still-forming bar is never fed in — see `feeds/base.py`), and hands each new
bar to the same `TradingEngine` a backtest uses. Which broker actually
receives orders is controlled entirely by `config.yaml`'s `broker.name` —
`paper` (default, no real orders, good for testing the live loop itself)
or `tradovate` (real orders; requires `broker.tradovate_symbol` and
Tradovate credentials in the environment, see `brokers/tradovate.py`).

`--live-symbol` is the *data vendor's* symbol for the contract (e.g.
`MESH6`); if trading through Tradovate, `config.yaml`'s
`broker.tradovate_symbol` is that broker's own symbol for the same expiry
(e.g. `MESZ5`) — the two vendors don't necessarily use the same naming, so
both have to be set correctly and don't have to match each other's string.

Stop with Ctrl+C — this flattens any open position and shuts down cleanly
before exiting, the same as reaching the end of a backtest.

## Interpreting results

Every report is ordered so you cannot reach the headline number without
first seeing what to be skeptical of, and ends with a "read this before
believing the numbers above" section that states in plain language why a
result might not be trustworthy — too few trades, one trade carrying the
whole result, commission drag, a drawdown the account might not survive.

As of Phase 4, the terminal and HTML reports also include a **"What these
numbers mean"** section that translates that specific run's Profit Factor,
Win Rate, Expectancy, Sharpe/Sortino, Max Drawdown, and R-multiple into a
plain-English sentence using the actual numbers just shown — not a generic
glossary. If you are new to trading metrics, start there.

The single most important habit: **never judge a strategy by its in-sample
(training) number.** Everything in this tool that searches or tunes
parameters (`--optimize`, `optimize.py`) reports a validation number
specifically so you have an honest figure to check against. See
[RESEARCH_GUIDE.md](RESEARCH_GUIDE.md) for why in-sample numbers are
structurally optimistic, and [TRADING_WORKFLOW.md](TRADING_WORKFLOW.md) for
the order operations should happen in.

## Where to go next

- [ARCHITECTURE.md](ARCHITECTURE.md) — how the pieces fit together and why,
  including the CLI/API/dashboard layer overview
- [STRATEGY_GUIDE.md](STRATEGY_GUIDE.md) — what each bundled strategy does
- [RESEARCH_GUIDE.md](RESEARCH_GUIDE.md) — the optimizer, trade database, and ML dataset
- [TRADING_WORKFLOW.md](TRADING_WORKFLOW.md) — the order to do all of this in
- [RESEARCH_INTERFACE.md](RESEARCH_INTERFACE.md) — the web dashboard: setup, every page, API reference
- [RESEARCH_WORKSTATION.md](RESEARCH_WORKSTATION.md) — background jobs, MAE/MFE, market regime, optimizer heatmap, experiments
- [RESEARCH_SERVER.md](RESEARCH_SERVER.md) — the opt-in autonomous mode: auto data sync, multi-strategy paper trading, nightly research
- [../deploy/DEPLOYMENT.md](../deploy/DEPLOYMENT.md) — deploying either the CLI bot or the API/dashboard
