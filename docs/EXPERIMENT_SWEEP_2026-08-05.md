# Experiment sweep — all four bundled strategies (2026-08-05)

Follow-up to [Experiment 1](EXPERIMENT_TREND_PULLBACK_RISK_STATE.md) (`trend_pullback`
+ `RiskState`, rejected) and [Experiment 2](EXPERIMENT_TREND_PULLBACK_REGIME_ALIGNMENT.md)
(`trend_pullback` + `market_regime`, inconclusive): three more context-gating
experiments (one per remaining bundled strategy) plus a parameter-tuning
sweep on `trend_pullback` itself, run together after being asked to "try
everything." Same pipeline throughout (`ContextGatedStrategy` →
`compare_context_impact` → `research/safety.py::build_safety_report`),
same shared harness (`tools/context_experiment_lib.py`), same real MES
5-minute data from `market_data.db` (~73,420 bars, ~13 months). Scripts:
`tools/experiment_context_orb_liquidity.py`,
`tools/experiment_context_vwap_reversion_volatility.py`,
`tools/experiment_context_ema_crossover_regime.py`,
`tools/tune_trend_pullback.py`.

## Experiment 3 — `opening_range_breakout` gated on `LiquidityState`

**Pre-registered hypothesis**: ORB entries without adequate volume behind
the break are more prone to false breakouts (a documented, generally
accepted failure mode). Requiring `LiquidityState != THIN` should filter
these out.

| | Trades | Net P&L | Win rate | Val profit factor |
|---|---|---|---|---|
| Baseline | 165 | -$887.10 | 31.5% | 1.03 |
| Gated | 143 | -$479.82 | 34.3% | **1.14** |

Train/validation: baseline train pf 0.63 (exp -$7.51/trade) → validation pf
1.03 (exp +$0.61); gated train pf 0.72 (exp -$5.42) → validation pf 1.14
(exp +$2.36). `research/safety.py`: baseline **Low** confidence (single
best trade = 165% of net profit); gated improves to **Medium** (single best
trade = 48% of net profit — still a real warning, but roughly a third as
dominant).

**Verdict: directionally the most encouraging result of the sweep, still
not trustworthy on its own.** Every headline number moved the hypothesized
direction and the safety rating genuinely improved (Low → Medium), which
didn't happen in any other experiment today. But "Medium confidence, one
warning" is not "validated" — a single trade still explains just under half
the gated result's profit. Worth a second, independent look (e.g. a longer
data window) before treating as real.

## Experiment 4 — `vwap_reversion` gated on `VolatilityState`

**Pre-registered hypothesis**: the strategy's own docstring already states
it fails when a market trends hard; `volatility_state in (HIGH, EXTREME)`
should correlate with those conditions, so excluding them should help.

| | Trades | Net P&L | Win rate |
|---|---|---|---|
| Baseline | 20 | **$925.20** | 40.0% |
| Gated | 11 | $240.11 | 54.5% |

The only baseline with a positive full-dataset net P&L across all five
experiments, and by far the highest profit factor (train 3.74, validation
3.39) — but on only 16 training / 4 validation trades. `research/safety.py`:
**Low confidence** for both variants, with the baseline alone carrying five
separate findings — insufficient trades both sides, 30% train→validation
degradation, and the single best trade accounting for **102% of training
profit and 84% of validation profit**. The gated variant is worse on every
count: net P&L collapses to $240.11, and single-trade dominance gets more
extreme (71% train, **200% validation** — meaning every other validation
trade combined lost money).

**Verdict: hypothesis rejected**, and independently — this is the important
part — **the baseline's attractive-looking profit factor is not
trustworthy either.** 20 trades in 13 months is too rare a signal to draw
any conclusion from; the platform's own significance floor
(`MIN_TRADES_FOR_SIGNIFICANCE = 30`) isn't cleared even by the full,
unfiltered dataset.

## Experiment 5 — `ema_crossover` gated on `market_regime` (avoid RANGING)

**Pre-registered hypothesis**: crossover systems whipsaw in range-bound
conditions; `market_regime != RANGING` should filter that out.

| | Trades | Net P&L | Win rate |
|---|---|---|---|
| Baseline | 178 | **-$3,465.72** | 12.4% |
| Gated | 19 | -$396.06 | 10.5% |

