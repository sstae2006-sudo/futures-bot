"""Context-aware strategy wrapper -- Phase 1 of wiring the Market Context
Engine into the Strategy Engine (see docs/ARCHITECTURE.md's "Market Context
Engine" section and ROADMAP.md's Context Engine phase list).

``ContextGatedStrategy`` composes any existing ``Strategy`` with a
``ContextScorer`` -- a pluggable rule that scores one entry signal against
the current ``MarketContext``. Nothing about an existing strategy changes:
this is a wrapper, not a modification, so every bundled strategy
(``ema_crossover``, ``opening_range_breakout``, ``vwap_reversion``,
``trend_pullback``) stays exactly as it is. Wrap one in
``ContextGatedStrategy`` to experiment with context-conditioned entries
without touching its source, its warmup behavior, or its exit/stop-management
logic (none of which this wrapper ever looks at).

**Why a scoring interface, not a plain allow/deny predicate.** A bare
``Callable[[MarketContext], bool]`` would only ever answer one question (veto
or not). ``ContextScorer`` instead returns a ``ContextScore`` -- a
``[0.0, 1.0]`` confidence value plus a reason -- so the *same* interface can
later back position sizing or confidence-weighted entries (a future phase,
not this one) without this module's shape changing. Phase 1 only ever
consumes the score as a binary veto (see ``ContextGatedStrategy.on_bar``'s
threshold check), and every scorer built here (``reject_if``/``require_if``/
``all_of``/``any_of``) only ever returns exactly ``0.0`` or ``1.0`` -- "binary
pass/fail rules for the experiments" is this phase's own explicit scope. A
future phase adding real confidence-weighted sizing would read
``ContextGatedStrategy.last_score`` (already populated, already carrying a
continuous value) rather than needing to touch this interface at all.

**Deliberately not surfaced through ``Signal.metadata``.** Other parts of
this codebase already read ``signal.metadata`` for their own purposes (the ML
``signal_filter`` in ``research_server/paper_trader.py``, ML feature
extraction in ``research/features.py``) -- adding new keys there risks
silently changing what those systems see. ``last_score`` is a plain instance
attribute instead, read directly off the strategy object, with zero effect on
anything that inspects a ``Signal``.

**Not registered with ``StrategyRegistry``.** This is a composition tool for
research/backtesting -- e.g. as the ``strategy_factory`` passed to
``backtest.context_comparison.compare_context_impact`` -- not a
config.yaml-selectable strategy in its own right. Selecting a real strategy
and rule to run this way is a trading-hypothesis decision, made by whoever
calls this module, not something built into it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from ..models import Bar, Position, Signal, SignalAction
from .base import Strategy

if TYPE_CHECKING:
    # Deferred: context/*.py doesn't import strategy/ at all, so a real
    # import here wouldn't actually be circular today -- but this stays
    # TYPE_CHECKING-only anyway, matching the exact precedent strategy/base.py
    # already set for its own `self.context` attribute (Strategy is a
    # protected interface; context_gate.py isn't, but there's no reason to
    # carry a real runtime dependency on context/ here either).
    from ..context.models import MarketContext


@dataclass(frozen=True)
class ContextScore:
    """One scorer's verdict on one entry signal, given the current
    ``MarketContext``.

    ``value`` is a confidence-like scalar in ``[0.0, 1.0]``. Phase 1 only
    ever produces ``0.0`` (veto) or ``1.0`` (pass) -- see the module
    docstring -- but the type itself does not enforce that, deliberately, so
    a future scorer returning a real continuous confidence needs no shape
    change here. ``reason`` is always required and non-empty, the same
    "always give a reason" discipline ``Strategy.on_bar``'s own docstring
    already holds every ``Signal`` to.
    """

    value: float
    reason: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"ContextScore.value = {self.value} is out of range [0.0, 1.0]")
        if not self.reason.strip():
            raise ValueError("ContextScore.reason must be a non-empty string.")


#: A pluggable rule: given the entry signal about to be considered and the
#: current MarketContext, return a ContextScore. Implementations must be pure
#: functions of their two arguments -- no broker/risk/engine reference, the
#: same hard boundary every ``context/`` module is already held to (see
#: docs/ARCHITECTURE.md's "Market Context Engine" section).
ContextScorer = Callable[[Signal, "MarketContext"], ContextScore]


class ContextGatedStrategy(Strategy):
    """Wraps ``inner`` with a ``ContextScorer``-driven veto over its entry
    signals.

    Only ``ENTER_LONG``/``ENTER_SHORT`` signals from ``inner`` are ever
    scored; every ``HOLD``/``EXIT`` (including a stop-management HOLD that
    carries ``stop_loss``, e.g. ``trend_pullback``'s trailing-stop requests)
    passes straight through untouched, and the scorer is not called for one.

    ``threshold`` (default ``1.0``, i.e. only a perfect score passes) is this
    phase's consumption of ``ContextScore.value``: below it, the entry is
    converted to a ``HOLD``. A future phase adding confidence-weighted sizing
    would consume the same ``ContextScore`` differently (e.g. scale
    ``quantity`` by ``value`` instead of/alongside thresholding) without this
    class's veto behavior or ``ContextScorer``'s interface needing to change.
    """

    uses_context = True

    def __init__(
        self,
        contract,
        inner: Strategy,
        scorer: ContextScorer,
        threshold: float = 1.0,
        **params,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold = {threshold} is out of range [0.0, 1.0]")
        super().__init__(contract, **params)
        self.inner = inner
        self.scorer = scorer
        self.threshold = threshold
        # Instance-level, not the class default of 0 -- every bundled
        # strategy computes its own `warmup_bars` in `__init__` (params-
        # dependent, e.g. trend_pullback's depends on its EMA/ADX/slope
        # settings), so this has to be read off the already-constructed
        # `inner` rather than assumed.
        self.warmup_bars = inner.warmup_bars
        #: The most recent ContextScore computed, or None if no entry signal
        #: has been scored yet this bar (inner returned a HOLD/EXIT, or no
        #: context was available). Reset at the start of every `on_bar` call.
        #: The seam a future position-sizing phase would read -- see the
        #: module docstring.
        self.last_score: Optional[ContextScore] = None

    def on_start(self) -> None:
        self.inner.on_start()

    def on_stop(self) -> None:
        self.inner.on_stop()

    def on_bar(self, bars: Sequence[Bar], position: Optional[Position]) -> Signal:
        self.last_score = None
        signal = self.inner.on_bar(bars, position)

        if signal.action not in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
            return signal
        if self.context is None:
            # ENABLED mode but context generation failed this bar (see
            # engine._build_market_context's broad except), or this wrapper
            # is being exercised outside ContextMode.ENABLED entirely --
            # either way there's nothing to score against. Let the inner
            # signal through unchanged rather than silently vetoing on
            # missing data.
            return signal

        score = self.scorer(signal, self.context)
        self.last_score = score
        if score.value < self.threshold:
            return self.hold(
                f"[context-gate] veto ({score.value:.2f} < {self.threshold:.2f} threshold): "
                f"{score.reason}"
            )
        return signal


# --- A small library of binary (0.0/1.0) scorers -- "pass/fail rules for the
# experiments" per this phase's own scope. Each is a pure function of
# (signal, context); the combinators below let several be composed without a
# new class per combination. ---


def reject_if(predicate: Callable[["MarketContext"], bool], reason: str) -> ContextScorer:
    """Veto (0.0) whenever ``predicate(context)`` is True, otherwise pass (1.0)."""

    def scorer(signal: Signal, context: "MarketContext") -> ContextScore:
        if predicate(context):
            return ContextScore(0.0, reason)
        return ContextScore(1.0, "reject_if condition not met")

    return scorer


def require_if(predicate: Callable[["MarketContext"], bool], reason: str) -> ContextScorer:
    """Pass (1.0) only when ``predicate(context)`` is True, otherwise veto (0.0)."""

    def scorer(signal: Signal, context: "MarketContext") -> ContextScore:
        if predicate(context):
            return ContextScore(1.0, "require_if condition met")
        return ContextScore(0.0, reason)

    return scorer


def all_of(*scorers: ContextScorer) -> ContextScorer:
    """Combine several scorers into one: the minimum of their scores (any
    single veto vetoes the whole combination).

    Generalizes correctly beyond binary 0.0/1.0 scorers -- the minimum of
    several ``[0.0, 1.0]`` confidence values is still a sound "weakest link"
    combination -- so this combinator needs no changes when a future
    non-binary scorer is added.
    """

    def scorer(signal: Signal, context: "MarketContext") -> ContextScore:
        results = [s(signal, context) for s in scorers]
        return min(results, key=lambda r: r.value)

    return scorer


def any_of(*scorers: ContextScorer) -> ContextScorer:
    """Combine several scorers into one: the maximum of their scores (any
    single pass is enough)."""

    def scorer(signal: Signal, context: "MarketContext") -> ContextScore:
        results = [s(signal, context) for s in scorers]
        return max(results, key=lambda r: r.value)

    return scorer
