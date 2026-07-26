# Strategy Guide

All four strategies below are standard, published setups implemented
properly and tested — not a discovered edge. Nothing here is a
recommendation to trade any of them. Backtest against your own data and
account size before trusting any of it (see
[TRADING_WORKFLOW.md](TRADING_WORKFLOW.md)).

Every strategy shares the same contract: `on_bar(bars, position)` looks at
price history up to and including the bar that just closed and returns a
`Signal` — a decision plus a plain-English reason, never an order. See
[ARCHITECTURE.md](ARCHITECTURE.md) for why that boundary exists.

---

## EMA Crossover (`ema_crossover`)

**Concept.** The oldest trend-following idea there is: when a fast moving
average crosses above a slow one, the recent trend is up; trade in that
direction. This implementation adds two filters on top of the raw cross,
because a raw cross alone whipsaws constantly in a ranging market:

1. A 200-period EMA trend filter — only take the cross if price is already
   on the correct side of the long-term trend.
2. A minimum-distance filter — ignore crosses where the fast and slow EMAs
   are too close together to mean anything (`min_ema_distance`).
3. A trend-slope filter — the 200 EMA itself has to be rising (for longs)
   over the last 5 bars, not just be below price.

Shorts are intentionally disabled in the current implementation (see the
commented-out block in `ema_crossover.py`) — this is a long-only reference
build.

**Parameters** (`strategy_params`):

| Name | Default | Meaning |
| --- | --- | --- |
| `fast_period` | 8 | Fast EMA length |
| `slow_period` | 34 | Slow EMA length |
| `trend_period` | 200 | Long-term trend filter EMA length |
| `min_ema_distance` | 1.5 | Minimum points between fast/slow to act on a cross |

**Strengths.** Simple, well-understood, cheap to compute, and the trend
filter meaningfully cuts down on trading against the dominant move. Good as
a baseline to compare other strategies against.

**Weaknesses.** Moving-average crossovers lag by construction — the signal
only appears after the move that caused it. In a choppy market the fast/slow
distance filter helps but doesn't eliminate whipsaws, and a 200-period
warmup means it needs a long history before it says anything at all
(`warmup_bars = trend_period + 5`). Long-only in this build means it
structurally cannot participate in downtrends.

---

## Opening Range Breakout (`opening_range_breakout`)

**Opening range.** The high and low of the first `range_minutes` of the
session (default: the first 30 minutes after `session_start_ct`, which
defaults to 08:30 CT — the RTH open). This range is treated as a
support/resistance zone: what happens when price leaves it is the signal.

**Breakout logic.** Once the range is built, a bar whose close (or high/low,
if `require_close_beyond` is `false`) moves beyond the range triggers an
entry in that direction — long above the high, short below the low — but
only within the configured entry window (`earliest_entry_ct` to
`latest_entry_ct`) and only once per session per direction.

**Filters:**

- `min_range_points` / `max_range_points` — skip days where the range is
  too tight to mean anything, or already made its move before the entry
  window opens.
- A 200-period EMA trend filter — the breakout must agree with the
  longer-term trend direction.
- `max_entries_per_session` — how many breakouts to act on before going
  flat for the rest of the session.
- `allow_long` / `allow_short` — disable a direction entirely.
- `stop_at_range_opposite` — when `true`, the protective stop sits at the
  *opposite* side of the range (long stop at the range low, short stop at
  the range high) instead of the config-wide `risk.stop_loss_points`. This
  makes the stop distance a function of that day's actual range rather
  than a fixed number of points, at the cost of a stop that can be
  considerably wider (or narrower) than the configured default on any
  given day.

The strategy also tracks **missed breakouts** (`self.missed_breakouts`) —
breakouts that happened but weren't taken, broken down by which filter
declined them (time window, EMA trend, direction disabled) — useful when
reviewing why a promising-looking day produced no trade.

**Failure cases.** ORB is a momentum bet: it buys strength and sells
weakness, on the assumption that a break of the opening range continues. On
a day that reverses right after the breakout (a classic "fakeout"), this
strategy takes the loss the mean-reversion strategy below would have
profited from. On random-walk data it loses money — that's the correct
signature for a breakout strategy with no real trend to catch, not a bug.
It's also sensitive to the entry-window/range-window relationship: if
`latest_entry_ct` is at or before the range finishes building, it can never
trade at all (this specific case is one of `Settings.strategy_warnings()`'s
checks — see [RESEARCH_GUIDE.md](RESEARCH_GUIDE.md)).

---

