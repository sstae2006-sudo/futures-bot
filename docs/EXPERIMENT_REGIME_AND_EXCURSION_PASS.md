# Free diagnostics pass — MAE/MFE efficiency + regime performance (2026-08-06)

Phase 0 of the professional-research-pipeline plan: persist the six
experiments/sweep already run on 2026-08-05
(`docs/EXPERIMENT_TREND_PULLBACK_RISK_STATE.md`,
`docs/EXPERIMENT_TREND_PULLBACK_REGIME_ALIGNMENT.md`,
`docs/EXPERIMENT_SWEEP_2026-08-05.md`) to a scratch database, then run two
diagnostics that need no new backtests — `api/analytics.py::compute_excursions`
(MAE/MFE, already fully built) and `GET /api/regime/performance`'s
underlying grouping logic (`api/services.py::_regime_buckets`, also
already built) — against real, already-computed trades. This is
diagnostic, not hypothesis-testing: nothing here went through a train/
validation split or `research/safety.py`, and nothing here should be
treated as more than a lead worth a properly pre-registered follow-up.

**Data**: `research_experiments.db`, populated by re-running all five
`tools/experiment_context_*.py` scripts and `tools/tune_trend_pullback.py`
with `store=`/`resolution=` now wired through. Real MES 5-minute bars from
`market_data.db`, identical dataset every prior 2026-08-05 experiment used.
767 trades across 4 strategies (baseline + context-gated variant each).

**Data-quality note, corrected before any of the numbers below were
computed**: one experiment script (`trend_pullback` + `RiskState`) was
interrupted mid-run while its true cost was being diagnosed (see
KNOWN_ISSUES.md ISSUE-046) and re-run cleanly from scratch. The
interrupted attempt had already persisted three of its four slices before
being killed, and the clean re-run persisted the same trades again under
the same `run_id` — `TradeStore.insert_trades` has no dedup protection
(see KNOWN_ISSUES.md ISSUE-047, newly filed from this discovery). Found by
checking for exact-duplicate `(entry_time, exit_time, net_pnl)` triples,
confirmed as a clean 2x duplication (not partial/coincidental overlap),
and removed directly from this scratch database before any diagnostic
below was run. Verified fix: `trend_pullback`'s baseline now matches
byte-for-byte between the two independent experiment scripts that both
ran it against the same data.

## Diagnostic 1 — MAE/MFE efficiency

`efficiency = realized_points / mfe_points` per trade (can go arbitrarily
negative for a trade that moved favorably then reversed hard before
exiting — this is not a bounded 0-1 "how much of the move you captured"
score, it also captures how much was given back).

| Strategy | n | Win median efficiency | Loss median efficiency | Loss mean efficiency |
|---|---:|---:|---:|---:|
| ema_crossover | 80 | 0.72 | -1.42 | -3.20 |
| opening_range_breakout | 152 | 0.86 | -1.34 | -2.83 |
| trend_pullback | 168 | 0.71 | -1.90 | -2.52 |
| vwap_reversion | 21 | 0.89 | -0.65 | -0.70 |

**Reading this**: winning trades across every strategy capture roughly
55-90% of their own maximum favorable excursion — exit timing on winners
looks reasonable, not the problem. Losing trades are the story: a median
losing trade gave back 1.3-1.9x its own peak favorable move before
finally closing at a loss, and the mean is worse (-2.5 to -3.2x),
meaning a handful of trades that ran deep into favorable territory before
reversing hard are dragging the average down further than the median
suggests. This is a genuinely different lever than anything the five
2026-08-05 context-gating experiments tested (which only gated *entries*,
never touched *exits*) — it points at stop/trail management (tighter
trailing stops, a breakeven-stop trigger closer to entry, or a hard
time-based exit once a trade has given back most of an early favorable
move) as a real, unexplored direction. **Not validated** — this is a
descriptive pattern in the data, not a tested hypothesis; a real test
would need a concrete exit-rule change run through the same train/
validation/`research/safety.py` pipeline as everything else.

## Diagnostic 2 — regime performance

Grouping every persisted trade by its entry-time regime labels
(`regime_trend`/`regime_volatility`/`regime_session`), looking for any
bucket with `n >= MIN_TRADES_FOR_SIGNIFICANCE` (30) whose profit factor
would survive the same scrutiny `research/safety.py` applies elsewhere.

**`ema_crossover`, `opening_range_breakout`**: every bucket checked
(`regime_trend`/`regime_volatility`/`regime_session`, `n >= 15`) is
net-negative. No lead here — consistent with these two already being the
weakest baselines in the 2026-08-05 sweep.

**`trend_pullback`**: the one strategy with a real single-dimension
positive bucket at meaningful size:

| Dimension | Value | n | Net P&L | Win rate |
|---|---|---:|---:|---:|
| `regime_trend` | bearish | 28 | +$647.78 | 71.4% |
| `regime_session` | open | 100 | +$526.00 | 52.0% |
| `regime_volatility` | low | 18 | +$155.18 | 66.7% |

The *intersection* of the two largest positive dimensions
(`regime_trend=bearish` AND `regime_session=open`) is smaller and more
extreme:

| regime_trend | regime_session | n | Net P&L | Win rate |
|---|---|---:|---:|---:|
| bearish | open | 12 | +$602.62 | 83.3% |
| bearish | close | 14 | +$177.64 | 71.4% |

**This is a lead, explicitly not a finding — read the caveats before
acting on it.** `regime_trend=bearish` alone sits right at the edge of
`MIN_TRADES_FOR_SIGNIFICANCE` (28, one below 30); the bearish+open
intersection (the more striking 83.3% win rate) has only 12 trades, well
under any threshold this platform treats as meaningful anywhere else.
This diagnostic checked 3 single dimensions × up to ~4 values each, across
4 strategies, then an ad hoc 2-dimension intersection on top — that's
dozens of implicit comparisons in one pass. A good-looking bucket
appearing somewhere in that many slices is close to expected by chance
alone (the same multiple-testing concern `research/safety.py::check_trial_inflation`,
built earlier in this same session, exists to formalize) — this exact
number should itself count as a trial if it's ever pursued further, not
be treated as free evidence because it came from a "diagnostic" instead
of a "hypothesis test."

**`vwap_reversion`**: no bucket reaches `n >= 15` at all (21 trades
total) — same "too rare a signal to conclude anything" verdict the
2026-08-05 sweep already reached for this strategy, confirmed again here.

## Recommended next step, not decided here

If `trend_pullback` + bearish-regime is worth pursuing, the honest next
step is a **pre-registered hypothesis** ("trend_pullback entries during
`MarketRegime` bearish conditions are hypothesized to outperform, tested
via `ContextGatedStrategy` gating on `market_regime == BEARISH`") run
through the exact same pipeline as the 2026-08-05 experiments — train/
validation split, `research/safety.py::build_safety_report`, and now
(built this session) `check_trial_inflation` counting this as one more
trial against an already-substantial count for this strategy/dataset
pair. Not decided here whether that's worth doing before or after Phase 4
(widening the search) — a decision for the next session/human, matching
how every other open question in this plan has been left.
