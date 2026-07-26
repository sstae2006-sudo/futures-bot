"""Phase 8B: degradation / regime-drift / parameter-recommendation
findings -- surfaced, never applied.

Same rule Phase 6B's `api.services.generate_insights` already follows:
every finding is a direct read of a number already computed and stored,
never a new statistical model or anything that could phrase a guess as a
fact. Computed on demand by the dashboard route (`api/research_server
_service.py`), not a background job -- there's no new persisted "log"
table to keep consistent, matching this phase's explicit scope boundary.

Returns plain dicts shaped exactly like `api.schemas.InsightOut`
(``strategy``, ``category``, ``message``, ``severity``, and an optional
``details`` dict -- Phase 10.2's structured payload behind each finding's
detail window, e.g. a recommendation's ``current_params``/
``recommended_params``/``run_id``) rather than importing that schema
directly -- keeps this module free of any dependency on `api`, unlike
`nightly_jobs.py` (which genuinely needs `api.services`' job-submission
functions and has no lower-level equivalent to call instead).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..research.trade_store import TradeStore, default_db_path

#: Below this many live trades, a regime-drift comparison is mostly noise
#: -- the same reasoning `backtest.metrics.MIN_TRADES_FOR_SIGNIFICANCE`
#: already states for a whole backtest, just at a smaller live-sample scale
#: since a live paper session naturally accumulates trades far slower.
_MIN_LIVE_TRADES_FOR_REGIME_CHECK = 5


def degradation_findings(strategy: str) -> list[dict]:
    """Compares the strategy's most recent completed live (`kind='live'`)
    run's expectancy against its own best historical backtest/walk-forward
    run. Flags only the clearest case -- live negative while the historical
    best was positive -- rather than a fuzzier statistical test, so every
    finding traces to two numbers a reader can go look up themselves."""
    store = TradeStore(default_db_path())
    try:
        live_runs = [r for r in store.fetch_runs(strategy=strategy, kind="live", limit=10) if r["status"] == "completed"]
        if not live_runs:
            return []
        latest_live = live_runs[0]
        if latest_live["expectancy"] is None or latest_live["trade_count"] in (None, 0):
            return []

        historical = [
            r for r in store.fetch_runs(strategy=strategy, limit=200)
            if r["kind"] in ("backtest", "walk_forward") and r["status"] == "completed" and r["expectancy"] is not None
        ]
        if not historical:
            return []
        best_historical = max(historical, key=lambda r: r["expectancy"])

        if latest_live["expectancy"] < 0 and best_historical["expectancy"] > 0:
            return [{
                "strategy": strategy, "category": "degradation", "severity": "warning",
                "message": (
                    f"{strategy}: live paper trading expectancy is ${latest_live['expectancy']:.2f}/trade "
                    f"(negative, over {latest_live['trade_count']} trade(s)) versus a historical backtest "
                    f"expectancy of ${best_historical['expectancy']:.2f}/trade (run {best_historical['id']}). "
                    f"Live performance has diverged from backtested expectations."
                ),
                "details": {
                    "live_run_id": latest_live["id"], "live_expectancy": str(latest_live["expectancy"]),
                    "live_trade_count": latest_live["trade_count"],
                    "historical_run_id": best_historical["id"],
                    "historical_expectancy": str(best_historical["expectancy"]),
                },
            }]
        return []
    finally:
        store.close()


def regime_drift_findings(strategy: str) -> list[dict]:
    """Flags live trades occurring in a trend/volatility/session
    combination this strategy's historical (backtest/walk-forward) trades
    never covered -- a market condition the backtest never actually
    validated, not just a performance dip."""
    store = TradeStore(default_db_path())
    try:
        live_run_ids = {r["id"] for r in store.fetch_runs(strategy=strategy, kind="live", limit=200)}
        if not live_run_ids:
            return []

        all_trades = store.fetch_trades(strategy=strategy)
        live_trades = [t for t in all_trades if t["run_id"] in live_run_ids]
        historical_trades = [t for t in all_trades if t["run_id"] not in live_run_ids]
        if len(live_trades) < _MIN_LIVE_TRADES_FOR_REGIME_CHECK:
            return []

        historical_regimes = {
            (t["regime_trend"], t["regime_volatility"], t["regime_session"])
            for t in historical_trades if t["regime_trend"] is not None
        }
        if not historical_regimes:
            return []

        live_regimes = [
            (t["regime_trend"], t["regime_volatility"], t["regime_session"])
            for t in live_trades if t["regime_trend"] is not None
        ]
        novel = [r for r in live_regimes if r not in historical_regimes]
        if not novel:
            return []

        trend, vol, session = novel[0]
        return [{
            "strategy": strategy, "category": "regime_drift", "severity": "warning",
            "message": (
                f"{strategy}: {len(novel)} of {len(live_regimes)} labeled live trade(s) occurred in a "
                f"trend={trend}/volatility={vol}/session={session} combination never seen in this strategy's "
                f"historical backtest trades. The backtest may not have validated this specific market regime."
            ),
            "details": {
                "trend": trend, "volatility": vol, "session": session,
                "novel_count": len(novel), "live_count": len(live_regimes),
            },
        }]
    finally:
        store.close()


def recommendation_findings(strategy: str, current_params: dict) -> list[dict]:
    """Compares the latest nightly optimizer run's best-found parameters
    against what's currently configured. Never writes to config.yaml or
    anywhere else -- this is read-only, surfaced as a suggestion."""
    store = TradeStore(default_db_path())
    try:
        opt_runs = [r for r in store.fetch_runs(strategy=strategy, kind="optimizer", limit=5) if r["status"] == "completed"]
        if not opt_runs:
            return []
        latest = opt_runs[0]
        trials = store.fetch_optimization_trials(latest["id"])
        if not trials:
            return []
        best = trials[0]  # fetch_optimization_trials orders by rank; rank 1 is best.

        best_params_normalized = {k: str(v) for k, v in best["params"].items()}
        current_normalized = {k: str(v) for k, v in current_params.items()}
        if best_params_normalized == current_normalized:
            return []

        return [{
            "strategy": strategy, "category": "recommendation", "severity": "info",
            "message": (
                f"{strategy}: the latest nightly optimizer run (batch {latest['id']}) found "
                f"{best['params']} trained better (net P&L ${best['train_net_pnl']}) than the currently "
                f"configured {current_params}. Not applied automatically -- review before changing config.yaml."
            ),
            "details": {
                "run_id": latest["id"], "current_params": current_params, "recommended_params": best["params"],
                "train_net_pnl": str(best["train_net_pnl"]),
            },
        }]
    finally:
        store.close()


def all_findings(strategy: str, current_params: Optional[dict] = None) -> list[dict]:
    """Convenience wrapper the dashboard calls once per configured
    strategy -- the three checks above, concatenated."""
    findings = degradation_findings(strategy) + regime_drift_findings(strategy)
    if current_params is not None:
        findings += recommendation_findings(strategy, current_params)
    return findings
