"""TimescaleDB/Postgres-backed research-data store -- the `research.db`
counterpart to `market_data/pg_store.py::PgMarketDataStore`.

Same public method surface as `research/trade_store.py::TradeStore`
(selected instead of it by `api/store.py::get_store()` whenever
`FUTURES_BOT_DATABASE_URL` is set), built on the shared pooled
`db.engine.get_engine()`. See `db/research_schema.py`'s module docstring
for the type-translation rules (NUMERIC/TIMESTAMPTZ/JSONB/Boolean) and the
one disclosed divergence from `TradeStore`'s exact return shapes
(consistent `Decimal` for every money column, vs. SQLite's occasional
un-decoded raw string).

**Verified against a real server** (see PROJECT_STATE.md) via
`tests/test_pg_trade_store_live.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from .features import TradeRecord
from .trade_store import _decimal_or_none, _jsonable, _jsonable_deep
from ..db.engine import get_engine
from ..db.research_schema import (
    client_import_fills,
    client_open_lots,
    client_profiles,
    config_deployments,
    experiments,
    import_history,
    import_staging,
    jobs,
    metadata,
    ml_models,
    model_deployments,
    optimization_trials,
    reports,
    runs,
    trades,
)


def _stringify_datetimes(d: dict) -> dict:
    """`TradeStore` never parses its TEXT-stored timestamps back into
    `datetime` on read (every `fetch_*` method returns them as the raw
    ISO string SQLite handed back) -- since Postgres columns are native
    `TIMESTAMPTZ` and the driver returns real `datetime` objects, this
    restores the exact same "always a string" contract every existing
    caller (routes, `_decode_run`/etc., pandas-facing consumers) already
    depends on."""
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in d.items()}


def _row_dict(row) -> dict:
    return dict(row._mapping)


class PgTradeStore:
    """Pooled, Postgres-backed. Holds no connection of its own -- every
    method borrows one from the shared `Engine`'s pool for the duration of
    that call, same design as `PgMarketDataStore`."""

    def __init__(self, engine: Optional[Engine] = None) -> None:
        self._engine = engine or get_engine()

    def ensure_schema(self) -> None:
        with self._engine.begin() as conn:
            metadata.create_all(conn, checkfirst=True)

    def close(self) -> None:
        """No-op: see `PgMarketDataStore.close`'s identical docstring."""

    def __enter__(self) -> "PgTradeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def location(self) -> str:
        return self._engine.url.render_as_string(hide_password=True)

    @property
    def size_bytes(self) -> int:
        table_names = [t.name for t in metadata.tables.values()]
        with self._engine.connect() as conn:
            total = conn.execute(
                text(
                    "SELECT COALESCE(SUM(pg_total_relation_size(quote_ident(tablename))), 0) "
                    "FROM pg_tables WHERE tablename = ANY(:names)"
                ),
                {"names": table_names},
            ).scalar_one()
            return int(total)

    # --- Trades ---

    def insert_trades(self, records: Sequence[TradeRecord]) -> None:
        with self._engine.begin() as conn:
            self._insert_trades_no_commit(records, conn)

    def _insert_trades_no_commit(self, records: Sequence[TradeRecord], conn) -> None:
        """Same insert as `insert_trades`, minus its own transaction --
        the piece `commit_client_import` needs so an import's writes share
        one transaction (mirrors `TradeStore._insert_trades_no_commit`,
        adapted for Core's explicit-connection style instead of a shared
        `self._conn`)."""
        if not records:
            return
        rows = [
            {
                "run_id": r.run_id, "contract": r.contract, "strategy": r.strategy,
                "strategy_params": _jsonable(r.strategy_params),
                "entry_time": r.entry_time, "exit_time": r.exit_time, "side": r.side,
                "entry_price": r.entry_price, "exit_price": r.exit_price,
                "gross_pnl": r.gross_pnl, "commission": r.commission, "net_pnl": r.net_pnl,
                "holding_minutes": r.holding_minutes, "exit_reason": r.exit_reason,
                "session_date": r.session_date, "day_of_week": r.day_of_week, "hour": r.hour,
                "entry_reason": r.entry_reason, "entry_metadata": _jsonable(r.entry_metadata), "outcome": r.outcome,
                "mfe_points": r.mfe_points, "mae_points": r.mae_points, "efficiency": r.efficiency,
                "regime_trend": r.regime_trend, "regime_volatility": r.regime_volatility, "regime_session": r.regime_session,
                "trade_id": r.trade_id, "stop_loss": r.stop_loss, "take_profit": r.take_profit,
                "entry_slippage": r.entry_slippage, "exit_slippage": r.exit_slippage,
            }
            for r in records
        ]
        conn.execute(trades.insert(), rows)

    def trade_count(self, run_id: Optional[str] = None) -> int:
        stmt = select(func.count()).select_from(trades)
        if run_id is not None:
            stmt = stmt.where(trades.c.run_id == run_id)
        with self._engine.connect() as conn:
            return conn.execute(stmt).scalar_one()

    def fetch_trades(
        self, run_id: Optional[str] = None, strategy: Optional[str] = None,
        side: Optional[str] = None, outcome: Optional[str] = None,
    ) -> list[dict]:
        stmt = select(trades)
        if run_id is not None:
            stmt = stmt.where(trades.c.run_id == run_id)
        if strategy is not None:
            stmt = stmt.where(trades.c.strategy == strategy)
        if side is not None:
            stmt = stmt.where(trades.c.side == side)
        if outcome is not None:
            stmt = stmt.where(trades.c.outcome == outcome)
        stmt = stmt.order_by(trades.c.entry_time)

        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()

        out = []
        for row in rows:
            d = _stringify_datetimes(_row_dict(row))
            for slippage_field in ("entry_slippage", "exit_slippage"):
                d[slippage_field] = d[slippage_field] if d.get(slippage_field) is not None else Decimal("0")
            out.append(d)
        return out

    # --- Optimization trials ---

    def insert_optimization_trial(
        self, *, batch_id: str, strategy: str, params: dict,
        train_trades: int, train_net_pnl: Decimal, train_profit_factor: Optional[Decimal],
        train_max_drawdown: Decimal, train_gross_losses: Optional[Decimal] = None,
        train_win_count: Optional[int] = None, train_largest_win: Optional[Decimal] = None,
        validation_trades: Optional[int] = None, validation_net_pnl: Optional[Decimal] = None,
        validation_profit_factor: Optional[Decimal] = None, validation_max_drawdown: Optional[Decimal] = None,
        rank: Optional[int] = None,
    ) -> None:
        combo_key = json.dumps(_jsonable(params), sort_keys=True)
        stmt = (
            pg_insert(optimization_trials)
            .values(
                batch_id=batch_id, strategy=strategy, params=_jsonable(params), combo_key=combo_key,
                train_trades=train_trades, train_net_pnl=train_net_pnl, train_profit_factor=train_profit_factor,
                train_max_drawdown=train_max_drawdown, train_gross_losses=train_gross_losses,
                train_win_count=train_win_count, train_largest_win=train_largest_win,
                validation_trades=validation_trades, validation_net_pnl=validation_net_pnl,
                validation_profit_factor=validation_profit_factor, validation_max_drawdown=validation_max_drawdown,
                rank=rank,
            )
            .on_conflict_do_nothing(index_elements=["batch_id", "combo_key"])
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def update_optimization_trial_validation(
        self, *, batch_id: str, combo_key: str, validation_trades: Optional[int],
        validation_net_pnl: Optional[Decimal], validation_profit_factor: Optional[Decimal],
        validation_max_drawdown: Optional[Decimal], rank: Optional[int],
    ) -> None:
        stmt = (
            update(optimization_trials)
            .where(optimization_trials.c.batch_id == batch_id, optimization_trials.c.combo_key == combo_key)
            .values(
                validation_trades=validation_trades, validation_net_pnl=validation_net_pnl,
                validation_profit_factor=validation_profit_factor, validation_max_drawdown=validation_max_drawdown,
                rank=rank,
            )
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fetch_completed_combos(self, batch_id: str) -> dict[str, dict]:
        stmt = select(optimization_trials).where(
            optimization_trials.c.batch_id == batch_id, optimization_trials.c.combo_key.is_not(None)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        out: dict[str, dict] = {}
        for row in rows:
            d = _stringify_datetimes(_row_dict(row))
            out[d["combo_key"]] = d
        return out

    def fetch_optimization_trials(self, batch_id: str) -> list[dict]:
        stmt = select(optimization_trials).where(optimization_trials.c.batch_id == batch_id).order_by(
            optimization_trials.c.rank
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    # --- Runs ---

    def insert_run(
        self, *, run_id: str, kind: str, status: str, strategy: str, contract: str,
        strategy_params: dict, csv_path: Optional[str] = None, walk_forward: bool = False,
    ) -> None:
        stmt = runs.insert().values(
            id=run_id, kind=kind, status=status, strategy=strategy, contract=contract,
            strategy_params=_jsonable(strategy_params), csv_path=csv_path, walk_forward=walk_forward,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def complete_run(
        self, run_id: str, *, starting_equity: Decimal, trade_count: int, net_pnl: Decimal,
        profit_factor: Optional[Decimal], win_rate: Optional[Decimal], expectancy: Optional[Decimal],
        sharpe_ratio: Optional[Decimal], sortino_ratio: Optional[Decimal], max_drawdown: Decimal,
        max_drawdown_pct: Optional[Decimal], caveats: list[str], first_bar=None, last_bar=None,
        validation_trade_count: Optional[int] = None, validation_net_pnl: Optional[Decimal] = None,
        validation_profit_factor: Optional[Decimal] = None,
    ) -> None:
        stmt = (
            update(runs)
            .where(runs.c.id == run_id)
            .values(
                status="completed", starting_equity=starting_equity, trade_count=trade_count, net_pnl=net_pnl,
                profit_factor=profit_factor, win_rate=win_rate, expectancy=expectancy, sharpe_ratio=sharpe_ratio,
                sortino_ratio=sortino_ratio, max_drawdown=max_drawdown, max_drawdown_pct=max_drawdown_pct,
                caveats=caveats, first_bar=first_bar, last_bar=last_bar,
                validation_trade_count=validation_trade_count, validation_net_pnl=validation_net_pnl,
                validation_profit_factor=validation_profit_factor, completed_at=func.now(),
            )
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fail_run(self, run_id: str, error_message: str) -> None:
        stmt = update(runs).where(runs.c.id == run_id).values(
            status="failed", error_message=error_message, completed_at=func.now()
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fetch_runs(self, *, strategy: Optional[str] = None, kind: Optional[str] = None, limit: int = 100) -> list[dict]:
        stmt = select(runs)
        if strategy is not None:
            stmt = stmt.where(runs.c.strategy == strategy)
        if kind is not None:
            stmt = stmt.where(runs.c.kind == kind)
        stmt = stmt.order_by(runs.c.created_at.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [self._decode_run(_row_dict(row)) for row in rows]

    def fetch_run(self, run_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.id == run_id)).first()
        return self._decode_run(_row_dict(row)) if row is not None else None

    @staticmethod
    def _decode_run(d: dict) -> dict:
        d["caveats"] = d.get("caveats") or []
        return _stringify_datetimes(d)

    # --- Reports ---

    def insert_report(self, *, report_id: str, run_id: str, format: str, path: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(reports.insert().values(id=report_id, run_id=run_id, format=format, path=path))

    def fetch_reports(self, run_id: Optional[str] = None) -> list[dict]:
        stmt = select(reports)
        if run_id is not None:
            stmt = stmt.where(reports.c.run_id == run_id)
        stmt = stmt.order_by(reports.c.created_at.desc())
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    # --- Jobs ---

    def insert_job(self, *, job_id: str, kind: str, request: dict) -> None:
        stmt = jobs.insert().values(
            id=job_id, kind=kind, status="queued", progress_total=0, request=_jsonable_deep(request)
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def start_job(self, job_id: str, *, progress_total: int = 0) -> None:
        stmt = update(jobs).where(jobs.c.id == job_id).values(
            status="running", started_at=func.now(), progress_total=progress_total
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def update_job_progress(
        self, job_id: str, *, current: int, total: Optional[int] = None, message: Optional[str] = None,
    ) -> None:
        values = {"progress_current": current, "progress_message": message}
        if total is not None:
            values["progress_total"] = total
        stmt = update(jobs).where(jobs.c.id == job_id).values(**values)
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def complete_job(self, job_id: str, *, result_id: Optional[str] = None, result_payload: Optional[dict] = None) -> None:
        stmt = update(jobs).where(jobs.c.id == job_id).values(
            status="completed", result_id=result_id,
            result_payload=_jsonable_deep(result_payload) if result_payload is not None else None,
            completed_at=func.now(),
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fail_job(self, job_id: str, error_message: str) -> None:
        stmt = update(jobs).where(jobs.c.id == job_id).values(
            status="failed", error_message=error_message, completed_at=func.now()
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fetch_job(self, job_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(jobs).where(jobs.c.id == job_id)).first()
        return _stringify_datetimes(_row_dict(row)) if row is not None else None

    def fetch_jobs(self, *, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        stmt = select(jobs)
        if status is not None:
            stmt = stmt.where(jobs.c.status == status)
        stmt = stmt.order_by(jobs.c.created_at.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    # --- Experiments ---

    def insert_experiment(
        self, *, experiment_id: str, name: str, hypothesis: str, strategy: str, parameters: dict,
        dataset: Optional[str] = None, run_id: Optional[str] = None, notes: Optional[str] = None,
        model_id: Optional[str] = None, dataset_version: Optional[str] = None, metrics: Optional[dict] = None,
    ) -> None:
        stmt = experiments.insert().values(
            id=experiment_id, name=name, hypothesis=hypothesis, strategy=strategy, dataset=dataset,
            parameters=_jsonable(parameters), run_id=run_id, notes=notes, model_id=model_id,
            dataset_version=dataset_version, metrics=_jsonable_deep(metrics) if metrics is not None else None,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def update_experiment_notes(self, experiment_id: str, notes: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(experiments).where(experiments.c.id == experiment_id).values(notes=notes))

    def fetch_experiment(self, experiment_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(experiments).where(experiments.c.id == experiment_id)).first()
        return _stringify_datetimes(_row_dict(row)) if row is not None else None

    def fetch_experiments(self, *, strategy: Optional[str] = None, limit: int = 100) -> list[dict]:
        stmt = select(experiments)
        if strategy is not None:
            stmt = stmt.where(experiments.c.strategy == strategy)
        stmt = stmt.order_by(experiments.c.created_at.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    # --- ML models ---

    def insert_model(
        self, *, model_id: str, strategy: str, model_type: str, feature_columns: list[str],
        hyperparameters: dict, evaluation_mode: str, dataset_size: int, dataset_version: str,
        job_id: Optional[str] = None,
    ) -> int:
        model_family = f"{strategy}:{model_type}"
        with self._engine.begin() as conn:
            current_max = conn.execute(
                select(func.coalesce(func.max(ml_models.c.version), 0)).where(ml_models.c.model_family == model_family)
            ).scalar_one()
            version = current_max + 1
            conn.execute(
                ml_models.insert().values(
                    id=model_id, model_family=model_family, version=version, strategy=strategy,
                    model_type=model_type, status="queued", job_id=job_id,
                    feature_columns=sorted(feature_columns), hyperparameters=_jsonable(hyperparameters),
                    evaluation_mode=evaluation_mode, dataset_size=dataset_size, dataset_version=dataset_version,
                )
            )
        return version

    def update_model_status(self, model_id: str, status: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(ml_models).where(ml_models.c.id == model_id).values(status=status))

    def update_model_job_id(self, model_id: str, job_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(ml_models).where(ml_models.c.id == model_id).values(job_id=job_id))

    def complete_model(
        self, model_id: str, *, metrics: dict, feature_importance: list[dict], artifact_path: str,
        app_version: str, git_commit: Optional[str], overfit_warning: bool = False, overfit_note: Optional[str] = None,
    ) -> None:
        stmt = update(ml_models).where(ml_models.c.id == model_id).values(
            status="finished", metrics=_jsonable_deep(metrics), feature_importance=_jsonable_deep(feature_importance),
            artifact_path=artifact_path, app_version=app_version, git_commit=git_commit,
            overfit_warning=overfit_warning, overfit_note=overfit_note, completed_at=func.now(),
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fail_model(self, model_id: str, error_message: str) -> None:
        stmt = update(ml_models).where(ml_models.c.id == model_id).values(
            status="failed", error_message=error_message, completed_at=func.now()
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def stop_model(self, model_id: str) -> None:
        stmt = update(ml_models).where(ml_models.c.id == model_id).values(status="stopped", completed_at=func.now())
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def update_model_backtest_metrics(self, model_id: str, derived_backtest_metrics: dict) -> None:
        stmt = update(ml_models).where(ml_models.c.id == model_id).values(
            derived_backtest_metrics=_jsonable_deep(derived_backtest_metrics)
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def update_model_notes(self, model_id: str, notes: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(ml_models).where(ml_models.c.id == model_id).values(notes=notes))

    def archive_model(self, model_id: str, archived: bool = True) -> None:
        with self._engine.begin() as conn:
            conn.execute(update(ml_models).where(ml_models.c.id == model_id).values(archived=archived))

    def delete_model(self, model_id: str) -> Optional[str]:
        row = self.fetch_model(model_id)
        with self._engine.begin() as conn:
            conn.execute(delete(ml_models).where(ml_models.c.id == model_id))
        return row["artifact_path"] if row else None

    def fetch_model(self, model_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(ml_models).where(ml_models.c.id == model_id)).first()
        return self._decode_model(_row_dict(row)) if row is not None else None

    def fetch_models(
        self, *, strategy: Optional[str] = None, model_family: Optional[str] = None, include_archived: bool = False,
    ) -> list[dict]:
        stmt = select(ml_models)
        if strategy is not None:
            stmt = stmt.where(ml_models.c.strategy == strategy)
        if model_family is not None:
            stmt = stmt.where(ml_models.c.model_family == model_family)
        if not include_archived:
            stmt = stmt.where(ml_models.c.archived.is_(False))
        stmt = stmt.order_by(ml_models.c.created_at.desc())
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [self._decode_model(_row_dict(row)) for row in rows]

    def fetch_model_versions(self, model_family: str) -> list[dict]:
        stmt = select(ml_models).where(ml_models.c.model_family == model_family).order_by(ml_models.c.version)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [self._decode_model(_row_dict(row)) for row in rows]

    @staticmethod
    def _decode_model(d: dict) -> dict:
        d["overfit_warning"] = bool(d["overfit_warning"])
        d["archived"] = bool(d["archived"])
        return _stringify_datetimes(d)

    # --- Model deployments ---

    def insert_deployment(self, *, deployment_id: str, strategy: str, model_id: Optional[str], action: str) -> None:
        stmt = model_deployments.insert().values(id=deployment_id, strategy=strategy, model_id=model_id, action=action)
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fetch_current_deployment(self, strategy: str) -> Optional[dict]:
        stmt = (
            select(model_deployments)
            .where(model_deployments.c.strategy == strategy)
            .order_by(model_deployments.c.created_at.desc())
            .limit(1)
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        if row is None:
            return None
        d = _stringify_datetimes(_row_dict(row))
        return d if d["action"] != "undeploy" else None

    def fetch_deployment_history(self, strategy: str) -> list[dict]:
        stmt = (
            select(model_deployments)
            .where(model_deployments.c.strategy == strategy)
            .order_by(model_deployments.c.created_at.desc())
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    # --- Client trade importer ---

    def insert_client_profile(self, *, profile_id: str, name: str, notes: Optional[str] = None) -> None:
        with self._engine.begin() as conn:
            conn.execute(client_profiles.insert().values(id=profile_id, name=name, notes=notes))

    def fetch_client_profile(self, profile_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(client_profiles).where(client_profiles.c.id == profile_id)).first()
        return _stringify_datetimes(_row_dict(row)) if row is not None else None

    def fetch_client_profiles(self) -> list[dict]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(client_profiles).order_by(client_profiles.c.name)).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    def insert_import_staging(
        self, *, staging_id: str, profile_id: str, filename: str, stored_path: str,
        detected_format: str, suggested_mapping: dict,
    ) -> None:
        stmt = import_staging.insert().values(
            id=staging_id, profile_id=profile_id, filename=filename, stored_path=stored_path,
            detected_format=detected_format, suggested_mapping=suggested_mapping,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fetch_import_staging(self, staging_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(import_staging).where(import_staging.c.id == staging_id)).first()
        return _stringify_datetimes(_row_dict(row)) if row is not None else None

    def delete_import_staging(self, staging_id: str) -> Optional[str]:
        row = self.fetch_import_staging(staging_id)
        with self._engine.begin() as conn:
            conn.execute(delete(import_staging).where(import_staging.c.id == staging_id))
        return row["stored_path"] if row else None

    def fetch_existing_fingerprints(self, profile_id: str, fingerprints: Sequence[str]) -> set[str]:
        if not fingerprints:
            return set()
        stmt = select(client_import_fills.c.fingerprint).where(
            client_import_fills.c.profile_id == profile_id,
            client_import_fills.c.fingerprint.in_(list(fingerprints)),
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return {r[0] for r in rows}

    def fetch_open_lots(self, profile_id: str, symbol: str) -> list[dict]:
        stmt = (
            select(client_open_lots)
            .where(client_open_lots.c.profile_id == profile_id, client_open_lots.c.symbol == symbol)
            .order_by(client_open_lots.c.entry_time, client_open_lots.c.id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    def commit_client_import(
        self, *, import_id: str, profile_id: str, filename: str, detected_format: str,
        new_fill_rows: Sequence[dict], consumed_lot_ids: Sequence[str], updated_lots: Sequence[tuple],
        new_open_lots: Sequence[dict], trade_records: Sequence[TradeRecord],
        total_fill_rows: int, imported_fill_count: int, duplicate_fill_count: int,
        error_count: int, errors: list, warnings: list, job_id: Optional[str] = None,
    ) -> None:
        """Same single-transaction contract as `TradeStore.commit_client_import`
        -- everything in this batch commits together or rolls back
        together, so `client_open_lots`'s FIFO ordering can never observe a
        partially-applied batch."""
        with self._engine.begin() as conn:
            for f in new_fill_rows:
                conn.execute(
                    client_import_fills.insert().values(
                        id=uuid.uuid4().hex[:12], profile_id=profile_id, fingerprint=f["fingerprint"],
                        symbol=f["symbol"], fill_time=f["fill_time"], side=f["side"], quantity=f["quantity"],
                        price=f["price"], raw_row=_jsonable(f["raw_row"]), import_id=import_id,
                    )
                )

            for lot_id in consumed_lot_ids:
                conn.execute(delete(client_open_lots).where(client_open_lots.c.id == lot_id))

            for lot_id, new_quantity in updated_lots:
                conn.execute(
                    update(client_open_lots).where(client_open_lots.c.id == lot_id).values(quantity_remaining=new_quantity)
                )

            for lot in new_open_lots:
                conn.execute(
                    client_open_lots.insert().values(
                        id=uuid.uuid4().hex[:12], profile_id=profile_id, symbol=lot["symbol"], side=lot["side"],
                        quantity_remaining=lot["quantity_remaining"], entry_price=lot["entry_price"],
                        entry_time=lot["entry_time"], entry_fingerprint=lot["entry_fingerprint"],
                        raw_entry_row=_jsonable(lot["raw_entry_row"]),
                    )
                )

            self._insert_trades_no_commit(trade_records, conn)

            conn.execute(
                import_history.insert().values(
                    id=import_id, profile_id=profile_id, filename=filename, detected_format=detected_format,
                    status="completed", total_fill_rows=total_fill_rows, imported_fill_count=imported_fill_count,
                    duplicate_fill_count=duplicate_fill_count, error_count=error_count, trades_created=len(trade_records),
                    errors=_jsonable_deep(errors), warnings=warnings, job_id=job_id,
                )
            )

    def fail_client_import(
        self, *, import_id: str, profile_id: str, filename: str, detected_format: str,
        error_message: str, job_id: Optional[str] = None,
    ) -> None:
        stmt = import_history.insert().values(
            id=import_id, profile_id=profile_id, filename=filename, detected_format=detected_format,
            status="failed", total_fill_rows=0, imported_fill_count=0, duplicate_fill_count=0,
            error_count=0, trades_created=0, errors=[], warnings=None, job_id=job_id, error_message=error_message,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    # --- Config parameter deploy/rollback log ---

    def insert_config_deployment(
        self, *, deployment_id: str, strategy: str, action: str, params: dict, backup_path: Optional[str] = None,
    ) -> None:
        stmt = config_deployments.insert().values(
            id=deployment_id, strategy=strategy, action=action, params=_jsonable_deep(params), backup_path=backup_path,
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def fetch_config_deployment(self, deployment_id: str) -> Optional[dict]:
        with self._engine.connect() as conn:
            row = conn.execute(select(config_deployments).where(config_deployments.c.id == deployment_id)).first()
        return _stringify_datetimes(_row_dict(row)) if row is not None else None

    def fetch_config_deployments(self, strategy: Optional[str] = None, limit: int = 50) -> list[dict]:
        stmt = select(config_deployments)
        if strategy is not None:
            stmt = stmt.where(config_deployments.c.strategy == strategy)
        stmt = stmt.order_by(config_deployments.c.created_at.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        return [_stringify_datetimes(_row_dict(row)) for row in rows]

    def fetch_import_history(self, profile_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        stmt = select(import_history)
        if profile_id is not None:
            stmt = stmt.where(import_history.c.profile_id == profile_id)
        stmt = stmt.order_by(import_history.c.created_at.desc()).limit(limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()
        out = []
        for row in rows:
            d = _stringify_datetimes(_row_dict(row))
            d["warnings"] = d.get("warnings") or []
            out.append(d)
        return out
