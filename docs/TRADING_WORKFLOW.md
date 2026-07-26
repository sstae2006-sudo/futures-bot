# Trading Workflow

The professional order to use this tool in. Skipping a step, or doing them
out of order, is how a strategy that looks great in testing loses money
live — every step here exists to catch a specific way that happens.

## 1. Collect data

Get real historical bars for the contract and timeframe you actually intend
to trade — `fetch_mes_data.py` pulls MES data from a specific vendor API as
one option; any CSV with `timestamp,open,high,low,close,volume` (or a
recognized vendor spelling) works, via `backtest.data.load_bars`.

Check `--check`'s and `--backtest`'s **DATA QUALITY** and **STRATEGY /
DATA WARNINGS** sections before doing anything else with it: duplicate or
out-of-order timestamps, zero-volume bars, unexpected intraday gaps, and —
new in Phase 4 — whether the bar resolution actually matches what the
strategy was designed for (a strategy built around 5-minute bars handed an
hourly file will warn explicitly, rather than just quietly producing fewer
signals than expected). None of these block the run; all of them change
how much you should trust what comes out of it.

## 2. Baseline backtest

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv
```

Default parameters, no tuning. The point isn't performance — it's
confirming the strategy actually does something sane on this data: it takes
trades, the trade count isn't absurd, nothing in the caveats section is
alarming. This is a smoke test, not a verdict; a single untuned run over
one dataset proves the pipeline works, not that the strategy has an edge.

## 3. Analyze weaknesses

```bash
python -m futures_bot.cli --config config.yaml --backtest data/your_data.csv --report
```

The advanced report's weekday × hour heatmap and best/worst hours/days
answer "when does this strategy actually work?" — a strategy that's only
profitable in a two-hour window, or only on one day of the week, is telling
you something about when to trade it (or not) that the headline P&L number
alone hides. This is also where `metrics.exit_reasons` (in the standard
report) is worth a look: a strategy that exits almost entirely on
max-bars-in-trade or a forced flatten, rather than its own stop/target
logic, isn't really being tested the way it was designed.

## 4. Optimize carefully

```bash
python -m futures_bot.cli --config config.yaml --optimize data/your_data.csv --top 10
```

Sweep a *coarse* grid first — a handful of values per parameter, not a fine
one. A coarse grid that finds a robust region is worth more than a fine one
that finds a lucky point; the optimizer's own default grids in
`optimize.py` are kept small for this reason. Read the "combinations
tried" count and the confidence/warnings section
([RESEARCH_GUIDE.md](RESEARCH_GUIDE.md#overfitting-detection)) before
touching the parameters — the more that was tried, the more skeptical to
be of the winner.

**Only ever act on the validation column, never the training column.**

## 5. Validate out-of-sample

```bash
python -m futures_bot.cli --config config.yaml --optimize data/your_data.csv --rolling
```

A single train/validation split can get lucky or unlucky depending on
exactly where the cut falls. `--rolling` re-validates the top candidates
across several sliding walk-forward windows instead, which is slower but
gives a real distribution of out-of-sample results rather than one number.
Look for a *cluster* of profitable windows, not one standout — a real edge
tends to show up as several nearby parameter combinations all working
reasonably well, not one isolated peak.

If you're choosing between strategies rather than tuning one:

```bash
python -m futures_bot.cli --config config.yaml --compare data/your_data.csv
```

runs every bundled strategy under identical risk/session/broker settings
and ranks them — the fairest head-to-head this tool can produce.

## 6. Paper trade

Nothing above runs against a live feed. Once a strategy has survived steps
2–5 on historical data, the next test is real time with simulated money:

```bash
export MASSIVE_API_KEY=your-data-vendor-key
python -m futures_bot.cli --config config.yaml --live --live-symbol MESH6
```

with `config.yaml`'s `broker.name` left at `paper` (the default). This
polls a live feed for real, current bars (`feeds/massive.py`) and runs them
through the exact same engine a backtest uses, so you're now also testing
things a backtest can't: whether the strategy holds up bar-by-bar in real
time, whether the risk manager's session/kill-switch behavior matches what
you expect live, and whether your own nerve holds up watching it happen
rather than reading a finished report.

Watch `decisions.jsonl` (via `journal.py`) during this phase — it logs
every decision, not just trades, which is what makes it possible to answer
"why didn't it take that obvious-looking trade?" without guessing.

## 7. Consider live deployment

Only after a strategy has:

- Backtested honestly (step 2–3) on real data for the actual contract and
  timeframe you'll trade,
- Survived out-of-sample validation (step 5) with a *cluster* of good
  results, not a lucky peak,
- Held up in live-polled paper trading (step 6) for long enough to see a
  real losing stretch, not just a good run,

does live deployment become a reasonable next step. As of Phase 5, a real
adapter exists (`broker.name: tradovate`) — but read
[USER_MANUAL.md](USER_MANUAL.md#going-live-read-this-first) before setting
it: that adapter was written against Tradovate's public API docs without
access to a live or demo account to verify it against, so it needs a
careful, supervised demo-account walkthrough before it's trusted with
anything unattended, let alone real money (`TRADOVATE_ENV=live`).

Beyond the adapter itself, going live needs a host that stays running with
a persistent volume for `state_file` (see `deploy/DEPLOYMENT.md`), and an
account sized so the risk numbers `--check` prints are actually
survivable. Run `--check` with the real account size before this step, not
the number in `config.example.yaml` — the risk warnings are calibrated to
catch exactly the mistake of copying example numbers into a much smaller
real account.