## VWAP Reversion (`vwap_reversion`)

**Mean reversion concept.** VWAP is the price at which the session's volume
has actually transacted, which is why price tends to be drawn back toward
it intraday — it's closer to a fair-value estimate than any single moving
average. This strategy fades extensions: when price closes more than
`std_devs` standard deviations away from session VWAP, it takes the
opposite side, targeting a return to VWAP (`exit_at_vwap`, on by default).

VWAP and its bands reset at the CME session boundary (17:00 CT), not
midnight, and the strategy waits `min_bars` into the session before trading
at all — early VWAP is computed from a handful of bars and the bands are
close to meaningless until some volume has accumulated.

**Parameters:**

| Name | Default | Meaning |
| --- | --- | --- |
| `std_devs` | 2 | How far price must stretch before fading it |
| `min_bars` | 20 | Bars into the session before trading starts |
| `exit_at_vwap` | true | Exit target is the return to VWAP |
| `max_entries_per_session` | 3 | Cap on fades per session |

**Risks.** Stated plainly in the module's own docstring: *mean reversion
sells strength and buys weakness, so it does badly precisely when a market
trends hard in one direction all session.* Fading a trend day is how this
strategy loses, and no parameter setting removes that — it's what the
strategy fundamentally is. Because it can re-enter up to
`max_entries_per_session` times, a bad trend day can compound: fade, get
run over, fade again. The daily loss limit in `config.yaml`'s `risk`
section matters more here than for a trend-following strategy for exactly
this reason.

---

## Trend Pullback (`trend_pullback`)

The most elaborate of the four, implemented as a package
(`strategy/trend_pullback/`) rather than one file — see
[ARCHITECTURE.md](ARCHITECTURE.md) for the module breakdown. It never
enters on a raw signal; every entry is a multi-step sequence:

1. **Trend regime.** EMA50 vs. EMA200, plus price on the correct side of
   EMA200 — establishes a bullish or bearish regime before anything else is
   considered.
2. **Pullback.** Waits for price to retrace to EMA21, then for a candle in
   the trend's direction to close inside that zone (`pullback_distance`).
   The entry doesn't fire on that candle — the *next* bar's break of that
   candle's high/low is the actual trigger, confirmed with the filters
   below re-checked at that exact moment. A setup that goes stale
   (`max_arm_bars`) is discarded.
3. **Filters at the trigger moment:**
   - **EMA** — trend regime still holds
   - **RSI** — `rsi_long_min` / `rsi_short_max`, confirming momentum agrees
     with the trade direction
   - **ADX** — `adx_min`, a floor on trend strength (a low ADX means the
     "trend" in step 1 may just be noise)
   - **Volume** — must clear `volume_multiplier` × its 20-bar average
   - **ATR** — `atr_min`, skips bars too quiet to trust
   - **VWAP** — distance from session VWAP is recorded as entry context
   - Optional `trading_sessions` time-of-day windows

**Exits**, checked every bar a position is open:

- ATR-scaled initial stop and target (`atr_stop_mult` / `atr_target_mult`)
- A trailing stop that follows price by `trailing_atr_mult` × ATR
- A breakeven ratchet once profit clears `breakeven_trigger_points`
- An EMA9/EMA21 reversal exit (`ema_reversal_enabled`)
- A VWAP-loss exit — flat if price falls back through session VWAP
  (`vwap_loss_enabled`)
- A max-bars-in-trade forced exit (`max_bars_in_trade`)

Trailing and breakeven stops rest at the broker, not just in the strategy's
own bookkeeping — a stop tracked only in-process disappears the moment the
process does.

**Strengths.** The multi-filter design is deliberately conservative: it
trades less often than the other three strategies, but each entry has
several independent confirmations. The incremental indicator design
(`rolling.py`) means it doesn't pay the O(n²) cost the other three used to
before Phase 4 — see [ARCHITECTURE.md](ARCHITECTURE.md#performance-incremental-vs-batch-indicators).

**Weaknesses.** More parameters means more ways to overfit a search to —
`optimize.py`'s default grid for this strategy already sweeps 4 dimensions
(54 combinations); a finer grid multiplies fast, and every extra
combination tried makes the best-looking training result less trustworthy
(see [RESEARCH_GUIDE.md](RESEARCH_GUIDE.md)'s overfitting section). It also
only ever sizes stops from ATR, so per-trade R-multiples in reports are
approximated against the configured `stop_loss_points` rather than the
actual stop distance used — the report notes this explicitly when this
strategy is active.
