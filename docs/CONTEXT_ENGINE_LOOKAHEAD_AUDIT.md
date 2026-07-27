# Market Context Engine — Look-Ahead Bias Audit

Performed as Phase 8, Part 4 ("Look-Ahead Bias Audit") of the Market
Context Engine's completion phase (2026-07-27). Read-only audit — no
production behavior was changed as a result; every module was already
built under this same discipline phase by phase (see `CHANGELOG.md`),
and this document consolidates that reasoning into one place plus
verifies it holds end to end.

**Standing convention every module in `context/` is held to:** ``bars``
is history up to and including the bar that just closed — the same
contract `Strategy.on_bar` has always used. No module in this package
ever reads real-time market data itself; every function receives
`timestamp`/`bars`/`bars_by_timeframe` as explicit arguments and derives
everything from those alone.

## Session (`context/session.py`)

**Not susceptible, by construction.** `classify_session` takes only a
`timestamp` and `symbol` — it never reads a single bar. Its only inputs
beyond the timestamp are `contracts.py`'s CME calendar rules (session
open/close times, the weekend closure window, the holiday list, the
daily maintenance halt) — all fixed, known in advance regardless of
when you ask, not derived from price/volume data at all. There is
nothing here that could leak "the future" because there is no market
data input to leak.

## Volatility (`context/volatility.py`)

**Not susceptible — verified directly by `TestNoFutureDataLeakage`
(`tests/test_context_volatility.py`).** `analyze_volatility` computes
`atr_series(bars, atr_period)` and reads `current_atr = atr_values[-1]`
plus `average_atr` from a trailing window `atr_values[-average_lookback:]`
— both always anchored at the *end* of whatever `bars` was given, never
reading past it. Deliberately does **not** reuse
`research.regime.classify_volatility`'s tercile approach as-is: that
function sorts the *entire* `bars` series up front to form its cutoffs,
which is correct for its own post-hoc, whole-backtest analytics use case
but would leak future volatility into an "as of timestamp T" reading if
reused verbatim for real-time classification. `analyze_volatility`
avoids that entirely by only ever computing a trailing statistic.

## Regime (`context/regime.py`)

**Not susceptible — verified directly by `TestNoFutureDataLeakage`
(`tests/test_context_regime.py`).** Composes three signals, each
independently trailing-only: `strategy.indicators.adx(bars, period)`
(a trailing Wilder computation), `research.regime.classify_trend(closes)`
(a start-to-end move over `closes[-_TREND_LOOKBACK:]`, the trailing
slice of whatever `closes` it's given), and `volatility.analyze_volatility`
(already proven safe above). None of the three sorts or aggregates the
whole series in a way sensitive to content beyond the input's own end.

## Multi-Timeframe Context (`context/timeframe.py`)

**The one dimension with a materially higher inherent risk — addressed
explicitly, and verified by a dedicated test constructing the exact
failure scenario.** Combining several *independent* bar streams (one
per timeframe) means a caller could realistically hand over a coarser
timeframe's series where the *last* bar is still forming — e.g. at
09:05, a 1-hour series' 09:00 bar has opened but not closed — even
though that bar's timestamp alone looks "at or before now." A naive
`bar.timestamp <= now` filter would wrongly accept it, leaking a
partial (or, in a backtest replaying stored data, potentially
clairvoyant) reading. `_completed_bars()` instead computes each
timeframe's actual duration and keeps a bar only once
`bar.timestamp + duration <= timestamp` — its close time has genuinely
passed. `TestAvoidsFutureLeakage.test_an_in_progress_bar_is_excluded_until_it_actually_closes`
constructs exactly this scenario and confirms the in-progress bar is
excluded until 10:00.

## Market Structure Context (`context/structure.py`)

**Not susceptible — but requires the most careful explanation, because
swing-point confirmation genuinely does look at bars *after* a
candidate point.** A bar's high/low only counts as a confirmed swing
once `swing_window` bars *on both sides* fail to exceed it — including
bars chronologically after the candidate. This is confirmation **lag**,
not leakage: every bar involved, both the candidate and its
confirmation bars, is already part of the given `bars` argument, which
itself must end at or before `timestamp` per the standing convention
above. Nothing outside that argument is ever read. The practical,
honest consequence is that the most recent `swing_window` bars simply
have no confirmed swing near them yet — verified directly by
`TestNoFutureDataLeakage`/`TestConfirmationLagIsNotFutureLeakage` in
`tests/test_context_structure.py` (a shorter prefix's reading is
unaffected by bars appended after it) and by
`test_the_most_recent_bars_have_no_confirmed_swing_yet`.

## Trend State (`context/trend.py`)

**Not susceptible — verified directly by a dedicated no-leakage test
(`tests/test_context_trend.py`).** Same reuse chain and same
trailing-only shape as `regime.py`: direction from
`research.regime.classify_trend`'s trailing closes window, confidence
from `strategy.indicators.adx`'s trailing computation over the given
`bars`. Nothing here reads anything `regime.py` doesn't already prove
safe.

## Liquidity State (`context/liquidity.py`)

**Not susceptible — verified directly by a dedicated no-leakage test
(`tests/test_context_liquidity.py`).** The trailing-average shape is
identical to `volatility.py`'s: `sma(volumes, period=min(lookback,
len(volumes)))` always ends at the last bar given (current-bar-
inclusive, same convention), never anything beyond it.

## Risk State (`context/risk.py`)

**Not susceptible, by construction — reads no bars at all.**
`assess_risk` is a pure composite of two *already-computed* enum values
(`volatility_state`, `market_regime`), each already proven look-ahead-
safe above. Since this module has no bar-reading logic of its own, it
cannot introduce new leakage; it can only inherit whatever safety its
two inputs already carry.

## Environment Score (`context/scoring.py`)

**Not susceptible, by construction — also reads no bars at all.**
`score_environment` reads only the fields of an already-built
`MarketContext`, every one of which was already computed look-ahead-
safely by the modules above. `EnvironmentScore` is additionally
computed strictly *after* the rest of `MarketContext` (via
`with_environment_score`'s two-step `dataclasses.replace` construction
in `ContextEngine.build_context`), so there is no path for it to read
anything not already finalized as of `timestamp`.

## Conclusion

No look-ahead risk was found in any of the eight context dimensions or
the combined Environment Score. No code changes were required as a
result of this audit — every module was already built under the
"trailing-window-only" / "confirmation lag is not leakage" / "no bars
read at all" disciplines documented above, each with its own dedicated
`TestNoFutureDataLeakage`-style test. This document exists to make that
reasoning explicit and auditable in one place, per Phase 8's own
instructions, rather than scattered across per-module docstrings.
