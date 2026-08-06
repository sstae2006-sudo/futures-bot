# Experiment 2 — `trend_pullback` gated on `market_regime` alignment (2026-08-05)

Second hypothesis run through the same pipeline as
[Experiment 1](EXPERIMENT_TREND_PULLBACK_RISK_STATE.md) (`RiskState`,
rejected): `ContextGatedStrategy` → `compare_context_impact` →
`research/safety.py::build_safety_report`, shared harness in
`tools/context_experiment_lib.py`. Script:
`tools/experiment_context_trend_pullback_regime_alignment.py`.

## Pre-registered hypothesis

Stated in the script's own docstring *before* it was run:

> `trend_pullback`'s own trend filter is a blunt, binary check
> (`ema50 > ema200 and close > ema200` for a long, the mirror for a short)
> that says nothing about trend *strength* or whether volatility conditions
> still support calling it a real trend. The Context Engine's
> `market_regime` (`classify_regime`: TRENDING_UP/TRENDING_DOWN/RANGING/
> HIGH_VOLATILITY/LOW_VOLATILITY, combining an ADX≥25 strength threshold,
> trend direction, and the volatility signal — a materially different, more
> selective computation than the strategy's own bare EMA order) is
> hypothesized to be a stronger confirmation of a genuine trend. Requiring
> the Context Engine's regime to independently agree with the entry's
> direction is hypothesized to filter out weak/borderline EMA-cross entries
> and improve win rate or expectancy, likely at a further cost in trade
> count.

## Method

Identical to Experiment 1 (real MES 5-minute bars from `market_data.db`,
`config.yaml`'s real settings, untuned `trend_pullback` defaults,
chronological 70/30 split, context-mode-aware commission shock) — see that
doc for the full method writeup. Only the scorer differs: a custom
`regime_alignment_scorer` (not expressible with `reject_if`/`require_if`,
since the required regime depends on the entry's own direction —
`ContextScorer`'s signature already supports reading the `Signal`, so this
needed no interface change).

## Results

### Full dataset (`compare_context_impact`)

| | Trades | Net P&L | Win rate |
|---|---|---|---|
| Baseline (unfiltered) | 92 | -$305.33 | 46.7% |
| Gated (regime alignment required) | 25 | **-$223.50** | **48.0%** |

68 of 92 trades changed — the filter is far more restrictive than
Experiment 1's (down to 25 trades, a 73% reduction, vs. 47 for `RiskState`).
Read in isolation, this headline looks like a modest improvement.

### Train/validation split

| | Trades | Net P&L | Profit factor | Expectancy/trade | Max drawdown |
|---|---|---|---|---|---|
| Baseline — train | 56 | -$178.19 | 0.87 | -$3.18 | $423.47 |
| Baseline — validation | 36 | -$127.14 | 0.85 | -$3.53 | $343.54 |
| Gated — train | 12 | -$232.38 | **0.46** | **-$19.36** | $232.38 |
| Gated — validation | 13 | +$8.88 | 1.03 | +$0.68 | $199.94 |

Train got materially *worse* (profit factor 0.46, expectancy -$19.36 — the
worst per-trade number in either experiment); validation flipped to barely
positive (profit factor 1.03, expectancy +$0.68 — essentially breakeven).
That train/validation inversion, on buckets this small, is the shape small-
sample noise produces, not the shape a real edge produces.

### `research/safety.py` verdict: **Low confidence**, three findings

1. Only 12 training trades (below 30) — the parameters were ranked on very
   little evidence.
2. Only 13 validation trades (below 20) — the out-of-sample check itself is
   close to noise.
3. **The single best trade produced 1675% of net profit** — remove it and
   the result looks materially different.

Finding 3 is the load-bearing one: the gated variant's entire "improvement"
over baseline is not a broad-based effect across 25 trades — it is
overwhelmingly one outlier trade. `research/safety.py` exists specifically
to catch a headline number that looks better while the evidence underneath
it is this thin, and it did.

## Verdict: inconclusive — not enough trustworthy evidence to accept or reject

Unlike Experiment 1 (a clean, interpretable negative result on reasonably
sized buckets), this filter is too restrictive to produce a sample worth
drawing a conclusion from. Requiring *both* the strategy's own EMA trend
filter *and* the Context Engine's stricter ADX+volatility-corroborated
regime classifier to agree cuts entries by 73%; what's left is dominated by
a single trade and falls well under this platform's own significance
thresholds on both sides of the split. The improved full-dataset headline
number is real arithmetic, not a mistake — but treating it as evidence the
hypothesis is *true* would be exactly the mistake `research/safety.py`
exists to prevent. This experiment neither confirms nor refutes whether
regime alignment could help `trend_pullback`; it shows that *requiring
exact agreement* (`threshold=1.0`) is too strict a bar to test meaningfully
on ~13 months of this instrument's data.

## What would be needed to actually test this hypothesis

Not started — options, not mutually exclusive: (a) a longer real-data
window, if/when more MES history is synced, to give the filtered bucket a
real sample size; (b) a softer version — score partial agreement (e.g.
`market_regime` merely *not opposing* the direction, rather than requiring
the specific TRENDING_UP/TRENDING_DOWN match) to reduce how much the filter
removes; (c) test regime alignment on a strategy with a higher baseline
trade count than `trend_pullback`'s 92, so a comparably selective filter
still leaves an evaluable sample. Same discipline as Experiment 1: this is
a decision point, not something to resolve by immediately trying another
variant until one looks clean.

## Engineering note: refactor verified

`tools/context_experiment_lib.py` was factored out of Experiment 1's script
before this experiment was written (avoiding a second ~130-line duplicate
harness). Experiment 1 was re-run against the shared lib to confirm the
refactor changed nothing: identical trade counts, net P&L, win rates,
profit factors, and `research/safety.py` findings as the original run (the
tiny bar-count difference, 73,411 vs. 73,312, is `market_data.db`'s
background sync adding ~99 bars to the tail between runs, entirely after
both runs' train/validation cut points — it did not change either run's
trade-level results).
