# Experiment 1 — `trend_pullback` gated on `RiskState` (2026-08-05)

The platform's first hypothesis run end to end through the full, already-built
validation pipeline: `strategy/context_gate.py::ContextGatedStrategy` (Phase 1,
2026-08-04) → `backtest/context_comparison.py::compare_context_impact` (built
2026-07-27, never exercised on a real claim before this) →
`research/safety.py::build_safety_report`. Script:
`tools/experiment_context_trend_pullback_risk_state.py` (reproducible, reads
real data directly from `market_data.db`, writes nothing to `research.db`).

## Pre-registered hypothesis

Stated in the script's own docstring *before* it was run:

> `trend_pullback`'s pullback-continuation entries are hypothesized to
> perform worse when `RiskState` indicates elevated-or-extreme
> market-condition risk (`context/risk.py`'s `RiskState.ELEVATED`/`HIGH` — a
> composite of `volatility_state` + `market_regime`, meaning volatility is
> running hot relative to its own trailing average). Disorderly,
> gap/spike-prone conditions are a documented failure mode for continuation
> systems. Requiring `RiskState.LOW` at entry is hypothesized to improve or
> at least preserve per-trade expectancy, likely at the cost of fewer trades.

## Method

- **Data**: real MES 5-minute bars from `market_data.db` — 73,312 bars,
  2025-07-24 → 2026-08-05 (a little over a year). Not synthetic.
- **Strategy**: `TrendPullbackStrategy` with its own class-default
  parameters (no tuning) — the baseline is the strategy as bundled, not a
  cherry-picked variant.
- **Settings**: config.yaml's real defaults (stop 5pts / target 10pts /
  daily-loss $300 / 1 contract / $2500 account, $0.62 commission per side,
  1 tick slippage).
- **Comparison**: `compare_context_impact` over the full dataset (baseline
  = `ContextMode.OBSERVE`, i.e. unfiltered — `ContextGatedStrategy` never
  sees a context in that mode, so it's decision-identical to the raw
  strategy; gated = `ContextMode.ENABLED`, the `RiskState.LOW` filter
  actually applied).
- **Validation**: a chronological 70/30 train/validation split
  (`backtest.runner.split_bars`), run separately for both variants, fed
  into `research/safety.py::build_safety_report`.
- **Commission stress**: 2x `commission_per_side`, reimplemented locally
  (not via `research/safety.py::check_commission_sensitivity`, which
  always runs at `ContextMode.OFF` and would silently test the *unfiltered*
  behavior for a `ContextGatedStrategy` factory — see the script's own
  `commission_shock_finding` docstring) so the gated variant is actually
  stress-tested at `ContextMode.ENABLED`.

## Results

### Full dataset (`compare_context_impact`)

| | Trades | Net P&L | Win rate |
|---|---|---|---|
| Baseline (unfiltered) | 92 | **-$305.33** | 46.7% |
| Gated (`RiskState.LOW` required) | 47 | **-$375.78** | 40.4% |

46 of 92 trades changed (roughly half the entries were vetoed by the filter).

### Train/validation split

| | Trades | Net P&L | Profit factor | Expectancy/trade | Max drawdown |
|---|---|---|---|---|---|
| Baseline — train | 56 | -$178.19 | 0.87 | -$3.18 | $423.47 |
| Baseline — validation | 36 | -$127.14 | 0.85 | -$3.53 | $343.54 |
| Gated — train | 26 | -$217.24 | 0.66 | -$8.36 | $373.53 |
| Gated — validation | 21 | -$158.54 | 0.66 | -$7.55 | $376.11 |

### Commission shock (2x commission)

Neither variant had a positive baseline net P&L to stress-test — the check
correctly declined to run rather than manufacture a number
(`research/safety.py`'s own convention: `if baseline.net_pnl <= 0: return []`).

### `research/safety.py` verdicts

- **Baseline: High confidence, no warnings.** Not a claim the strategy is
  good — it means none of the automated overfitting/degradation/unrealistic-
  gains checks fired. A consistently unprofitable result with similar
  train/validation behavior and no too-good-to-be-true pattern is exactly
  what "not overfit, just not an edge" looks like to these checks.
- **Gated: Medium confidence, one warning** — "Only 26 training trades
  (below 30) — the parameters were ranked on very little evidence." The
  filter's own sample-halving directly triggered the platform's existing
  minimum-trade-count guard.

## Verdict: hypothesis rejected

`RiskState.LOW` gating did not improve `trend_pullback`'s entries — it made
them worse on every measured axis: fewer trades, more negative total P&L,
lower win rate, lower profit factor, and roughly 2.3x worse per-trade
expectancy (-$7.55 to -$8.36 vs. -$3.18 to -$3.53). Working the arithmetic
in the other direction: the 45 trades the filter *removed* (full-dataset)
averaged **+$1.57/trade**, while the 47 it *kept* averaged **-$8.00/trade**
— directionally the opposite of the hypothesis, not merely "no effect." With
only ~45-47 trades in each bucket this difference is well within noise and
should not be read as "elevated risk state is actually good for this
strategy" either — the honest reading is that this sample provides no
support for `RiskState` being a useful entry filter for `trend_pullback`,
and a weak, statistically fragile hint in the opposite direction.

A second, independent finding, not part of the original hypothesis but
worth stating plainly: **`trend_pullback` with default parameters has no
demonstrated edge on real MES 5-minute data over this period** — it lost
money in both the training and validation slices, with consistent
(non-overfit-looking) unprofitable behavior. This is useful ground truth
for this platform's stated mission independent of the context-gating
question.

## What this does and doesn't establish

- It does **not** mean `MarketContext`/`RiskState` are useless — one
  strategy, one composite dimension, one ~1-year sample, one fixed set of
  default parameters. It means *this specific* hypothesis, tested this way,
  did not hold up.
- It does **not** mean `trend_pullback` is a bad strategy design — its
  default parameters have never been tuned or walk-forward-optimized on
  this instrument; an unprofitable default-parameter run is expected, not
  alarming, and separate from whether context-awareness helps.
- It **does** demonstrate the full pipeline works end to end against real
  data and produces an honest, checkable verdict rather than a number
  someone has to trust on faith — which was the actual point of Phase 1/2.

## Recommended next step (not started — needs a decision)

Options, not mutually exclusive: (a) try a different context dimension for
`trend_pullback` (e.g. `market_regime`/`trend_state` alignment with the
strategy's own EMA-based trend filter, rather than a volatility-composite);
(b) try a different strategy/hypothesis pair (`opening_range_breakout` +
`LiquidityState`, `vwap_reversion` + `volatility_state` — both sketched in
`docs/ENGINEERING_REVIEW_2026-08.md`); (c) tune `trend_pullback`'s own
parameters first via the existing optimizer, since an unprofitable baseline
makes any entry filter's effect hard to read meaningfully; or (d) treat this
Critical-priority item as "mechanism proven, first hypothesis honestly
rejected" and move to the next `ROADMAP.md` priority. Not decided here —
this is exactly the kind of call the project's skeptic mandate says should
go back to a human, not get resolved by running a second test until one
looks better.