The worst baseline of any of the four strategies by a wide margin — 12.4%
win rate, training profit factor 0.18, and `research/safety.py`'s Low
confidence flags a single trade at **3352% of net profit** (the strategy is
a near-total loser propped up by one outlier). The regime filter cuts
entries 89% (178 → 19); total loss shrinks to -$396.06, but validation
collapses to a **single trade** (net -$28.74).

**Verdict: untestable, not "improved."** An 89% sample reduction leaving
one validation trade cannot support any conclusion in either direction —
`research/safety.py` correctly returns Low confidence on both training-size
and validation-size grounds. Independently: this strategy's own baseline is
badly unprofitable on this data, worse than any of the other three.

## Parameter tuning — `trend_pullback`'s own settings (not context-gating)

Run because Experiments 1-2 found the untuned baseline has no edge — 27
combinations across `adx_min` (15/20/25) × `atr_stop_mult` (1.0/1.5/2.0) ×
`atr_target_mult` (2.0/3.0/4.0), via the existing
`research/optimizer.py::run_optimization` (no context-gating involved).

**Best combination found**: `adx_min=15, atr_stop_mult=1.5,
atr_target_mult=4.0` — profit factor **1.11 training, 1.13 validation**,
net P&L +$205.90 / +$141.75. The only result across the entire sweep that
cleared profit factor > 1 in *both* splits.

`research/safety.py`'s verdict: **Low confidence**, four findings —

1. Single best trade = 80% of training net profit.
2. Single best trade = **141% of validation** net profit (every other
   validation trade combined *lost* money).
3. Only 2 of 26 other grid combinations scored anywhere close — "an
   isolated spike in the grid, not a robust region."
4. At 2x commission, net P&L drops 54% ($205.90 → $94.30).

**Verdict: not a validated edge, despite clearing both train and validation
profit-factor thresholds.** This is close to a textbook demonstration of
why `check_parameter_fitting`/`check_unrealistic_gains` exist: a
seemingly-positive out-of-sample result that is entirely explained by one
lucky trade, isolated in the parameter space, and highly cost-sensitive.
Trusting this because "it passed train and validation" would have been
exactly the mistake this platform's own methodology is built to prevent.

## The consolidated finding

Six runs — four strategies, five context-gating hypotheses, one 27-combination
parameter sweep — against the same real ~13-month MES 5-minute dataset.
**Zero results cleared `research/safety.py`'s own bar for trustworthiness.**

| Strategy | Baseline verdict | Best context-gating result |
|---|---|---|
| `trend_pullback` | Unprofitable, High confidence (i.e. reliably unprofitable) | Both hypotheses made it worse or left it untrustworthy; tuned "best" params are an isolated spike |
| `opening_range_breakout` | Unprofitable, Low confidence | Improved but still Medium/one warning |
| `vwap_reversion` | Best-looking profit factor, Low confidence (too few trades) | Made it worse |
| `ema_crossover` | Worst of the four, Low confidence | Sample collapsed, untestable |

This is not a failure of the Context Engine, `ContextGatedStrategy`, or the
optimizer — every one of those tools worked exactly as designed and caught
every case where a headline number looked better than the evidence behind
it actually supported. It is an honest answer to the question `config.yaml`
itself already poses: *"All are standard published setups or reference
implementations, not proven edges. Backtest before assuming anything."*
That disclaimer is accurate, as of this data, for all four strategies,
checked rigorously for the first time today.

## What this means for the roadmap

The Critical-priority item — "close the Context → Strategy → Validation
loop for real" — is now unambiguously *done* as a mechanism, exercised on
six independent runs, not one. What it isn't is a source of a validated
strategy yet. The real, un-glamorous finding is one level up: **this
platform's four reference strategies do not currently have a demonstrated
edge on the real data available**, with or without context-gating. Fixing
that is not a context-engineering problem — it's either (a) genuinely
different strategy logic than what's bundled today, (b) a longer/different
data window once more history is synced, or (c) accepting that "paper
strategies with no proven edge, run for research" is the honest current
state of this platform's trading capability, distinct from its very real
engineering capability. None of those is a decision for this session to
make unilaterally.
