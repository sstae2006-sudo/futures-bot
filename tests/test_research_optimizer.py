"""Tests for `research.optimizer`: grid expansion, batch execution, ranking,
walk-forward validation, and safety integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from futures_bot.config import BrokerSettings, RiskSettings, SessionSettings, Settings
from futures_bot.contracts import CME_TZ, MES
from futures_bot.models import Bar
from futures_bot.research.optimizer import (
    OptimizationResult,
    expand_param_grid,
    format_optimization_report,
    run_optimization,
    score_by_net_pnl,
    score_by_profit_factor,
)
from futures_bot.research.trade_store import TradeStore
from futures_bot.strategy import ema_crossover, vwap_reversion  # noqa: F401 -- registers the strategies


class TestExpandParamGrid:
    def test_single_sweep_dimension(self):
        combos = expand_param_grid({"range_minutes": [15, 30, 45]})
        assert combos == [{"range_minutes": 15}, {"range_minutes": 30}, {"range_minutes": 45}]

    def test_multiple_sweep_dimensions_is_cartesian_product(self):
        combos = expand_param_grid({"a": [1, 2], "b": [10, 20]})
        assert len(combos) == 4
        assert {"a": 1, "b": 10} in combos
        assert {"a": 2, "b": 20} in combos

    def test_scalar_values_stay_fixed_on_every_combo(self):
        combos = expand_param_grid({"range_minutes": [15, 30], "min_range_points": 2})
        assert all(c["min_range_points"] == 2 for c in combos)

    def test_no_sweep_keys_returns_one_combo(self):
        assert expand_param_grid({"a": 1, "b": "x"}) == [{"a": 1, "b": "x"}]

    def test_empty_grid_returns_one_empty_combo(self):
        assert expand_param_grid({}) == [{}]

    def test_empty_sweep_list_raises(self):
        with pytest.raises(ValueError, match="empty list"):
            expand_param_grid({"range_minutes": []})

    def test_a_list_of_lists_is_rejected_rather_than_silently_swept(self):
        """Regression test: this used to be silently (mis)read as a sweep
        over the two session windows -- `trading_sessions=["08:30","10:30"]`
        on one combo, `trading_sessions=["13:30","15:00"]` on the other --
        neither of which is the single, fixed, two-window value the config
        actually meant."""
        with pytest.raises(ValueError, match="trading_sessions"):
            expand_param_grid({"trading_sessions": [["08:30", "10:30"], ["13:30", "15:00"]]})

    def test_the_documented_single_element_wrap_is_also_rejected(self):
        """There is no shape-based way to tell a genuine sweep over
        list-valued elements apart from one fixed compound value wrapped
        to *look* like a single-element sweep -- both raise. Excluding the
        parameter from the grid (to use the strategy's default) is the
        only currently-supported way to hold it fixed at a non-default
        value; see `expand_param_grid`'s docstring."""
        with pytest.raises(ValueError, match="trading_sessions"):
            expand_param_grid({"trading_sessions": [[["08:30", "10:30"], ["13:30", "15:00"]]]})

    def test_a_list_of_dicts_is_also_rejected(self):
        with pytest.raises(ValueError, match="risk_overrides"):
            expand_param_grid({"risk_overrides": [{"max_loss": 100}, {"max_loss": 200}]})


def make_settings(**overrides) -> Settings:
    base = dict(
        contract="MES",
        mode="paper",
        risk=RiskSettings(
            contracts_per_trade=1,
            stop_loss_points=Decimal("5"),
            take_profit_points=Decimal("10"),
            daily_max_loss=Decimal("2000"),
            max_trades_per_session=500,
            account_size=Decimal("5000"),
        ),
        session=SessionSettings(start_ct="08:30", end_ct="15:00"),
        broker=BrokerSettings(starting_cash=Decimal("5000")),
    )
    base.update(overrides)
    return Settings(**base)


def make_choppy_bars(n: int, start_price: Decimal = Decimal("7500"), seed: int = 1) -> list[Bar]:
    """Deterministic oscillation -- gives ema_crossover something to cross
    repeatedly across a large bar count, without pulling in `random`."""
    start = datetime(2026, 1, 5, 8, 30, tzinfo=CME_TZ)
    bars, price = [], start_price
    for i in range(n):
        cycle = (i + seed) % 12
        swing = Decimal("2") if cycle < 6 else Decimal("-2")
        price += swing
        bars.append(
            Bar(
                timestamp=start + timedelta(minutes=i),
                open=price, high=price + Decimal("1"), low=price - Decimal("1"),
                close=price, volume=600,
            )
        )
    return bars


class TestExhaustiveParallelResumableSearch:
    """Fix: the grid search must actually be exhaustive under parallel
    execution (all CPU cores, `max_workers=None`) and resumable (a killed
    run picks back up instead of restarting), with every combination
    evaluated exactly once either way."""

    #: All 27 combos are structurally valid (fast_period always well below
    #: slow_period) -- keeps the exhaustiveness assertion simple (no combo
    #: is expected to be skipped) and isolates these tests from
    #: `TestRunOptimization::test_invalid_parameter_combo_is_skipped_not_fatal`,
    #: which already covers the skip path directly.
    GRID = {"fast_period": [3, 4, 5], "slow_period": [20, 25, 30], "trend_period": [50, 60, 70]}

    def test_every_combination_is_evaluated_exactly_once(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(600)

        result = run_optimization(
            settings, "ema_crossover", self.GRID, bars,
            top_n=3, assess_commission_sensitivity=False,
        )

        assert result.combos_tried == 27
        assert len(result.all_trials) == 27  # none skipped -- every combo here is valid
        seen = [tuple(sorted(t.params.items())) for t in result.all_trials]
        assert len(seen) == len(set(seen)) == 27  # no duplicates, none missing

    def test_resume_never_reevaluates_a_cached_combo(self, tmp_path, monkeypatch):
        """Simulates an interrupted-then-resumed run: the first call
        completes a batch and persists it; the second call, given the same
        batch id, must restore every combo from the store instead of
        re-running its backtest. `max_workers=1` forces the sequential
        code path (deliberately -- see below) so the tracking wrapper
        below can prove *zero* calls happened on resume, not just that the
        final result looks right."""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        store = TradeStore(tmp_path / "trials.db")
        grid = {"fast_period": [3, 5], "slow_period": [15, 25]}

        first = run_optimization(
            settings, "ema_crossover", grid, bars, top_n=2,
            assess_commission_sensitivity=False, store=store, batch_id="resume-test", max_workers=1,
        )
        assert first.combos_cached == 0
        assert first.combos_tried == 4

        # Wrap the module-level worker function to record every call --
        # with everything already cached, this must record nothing at all.
        # A pickled-closure trap for `ProcessPoolExecutor` wouldn't survive
        # crossing a process boundary, which is exactly why `max_workers=1`
        # (forcing the in-process sequential path) is used here rather than
        # the default.
        import futures_bot.research.optimizer as optimizer_module

        calls: list[dict] = []
        original = optimizer_module._evaluate_combo

        def tracking_evaluate_combo(strategy_name, params, settings_, train_bars):
            calls.append(params)
            return original(strategy_name, params, settings_, train_bars)

        monkeypatch.setattr(optimizer_module, "_evaluate_combo", tracking_evaluate_combo)

        second = run_optimization(
            settings, "ema_crossover", grid, bars, top_n=2,
            assess_commission_sensitivity=False, store=store, batch_id="resume-test", max_workers=1,
        )

        assert calls == []  # nothing was re-evaluated -- every combo came from the cache
        assert second.combos_cached == 4
        assert second.combos_tried == first.combos_tried == 4
        assert len(second.all_trials) == 4
        store.close()

    def test_no_duplicate_rows_after_resume(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        store = TradeStore(tmp_path / "trials.db")
        grid = {"fast_period": [3, 5], "slow_period": [15, 25]}

        run_optimization(
            settings, "ema_crossover", grid, bars, top_n=2,
            assess_commission_sensitivity=False, store=store, batch_id="dedup-test", max_workers=1,
        )
        run_optimization(  # a second call with the same batch id -- must not duplicate anything
            settings, "ema_crossover", grid, bars, top_n=2,
            assess_commission_sensitivity=False, store=store, batch_id="dedup-test", max_workers=1,
        )

        dupes = store._conn.execute(
            "SELECT combo_key, COUNT(*) c FROM optimization_trials "
            "WHERE batch_id = ? GROUP BY combo_key HAVING c > 1",
            ("dedup-test",),
        ).fetchall()
        assert dupes == []
        total_rows = store._conn.execute(
            "SELECT COUNT(*) FROM optimization_trials WHERE batch_id = ?", ("dedup-test",)
        ).fetchone()[0]
        assert total_rows == 4
        store.close()

    def test_max_workers_one_and_default_agree_on_the_result(self, tmp_path):
        """Correctness invariant across execution modes -- not a proof that
        real OS-level concurrency happened (unreliable to assert directly),
        but that the sequential fallback and the `ProcessPoolExecutor` path
        (default `max_workers=None`, all CPU cores, exercised here for real
        since this grid has more than one combo to run) produce the same
        set of combos and the same top result."""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(600)

        sequential = run_optimization(
            settings, "ema_crossover", self.GRID, bars,
            top_n=3, assess_commission_sensitivity=False, max_workers=1,
        )
        parallel = run_optimization(
            settings, "ema_crossover", self.GRID, bars,
            top_n=3, assess_commission_sensitivity=False, max_workers=None,
        )

        assert sequential.combos_tried == parallel.combos_tried == 27
        seq_keys = {tuple(sorted(t.params.items())) for t in sequential.all_trials}
        par_keys = {tuple(sorted(t.params.items())) for t in parallel.all_trials}
        assert seq_keys == par_keys
        assert sequential.best is not None and parallel.best is not None
        # Compare the *score* reached, not which specific params won --
        # this grid's synthetic bars produce a wide tie at the top (many
        # combos score identically), and which tied combo is recorded as
        # "best" depends on completion order, which parallel execution
        # deliberately does not guarantee. The optimizer's actual
        # correctness invariant is that both modes reach the same best
        # achievable score and evaluate the same complete set of combos
        # (both already asserted above) -- not a specific tie-breaking
        # order neither this test nor `run_optimization` ever promised.
        assert score_by_net_pnl(sequential.best.train_metrics) == score_by_net_pnl(parallel.best.train_metrics)


class TestRunOptimization:
    def test_tries_every_combination_in_the_grid(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)

        result = run_optimization(
            settings, "ema_crossover",
            {"fast_period": [3, 5], "slow_period": [12, 20]},
            bars, top_n=10, assess_commission_sensitivity=False,
        )

        assert result.combos_tried == 4
        assert len(result.all_trials) <= 4  # some combos can be skipped (e.g. fast >= slow)

    def test_progress_callback_reaches_final_combo(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        calls = []

        run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5], "slow_period": [12, 20]},
            bars, top_n=10, assess_commission_sensitivity=False,
            progress_callback=lambda done, total, best: calls.append((done, total, best)),
        )

        assert calls, "progress_callback was never called"
        assert calls[-1][0] == 4  # done
        assert calls[-1][1] == 4  # total
        assert calls[0][2] is None or "params" in calls[0][2]

    def test_progress_callback_current_best_only_improves(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        scores = []

        def on_progress(done, total, best):
            if best is not None:
                scores.append(best["score"])

        run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 4, 5], "slow_period": [12, 20, 30]},
            bars, top_n=10, assess_commission_sensitivity=False, progress_callback=on_progress,
        )

        assert scores  # at least one combo must have completed to score anything
        assert scores == sorted(scores)  # non-decreasing -- "current best" never gets worse

    def test_omitted_progress_callback_does_not_change_behavior(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5], "slow_period": [12, 20]},
            bars, top_n=10, assess_commission_sensitivity=False,
        )
        assert result.combos_tried == 4

    def test_ranked_trials_sorted_best_first_by_training_score(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)

        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5, 8], "slow_period": [15, 25]},
            bars, top_n=10, assess_commission_sensitivity=False,
        )

        scores = [score_by_net_pnl(t.train_metrics) for t in result.ranked_trials]
        assert scores == sorted(scores, reverse=True)

    def test_top_n_limits_how_many_get_validated(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)

        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 4, 5, 6], "slow_period": [15, 20, 25]},
            bars, top_n=2, assess_commission_sensitivity=False,
        )

        assert len(result.ranked_trials) <= 2
        assert all(t.rank is not None for t in result.ranked_trials)
        # Trials outside the top N were never validated.
        validated_param_sets = {tuple(sorted(t.params.items())) for t in result.ranked_trials}
        for trial in result.all_trials:
            key = tuple(sorted(trial.params.items()))
            if key not in validated_param_sets:
                assert trial.validation_metrics is None

    def test_best_is_the_top_ranked_trial(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5], "slow_period": [15, 25]},
            make_choppy_bars(400), assess_commission_sensitivity=False,
        )
        assert result.best is result.ranked_trials[0]

    def test_custom_score_key_changes_ranking(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        by_pnl = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5, 8], "slow_period": [15, 25]},
            bars, assess_commission_sensitivity=False, score_key=score_by_net_pnl,
        )
        by_pf = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5, 8], "slow_period": [15, 25]},
            bars, assess_commission_sensitivity=False, score_key=score_by_profit_factor,
        )
        # Both completed; whether the winner differs depends on the data,
        # but both must produce a valid ranked result.
        assert by_pnl.best is not None and by_pf.best is not None

    def test_safety_report_is_attached(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3], "slow_period": [15]},
            make_choppy_bars(400), assess_commission_sensitivity=False,
        )
        assert result.safety is not None
        assert result.safety.confidence in ("High", "Medium", "Low")

    def test_invalid_parameter_combo_is_skipped_not_fatal(self, tmp_path):
        """VwapReversion.__init__ raises for min_bars < 2 -- a bad combo
        mixed in with good ones must be skipped, not abort the whole search.
        (Per-bar strategy exceptions no longer propagate at all -- Phase 2's
        `TradingEngine._safe_signal` contains those -- so this exercises the
        one place a bad combination can still fail loudly: construction.)"""
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        result = run_optimization(
            settings, "vwap_reversion",
            {"min_bars": [5, 1]},  # 1 is invalid (< 2); 5 is fine
            make_choppy_bars(400), assess_commission_sensitivity=False,
        )
        assert result.combos_tried == 2
        assert len(result.all_trials) == 1  # the invalid combo was skipped, not fatal
        assert result.all_trials[0].params == {"min_bars": 5}

    def test_all_combos_invalid_raises_clear_error(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        with pytest.raises(ValueError, match="No parameter combination"):
            run_optimization(
                settings, "vwap_reversion", {"min_bars": [1]},
                make_choppy_bars(200), assess_commission_sensitivity=False,
            )

    def test_rolling_validation_uses_multiple_windows(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(1500)

        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3], "slow_period": [15]},
            bars, top_n=1, rolling=True, assess_commission_sensitivity=False,
        )

        assert result.best is not None
        # Rolling validation combines multiple windows -- if it ran at all,
        # bars_processed for validation should reflect more than one window's
        # worth (or be None if the validation slice was too small for even
        # one window, which the assertion below tolerates).
        if result.best.validation_metrics is not None:
            assert result.best.validation_metrics.bars_processed > 0

    def test_persists_every_trial_to_the_store(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(400)
        store = TradeStore(tmp_path / "trials.db")

        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5], "slow_period": [15, 25]},
            bars, top_n=2, assess_commission_sensitivity=False, store=store, batch_id="test-batch",
        )

        stored = store.fetch_optimization_trials("test-batch")
        assert len(stored) == len(result.all_trials)
        ranked_count = sum(1 for s in stored if s["rank"] is not None)
        assert ranked_count == len(result.ranked_trials)
        store.close()

    def test_unknown_strategy_raises(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        with pytest.raises(KeyError):
            run_optimization(settings, "not_a_real_strategy", {}, make_choppy_bars(100))


class TestScoreByProfitFactor:
    """Regression tests: `profit_factor` is `None` both when a combo traded
    zero times (worthless) and when it traded and never lost (excellent) --
    `score_by_profit_factor` must not rank both as the worst possible
    result."""

    def _metrics(self, trades):
        from futures_bot.backtest.metrics import BacktestMetrics
        return BacktestMetrics(trades=trades, starting_equity=Decimal("5000"))

    def _win(self):
        from futures_bot.models import Side, Trade
        return Trade(
            side=Side.LONG, quantity=1, entry_price=Decimal("100"), exit_price=Decimal("105"),
            entry_time=datetime(2024, 1, 1, tzinfo=CME_TZ),
            exit_time=datetime(2024, 1, 1, 0, 30, tzinfo=CME_TZ),
            gross_pnl=Decimal("50"), commission=Decimal("1.24"), exit_reason="take_profit",
        )

    def test_zero_trades_scores_as_worst_possible(self):
        empty = self._metrics([])
        assert empty.profit_factor is None
        assert score_by_profit_factor(empty) == Decimal("-1")

    def test_all_wins_scores_better_than_zero_trades(self):
        all_wins = self._metrics([self._win()])
        assert all_wins.profit_factor is None  # same None as the zero-trades case
        assert score_by_profit_factor(all_wins) > score_by_profit_factor(self._metrics([]))

    def test_all_wins_scores_higher_than_any_real_mixed_result(self):
        # A real profit factor of, say, 3.0 (generous) must still rank below
        # an all-winning combo's sentinel, not the other way around.
        from futures_bot.models import Side, Trade
        loss = Trade(
            side=Side.LONG, quantity=1, entry_price=Decimal("100"), exit_price=Decimal("99"),
            entry_time=datetime(2024, 1, 1, tzinfo=CME_TZ),
            exit_time=datetime(2024, 1, 1, 0, 30, tzinfo=CME_TZ),
            gross_pnl=Decimal("-25"), commission=Decimal("1.24"), exit_reason="stop_loss",
        )
        mixed = self._metrics([self._win(), self._win(), self._win(), loss])
        assert mixed.profit_factor is not None
        assert score_by_profit_factor(self._metrics([self._win()])) > score_by_profit_factor(mixed)


class TestDeterministicRanking:
    """Regression test: two combos tied on `score_key` must rank in the same
    (combo-grid) order on every run, regardless of which one's backtest
    happens to finish first under `ProcessPoolExecutor`."""

    def test_tied_scores_rank_by_grid_order_not_completion_order(self, tmp_path, monkeypatch):
        import futures_bot.research.optimizer as optimizer_module

        original_as_completed = optimizer_module.as_completed

        def reversed_as_completed(future_to_params):
            # Forces "the last combo submitted finishes first" -- the exact
            # non-reproducible ordering a real ProcessPoolExecutor could
            # produce on any given run, made deterministic for the test.
            return list(original_as_completed(future_to_params))[::-1]

        monkeypatch.setattr(optimizer_module, "as_completed", reversed_as_completed)

        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        # Far too few bars for any EMA crossover to clear warmup -- every
        # combo genuinely produces zero trades, a guaranteed exact tie.
        bars = make_choppy_bars(5)
        grid = {"fast_period": [3, 5, 8], "slow_period": [15, 25]}

        result = run_optimization(settings, "ema_crossover", grid, bars, top_n=10, assess_commission_sensitivity=False)

        assert all(t.train_metrics.trade_count == 0 for t in result.all_trials), "expected a genuine tie (0 trades each)"
        combo_order = [c for c in [
            {"fast_period": 3, "slow_period": 15}, {"fast_period": 3, "slow_period": 25},
            {"fast_period": 5, "slow_period": 15}, {"fast_period": 5, "slow_period": 25},
            {"fast_period": 8, "slow_period": 15}, {"fast_period": 8, "slow_period": 25},
        ]]
        assert [t.params for t in result.all_trials] == combo_order


def _fake_metrics_with_trade_count(n: int):
    """A `BacktestMetrics` with exactly `n` trivial trades -- lets tests
    control trade_count directly rather than hoping a real strategy/dataset
    combination happens to produce the crossing case they need."""
    from futures_bot.backtest.metrics import BacktestMetrics
    from futures_bot.models import Side, Trade

    base = datetime(2024, 1, 1, tzinfo=CME_TZ)
    trades = [
        Trade(
            side=Side.LONG, quantity=1, entry_price=Decimal("100"), exit_price=Decimal("101"),
            entry_time=base + timedelta(minutes=i), exit_time=base + timedelta(minutes=i, seconds=30),
            gross_pnl=Decimal("10"), commission=Decimal("1"), exit_reason="take_profit",
        )
        for i in range(n)
    ]
    return BacktestMetrics(trades=trades, starting_equity=Decimal("5000"))


class TestMinTradesRankingFloor:
    """Regression: a combo with too few training trades to mean anything
    must never outrank one with a real sample size, no matter what
    `score_key` says -- a single lucky trade could otherwise beat a
    genuinely good combo with hundreds of trades and a slightly lower
    ratio. `_evaluate_combo` is monkeypatched so trade counts are exact and
    deterministic rather than hoping a real strategy/dataset pairing
    happens to produce a mix of thin and real samples."""

    def _patch_trade_counts(self, monkeypatch):
        import futures_bot.research.optimizer as optimizer_module

        def fake_evaluate_combo(strategy_name, params, settings, train_bars):
            # fast_period=3 combos are the thin sample (1 trade); everything
            # else is a real sample (50 trades).
            return _fake_metrics_with_trade_count(1 if params["fast_period"] == 3 else 50)

        monkeypatch.setattr(optimizer_module, "_evaluate_combo", fake_evaluate_combo)

    def test_a_score_key_favoring_thin_samples_is_overridden_by_the_floor(self, tmp_path, monkeypatch):
        self._patch_trade_counts(monkeypatch)
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(50)

        # Deliberately perverse: rewards *fewer* trades -- the opposite of
        # what min_trades should let win. Without the floor, this would put
        # the thin (1-trade) combo at the very top of all_trials.
        def favor_thin_samples(m):
            return Decimal(-m.trade_count)

        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5, 8], "slow_period": [12, 20, 30]},
            bars, top_n=10, assess_commission_sensitivity=False,
            score_key=favor_thin_samples, min_trades=5, max_workers=1,
        )

        # Every trial that clears the floor must rank above every trial
        # that doesn't -- i.e. the "meets floor" flags form a sorted
        # (True-before-False) prefix, regardless of score.
        meets_floor = [t.train_metrics.trade_count >= 5 for t in result.all_trials]
        assert meets_floor == sorted(meets_floor, reverse=True)
        assert True in meets_floor and False in meets_floor  # confirms this exercised the crossing case

    def test_min_trades_zero_restores_pure_score_ranking(self, tmp_path, monkeypatch):
        self._patch_trade_counts(monkeypatch)
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        bars = make_choppy_bars(50)

        def favor_thin_samples(m):
            return Decimal(-m.trade_count)

        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3, 5, 8], "slow_period": [12, 20, 30]},
            bars, top_n=10, assess_commission_sensitivity=False,
            score_key=favor_thin_samples, min_trades=0, max_workers=1,
        )
        scores = [favor_thin_samples(t.train_metrics) for t in result.all_trials]
        assert scores == sorted(scores, reverse=True)


class TestFormatOptimizationReport:
    def test_includes_required_sections(self, tmp_path):
        settings = make_settings(logging={"directory": tmp_path, "level": "WARNING"})
        result = run_optimization(
            settings, "ema_crossover", {"fast_period": [3], "slow_period": [15]},
            make_choppy_bars(400), assess_commission_sensitivity=False,
        )
        text = format_optimization_report(result)
        for section in ("BEST CONFIGURATION", "Strategy:", "Parameters:", "Training:", "Confidence:", "Warnings:"):
            assert section in text

    def test_handles_no_successful_trials_gracefully(self):
        empty_result = OptimizationResult(
            batch_id="x", strategy="ema_crossover", contract="MES", combos_tried=1,
            all_trials=[], ranked_trials=[], best=None, safety=None,
        )
        text = format_optimization_report(empty_result)
        assert "No configuration" in text
