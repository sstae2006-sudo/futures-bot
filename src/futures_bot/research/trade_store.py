"""Persistent trade and optimization-trial storage.

SQLite via the standard library `sqlite3` module -- no new dependency, which
matches this project's deliberately minimal `pyproject.toml` (pydantic and
pyyaml only). Plain SQL rather than an ORM, for the same reason: it's
transparent, and the schema is small enough that an ORM would add a
dependency and a learning curve without buying anything back.

Two tables, two different shapes on purpose:

* `trades` -- one denormalized row per closed trade (see
  `research.features.TradeRecord`). Denormalized rather than joined against a
  separate `runs` table because the target consumer is `pandas.read_sql` /
  ad hoc `SELECT *` for ML feature work, where a join the caller has to know
  to perform is friction, not structure, at this data volume (thousands of
  trades, not millions).
* `optimization_trials` -- one row per parameter combination a grid search
  tried (Phase 3 feature 1's "record every configuration tested"), a
  different unit of record than a trade and so a different table.

Decimal-valued money fields are stored as TEXT (exact string, e.g.
``"36.26"``), the same convention `journal.py` already uses for JSON --
SQLite has no native arbitrary-precision decimal type, and REAL (float)
would reintroduce exactly the precision risk `models.py`'s docstring says
Decimal exists to avoid.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from .features import TradeRecord

#: Overridable so tests (and anyone running more than one instance of this
#: project against the same checkout) don't collide on the default path.
#: Lives here, next to `TradeStore` itself, rather than only in
#: `api/store.py` -- `live_trade_journal.py` needs the same resolution
#: without depending on the `api` package (a `research_server` consumer of
#: it must not import from `api`, since `api` is meant to depend on
#: `research_server`, never the reverse). `api/store.py` keeps its own
#: `get_store()`/`_db_path()` as a thin, unchanged wrapper around this.
DEFAULT_DB_PATH = Path("research.db")


def default_db_path() -> Path:
    return Path(os.environ.get("FUTURES_BOT_RESEARCH_DB", str(DEFAULT_DB_PATH)))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    contract        TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    strategy_params TEXT NOT NULL,   -- JSON
    entry_time      TEXT NOT NULL,   -- ISO 8601, timezone-aware
    exit_time       TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     TEXT NOT NULL,   -- Decimal, exact
    exit_price      TEXT NOT NULL,
    gross_pnl       TEXT NOT NULL,
    commission      TEXT NOT NULL,
    net_pnl         TEXT NOT NULL,
    holding_minutes REAL NOT NULL,
    exit_reason     TEXT NOT NULL,
    session_date    TEXT NOT NULL,
    day_of_week     TEXT NOT NULL,
    hour            INTEGER NOT NULL,
    entry_reason    TEXT NOT NULL,
    entry_metadata  TEXT NOT NULL,   -- JSON
    outcome         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trades_run_id ON trades(run_id);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_session_date ON trades(session_date);

CREATE TABLE IF NOT EXISTS optimization_trials (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id            TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    params              TEXT NOT NULL,   -- JSON
    combo_key           TEXT,             -- canonical json.dumps(params, sort_keys=True) -- resume/dedup lookup key
    train_trades        INTEGER NOT NULL,
    train_net_pnl       TEXT NOT NULL,
    train_profit_factor TEXT,             -- nullable: undefined with zero losing trades
    train_max_drawdown  TEXT NOT NULL,
    train_gross_losses  TEXT,             -- nullable: only needed to reconstruct a cached trial for resume
    train_win_count     INTEGER,          -- nullable: same as above
    train_largest_win   TEXT,             -- nullable: same as above -- largest_win_share_pct needs this
    validation_trades        INTEGER,
    validation_net_pnl       TEXT,
    validation_profit_factor TEXT,
    validation_max_drawdown  TEXT,
    rank                INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_trials_batch_id ON optimization_trials(batch_id);
-- The unique (batch_id, combo_key) index is created in `ensure_schema()`,
-- not here -- on a database that predates `combo_key`, this executescript
-- runs before the `ALTER TABLE ADD COLUMN` fallback that adds it, so an
-- index referencing that column here would fail immediately on any
-- existing database (`CREATE TABLE IF NOT EXISTS` above is a no-op on one,
-- but a bare `CREATE INDEX` isn't guarded by the column actually existing).

-- Added for the Phase 6A research API: one row per backtest/walk-forward/
-- compare run, so a dashboard can list history and show aggregate figures
-- without recomputing them from `trades` on every request. Deliberately a
-- separate table from `trades` (rather than adding run-level columns to
-- every trade row) -- a run's own headline metrics (Sharpe, max drawdown,
-- caveats) aren't properties of any single trade, and a run with zero
-- trades (a strategy that never fired) still needs a row here.
CREATE TABLE IF NOT EXISTS runs (
    id                      TEXT PRIMARY KEY,     -- uuid hex, matches trades.run_id when kind='backtest'
    kind                    TEXT NOT NULL,         -- 'backtest' | 'walk_forward' | 'optimizer' | 'compare' | 'live'
    status                  TEXT NOT NULL,         -- 'running' | 'completed' | 'failed'
    strategy                TEXT NOT NULL,
    contract                TEXT NOT NULL,
    strategy_params         TEXT NOT NULL,         -- JSON
    csv_path                TEXT,
    walk_forward            INTEGER NOT NULL DEFAULT 0,
    starting_equity         TEXT,
    trade_count             INTEGER,
    net_pnl                 TEXT,
    profit_factor           TEXT,
    win_rate                TEXT,
    expectancy              TEXT,
    sharpe_ratio             TEXT,
    sortino_ratio            TEXT,
    max_drawdown             TEXT,
    max_drawdown_pct         TEXT,
    validation_trade_count   INTEGER,
    validation_net_pnl       TEXT,
    validation_profit_factor TEXT,
    caveats                  TEXT,                 -- JSON list[str]
    first_bar                TEXT,
    last_bar                 TEXT,
    error_message            TEXT,
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_strategy ON runs(strategy);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at);

-- Generated report artifacts (HTML today), tracked so the API can list
-- "reports generated" and serve one back without re-walking the filesystem.
CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    format      TEXT NOT NULL,   -- 'html'
    path        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_run_id ON reports(run_id);

-- Phase 6B: the background job manager's persistence (see api/jobs.py).
-- Separate from `runs` on purpose -- a job is "this process is doing
-- (or did) some work," a run is "this backtest/optimizer batch produced
-- these figures." A job's `result_id` points at the `runs.id` or
-- `reports.id` row its work produced, once it has one.
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,     -- 'backtest' | 'walk_forward' | 'optimizer' | 'compare' | 'report'
    status      TEXT NOT NULL,     -- 'queued' | 'running' | 'completed' | 'failed'
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total   INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT,
    request     TEXT NOT NULL,     -- JSON: the request body that started this job
    result_id   TEXT,              -- runs.id / reports.id, once available
    result_payload TEXT,           -- JSON: full result, for job kinds (e.g. 'compare') with no runs-table home
    error_message TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    started_at   TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

-- Phase 6B: research experiment tracking (section 6) -- a named hypothesis
-- plus the run it was tested with and free-text notes on what was learned.
CREATE TABLE IF NOT EXISTS experiments (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    hypothesis  TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    dataset     TEXT,
    parameters  TEXT NOT NULL,     -- JSON
    run_id      TEXT,              -- runs.id, if this experiment ran a backtest
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_experiments_strategy ON experiments(strategy);

-- Phase 9: trained ML models as first-class, versioned artifacts. Rows are
-- append-only -- retraining never UPDATEs a prior row, it INSERTs a new one
-- with the next `version` in its `model_family` (see `insert_model`) -- so a
-- model's history is always fully reconstructable and nothing is ever
-- silently overwritten. `job_id` links back to `jobs` for the training run's
-- SSE stream; `status` here is model-lifecycle status (queued/training/
-- finished/failed/stopped), distinct from `jobs.status`, since a model can be
-- cooperatively stopped mid-training without that being a job "failure".
CREATE TABLE IF NOT EXISTS ml_models (
    id                      TEXT PRIMARY KEY,
    model_family            TEXT NOT NULL,   -- f"{strategy}:{model_type}"
    version                 INTEGER NOT NULL,
    strategy                TEXT NOT NULL,
    model_type              TEXT NOT NULL,   -- 'logistic_regression' | 'random_forest' | 'xgboost' | 'neural_network'
    status                  TEXT NOT NULL,   -- 'queued' | 'training' | 'finished' | 'failed' | 'stopped'
    job_id                  TEXT,
    feature_columns         TEXT NOT NULL,   -- JSON list[str]
    hyperparameters         TEXT NOT NULL,   -- JSON
    evaluation_mode         TEXT NOT NULL,   -- 'chronological_split' | 'walk_forward'
    dataset_size            INTEGER NOT NULL,
    dataset_version         TEXT NOT NULL,
    metrics                 TEXT,            -- JSON: {"train": {...}, "validation": {...}, "test": {...}} or walk-forward shape
    feature_importance      TEXT,            -- JSON list[{feature, importance}]
    overfit_warning         INTEGER NOT NULL DEFAULT 0,
    overfit_note            TEXT,
    derived_backtest_metrics TEXT,           -- JSON, populated on demand
    artifact_path           TEXT,
    app_version              TEXT,
    git_commit                TEXT,
    notes                    TEXT,
    archived                 INTEGER NOT NULL DEFAULT 0,
    error_message            TEXT,
    created_at                TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_ml_models_family ON ml_models(model_family);
CREATE INDEX IF NOT EXISTS idx_ml_models_strategy ON ml_models(strategy);

-- Phase 9: append-only deploy/rollback log -- "currently deployed model for
-- strategy X" is defined as the latest row for that strategy, never a
-- mutated pointer, so the full deployment history is always reconstructable
-- and a rollback is just another row, not a destructive update.
CREATE TABLE IF NOT EXISTS model_deployments (
    id          TEXT PRIMARY KEY,
    strategy    TEXT NOT NULL,
    model_id    TEXT,            -- NULL for an explicit 'undeploy'
    action      TEXT NOT NULL,   -- 'deploy' | 'rollback' | 'undeploy'
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_model_deployments_strategy ON model_deployments(strategy, created_at);

-- Phase 10.1: universal client trade importer. A `client_profile` is one
-- external trader/account whose trade history gets imported; imported
-- trades land in the same `trades` table above (strategy = 'import:<name>'),
-- through the same `insert_trades` every backtest/live session already uses.
CREATE TABLE IF NOT EXISTS client_profiles (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The upload -> preview -> confirm handoff. The uploaded file itself lives
-- on disk at `stored_path` (see research/trade_import.py); this row is what
-- lets `POST /api/imports/{id}/confirm` find it again, with whatever column
-- mapping the wizard ended up with.
CREATE TABLE IF NOT EXISTS import_staging (
    id                  TEXT PRIMARY KEY,
    profile_id          TEXT NOT NULL,
    filename            TEXT NOT NULL,
    stored_path         TEXT NOT NULL,
    detected_format     TEXT NOT NULL,
    suggested_mapping   TEXT NOT NULL,   -- JSON
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The permanent fill ledger. UNIQUE(profile_id, fingerprint) is what makes
-- duplicate detection correct across *multiple* imports over time (a
-- re-uploaded, overlapping date range must not double-count fills, which
-- would otherwise corrupt the FIFO queue in `client_open_lots` below) --
-- not just within one file.
CREATE TABLE IF NOT EXISTS client_import_fills (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    fill_time   TEXT NOT NULL,
    side        TEXT NOT NULL,    -- 'buy' | 'sell'
    quantity    TEXT NOT NULL,    -- Decimal, exact
    price       TEXT NOT NULL,    -- Decimal, exact
    raw_row     TEXT NOT NULL,    -- JSON: the complete original row, every column
    import_id   TEXT NOT NULL,    -- import_history.id this fill arrived with
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(profile_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_client_import_fills_profile ON client_import_fills(profile_id);

-- The persisted FIFO queue per (profile_id, symbol). A position opened in
-- one import and closed in a later one must still match correctly, so open
-- lots have to survive between import batches, not just within one file's
-- processing -- this table is that surviving state.
CREATE TABLE IF NOT EXISTS client_open_lots (
    id                  TEXT PRIMARY KEY,
    profile_id          TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,   -- 'buy' | 'sell' -- the open position's own direction
    quantity_remaining  TEXT NOT NULL,   -- Decimal, exact
    entry_price         TEXT NOT NULL,
    entry_time          TEXT NOT NULL,
    entry_fingerprint   TEXT NOT NULL,   -- the opening fill's fingerprint, for traceability
    raw_entry_row       TEXT NOT NULL,   -- JSON: the opening fill's complete original row
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_client_open_lots_profile_symbol ON client_open_lots(profile_id, symbol, entry_time);

CREATE TABLE IF NOT EXISTS import_history (
    id                      TEXT PRIMARY KEY,
    profile_id              TEXT NOT NULL,
    filename                TEXT NOT NULL,
    detected_format         TEXT NOT NULL,
    status                  TEXT NOT NULL,   -- 'completed' | 'failed'
    total_fill_rows         INTEGER NOT NULL,
    imported_fill_count     INTEGER NOT NULL,
    duplicate_fill_count    INTEGER NOT NULL,
    error_count             INTEGER NOT NULL,
    trades_created          INTEGER NOT NULL,
    errors                  TEXT NOT NULL,   -- JSON list[{row, message}]
    warnings                TEXT,            -- JSON list[str], nullable
    job_id                  TEXT,
    error_message           TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_import_history_profile ON import_history(profile_id, created_at);

-- Phase 10.2: config.yaml strategy_params deploy/rollback log for
-- research-server "recommendation" findings -- append-only, same
-- "currently deployed = latest row" convention as `model_deployments`.
-- `backup_path` points at the full config.yaml snapshot taken immediately
-- before this row's change was written (see research_server_service.py's
-- `deploy_strategy_params`), so a rollback can always restore the exact
-- prior file, comments included.
CREATE TABLE IF NOT EXISTS config_deployments (
    id          TEXT PRIMARY KEY,
    strategy    TEXT NOT NULL,
    action      TEXT NOT NULL,   -- 'deploy' | 'rollback'
    params      TEXT NOT NULL,   -- JSON: the strategy_params this row set
    backup_path TEXT,            -- config.yaml snapshot taken before this row's change
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_config_deployments_strategy ON config_deployments(strategy, created_at);
"""


def _decimal_or_none(value: Optional[str]) -> Optional[Decimal]:
    return Decimal(value) if value is not None else None


class TradeStore:
    """Owns one SQLite connection. Not thread-safe -- open one per process/
    script, same as any other sqlite3.Connection usage."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets readers and a writer proceed concurrently instead of the
        # default rollback-journal mode's exclusive-lock-per-write; busy_timeout
        # makes a second writer that does collide (WAL still serializes writers
        # among themselves) block and retry instead of raising `database is
        # locked` immediately. Both matter now that `research_server` has
        # several background threads (data scheduler, N paper-trading
        # strategies, nightly jobs) writing to this file concurrently, not just
        # one HTTP request at a time -- without this, a lock collision here
        # silently drops a trade (`LiveTradeJournal.trade()`'s catch-all logs
        # and moves on, by design, since one bad write must not kill a live
        # session, but "must not kill it" shouldn't mean "must not persist it").
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self.ensure_schema()

    #: Phase 6B analytics columns added to `trades` after that table already
    #: shipped -- `CREATE TABLE IF NOT EXISTS` can't retrofit columns onto an
    #: existing table, so these are added via `ALTER TABLE` in `ensure_schema`
    #: if missing. Every one is nullable: old rows (or a run persisted before
    #: excursions/regimes were computed) simply read back as None, the same
    #: as if they'd never been requested.
    _TRADE_ANALYTICS_COLUMNS = (
        ("mfe_points", "TEXT"), ("mae_points", "TEXT"), ("efficiency", "TEXT"),
        ("regime_trend", "TEXT"), ("regime_volatility", "TEXT"), ("regime_session", "TEXT"),
    )

    #: Phase 10.3: the bracket levels and simulated execution slippage a
    #: trade actually carried (see `models.Trade`), plus a stable `trade_id`
    #: independent of this table's own autoincrement `id` -- same "ALTER
    #: TABLE if missing" migration as `_TRADE_ANALYTICS_COLUMNS`. All
    #: nullable/zero-defaulted: a row written before this existed just reads
    #: back as None/0, same as if it had never been requested. `trade_id`
    #: gets its own unique index below (not here -- a bare `ALTER TABLE ADD
    #: COLUMN` can't add one directly); a NULL `trade_id` on every
    #: pre-migration row coexists fine under that index (SQL treats each
    #: NULL as distinct from every other for uniqueness purposes).
    _TRADE_STORAGE_COLUMNS = (
        ("trade_id", "TEXT"), ("stop_loss", "TEXT"), ("take_profit", "TEXT"),
        ("entry_slippage", "TEXT"), ("exit_slippage", "TEXT"),
    )

    #: Resume/dedup support added to `optimization_trials` after that table
    #: already shipped -- same "ALTER TABLE if missing" reasoning as
    #: `_TRADE_ANALYTICS_COLUMNS` above. All nullable: rows written before
    #: this existed just read back as None, which `fetch_completed_combos`
    #: treats as "not resumable" (no `combo_key` to match on), not an error.
    _TRIAL_RESUME_COLUMNS = (
        ("combo_key", "TEXT"), ("train_gross_losses", "TEXT"), ("train_win_count", "INTEGER"),
        ("train_largest_win", "TEXT"),
    )

    #: Phase 9: links an experiment to the model it tested, the dataset
    #: version that model was trained against, and a snapshot of its
    #: headline metrics -- so two experiments can be compared side-by-side
    #: without a second lookup into `ml_models`. Same "ALTER TABLE if
    #: missing" migration as above; all nullable, so an experiment recorded
    #: before Phase 9 just reads back with these as None.
    _EXPERIMENT_ML_COLUMNS = (
        ("model_id", "TEXT"), ("dataset_version", "TEXT"), ("metrics", "TEXT"),
    )

    def ensure_schema(self) -> None:
        """Idempotent -- safe to call on every startup, matching
        `state.py`/`journal.py`'s pattern of not requiring a separate
        migration step for a schema this small."""
        self._conn.executescript(_SCHEMA)
        existing_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(trades)")}
        for name, sqltype in self._TRADE_ANALYTICS_COLUMNS:
            if name not in existing_columns:
                self._conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {sqltype}")
        for name, sqltype in self._TRADE_STORAGE_COLUMNS:
            if name not in existing_columns:
                self._conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {sqltype}")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id ON trades(trade_id)")
        existing_trial_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(optimization_trials)")}
        for name, sqltype in self._TRIAL_RESUME_COLUMNS:
            if name not in existing_trial_columns:
                self._conn.execute(f"ALTER TABLE optimization_trials ADD COLUMN {name} {sqltype}")
        existing_experiment_columns = {row[1] for row in self._conn.execute("PRAGMA table_info(experiments)")}
        for name, sqltype in self._EXPERIMENT_ML_COLUMNS:
            if name not in existing_experiment_columns:
                self._conn.execute(f"ALTER TABLE experiments ADD COLUMN {name} {sqltype}")
        # Re-run now that the columns above are guaranteed to exist -- the
        # unique index in _SCHEMA references combo_key, so on a database
        # that predates this migration, the first executescript() call
        # above silently skipped creating it (the column didn't exist yet).
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trials_batch_combo ON optimization_trials(batch_id, combo_key)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TradeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    @property
    def location(self) -> str:
        """Human-readable, safe-to-display "where is this data" string --
        the uniform property both this class and `PgTradeStore` expose
        (`.path` itself stays SQLite-only, and is *not* safe to call on
        the Postgres store, since it has no file path at all). Callers
        that want to display this to a user (e.g. `SystemOverview.database_path`)
        should use this, not `.path`, so the same code works against
        either backend -- same fix `market_data/store.py::MarketDataStore.location`
        already established for `market_data.db`."""
        return str(self.path)

    # --- Trades ---

    def insert_trades(self, records: Sequence[TradeRecord]) -> None:
        self._insert_trades_no_commit(records)
        self._conn.commit()

    def _insert_trades_no_commit(self, records: Sequence[TradeRecord]) -> None:
        """Same insert as `insert_trades`, minus the commit -- the one piece
        `commit_client_import` needs so an import's fill-ledger/open-lot/
        trade writes land in a single transaction (see that method)."""
        if not records:
            return
        rows = [
            (
                r.run_id, r.contract, r.strategy, json.dumps(_jsonable(r.strategy_params), sort_keys=True),
                r.entry_time.isoformat(), r.exit_time.isoformat(), r.side,
                str(r.entry_price), str(r.exit_price), str(r.gross_pnl), str(r.commission), str(r.net_pnl),
                r.holding_minutes, r.exit_reason,
                r.session_date, r.day_of_week, r.hour,
                r.entry_reason, json.dumps(_jsonable(r.entry_metadata), sort_keys=True), r.outcome,
                str(r.mfe_points) if r.mfe_points is not None else None,
                str(r.mae_points) if r.mae_points is not None else None,
                str(r.efficiency) if r.efficiency is not None else None,
                r.regime_trend, r.regime_volatility, r.regime_session,
                r.trade_id,
                str(r.stop_loss) if r.stop_loss is not None else None,
                str(r.take_profit) if r.take_profit is not None else None,
                str(r.entry_slippage), str(r.exit_slippage),
            )
            for r in records
        ]
        self._conn.executemany(
            """
            INSERT INTO trades (
                run_id, contract, strategy, strategy_params,
                entry_time, exit_time, side,
                entry_price, exit_price, gross_pnl, commission, net_pnl,
                holding_minutes, exit_reason,
                session_date, day_of_week, hour,
                entry_reason, entry_metadata, outcome,
                mfe_points, mae_points, efficiency,
                regime_trend, regime_volatility, regime_session,
                trade_id, stop_loss, take_profit, entry_slippage, exit_slippage
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )

    def trade_count(self, run_id: Optional[str] = None) -> int:
        if run_id is None:
            cur = self._conn.execute("SELECT COUNT(*) FROM trades")
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM trades WHERE run_id = ?", (run_id,))
        return cur.fetchone()[0]

    def fetch_trades(
        self, run_id: Optional[str] = None, strategy: Optional[str] = None,
        side: Optional[str] = None, outcome: Optional[str] = None,
    ) -> list[dict]:
        """Returns plain dicts (one per row) -- money fields as Decimal,
        JSON columns decoded -- ready to hand to `pandas.DataFrame(...)` or
        inspect directly without the caller needing to know the storage
        encoding.

        ``side``/``outcome`` are pushed into the SQL ``WHERE`` clause rather
        than left for the caller to filter in Python after fetching every
        row -- `api.services.list_trades` used to do exactly that, meaning
        a filtered Trade Explorer query still materialized and JSON-decoded
        every trade in the table (tens of thousands of rows) before
        discarding most of them."""
        query = "SELECT * FROM trades"
        clauses, params = [], []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if strategy is not None:
            clauses.append("strategy = ?")
            params.append(strategy)
        if side is not None:
            clauses.append("side = ?")
            params.append(side)
        if outcome is not None:
            clauses.append("outcome = ?")
            params.append(outcome)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY entry_time"

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None

        out = []
        for row in rows:
            d = dict(row)
            for money_field in ("entry_price", "exit_price", "gross_pnl", "commission", "net_pnl"):
                d[money_field] = Decimal(d[money_field])
            for excursion_field in ("mfe_points", "mae_points", "efficiency"):
                d[excursion_field] = _decimal_or_none(d.get(excursion_field))
            for bracket_field in ("stop_loss", "take_profit"):
                d[bracket_field] = _decimal_or_none(d.get(bracket_field))
            for slippage_field in ("entry_slippage", "exit_slippage"):
                d[slippage_field] = Decimal(d[slippage_field]) if d.get(slippage_field) is not None else Decimal("0")
            d["strategy_params"] = json.loads(d["strategy_params"])
            d["entry_metadata"] = json.loads(d["entry_metadata"])
            out.append(d)
        return out

    # --- Optimization trials ---

    def insert_optimization_trial(
        self,
        *,
        batch_id: str,
        strategy: str,
        params: dict,
        train_trades: int,
        train_net_pnl: Decimal,
        train_profit_factor: Optional[Decimal],
        train_max_drawdown: Decimal,
        train_gross_losses: Optional[Decimal] = None,
        train_win_count: Optional[int] = None,
        train_largest_win: Optional[Decimal] = None,
        validation_trades: Optional[int] = None,
        validation_net_pnl: Optional[Decimal] = None,
        validation_profit_factor: Optional[Decimal] = None,
        validation_max_drawdown: Optional[Decimal] = None,
        rank: Optional[int] = None,
    ) -> None:
        """``OR IGNORE``: a second insert for a ``(batch_id, combo_key)``
        pair that's already there is a silent no-op, not an error --
        defense in depth for a resumed run, which already avoids
        re-submitting a cached combo in the first place (see
        `optimizer.run_optimization`), but must not corrupt the batch if
        something ever does submit it twice."""
        combo_key = json.dumps(_jsonable(params), sort_keys=True)
        self._conn.execute(
            """
            INSERT OR IGNORE INTO optimization_trials (
                batch_id, strategy, params, combo_key,
                train_trades, train_net_pnl, train_profit_factor, train_max_drawdown,
                train_gross_losses, train_win_count, train_largest_win,
                validation_trades, validation_net_pnl, validation_profit_factor, validation_max_drawdown,
                rank
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                batch_id, strategy, combo_key, combo_key,
                train_trades, str(train_net_pnl),
                str(train_profit_factor) if train_profit_factor is not None else None,
                str(train_max_drawdown),
                str(train_gross_losses) if train_gross_losses is not None else None,
                train_win_count,
                str(train_largest_win) if train_largest_win is not None else None,
                validation_trades,
                str(validation_net_pnl) if validation_net_pnl is not None else None,
                str(validation_profit_factor) if validation_profit_factor is not None else None,
                str(validation_max_drawdown) if validation_max_drawdown is not None else None,
                rank,
            ),
        )
        self._conn.commit()

    def update_optimization_trial_validation(
        self,
        *,
        batch_id: str,
        combo_key: str,
        validation_trades: Optional[int],
        validation_net_pnl: Optional[Decimal],
        validation_profit_factor: Optional[Decimal],
        validation_max_drawdown: Optional[Decimal],
        rank: Optional[int],
    ) -> None:
        """The second (validation) pass targets the row the first
        (training) pass already wrote, instead of inserting a duplicate --
        this is what lets training results be saved continuously (one
        `insert_optimization_trial` per combo, immediately) while
        validation/ranking still only happens once, at the end, for the
        top N."""
        self._conn.execute(
            """
            UPDATE optimization_trials
            SET validation_trades = ?, validation_net_pnl = ?,
                validation_profit_factor = ?, validation_max_drawdown = ?, rank = ?
            WHERE batch_id = ? AND combo_key = ?
            """,
            (
                validation_trades,
                str(validation_net_pnl) if validation_net_pnl is not None else None,
                str(validation_profit_factor) if validation_profit_factor is not None else None,
                str(validation_max_drawdown) if validation_max_drawdown is not None else None,
                rank, batch_id, combo_key,
            ),
        )
        self._conn.commit()

    def fetch_completed_combos(self, batch_id: str) -> dict[str, dict]:
        """Every already-persisted trial for ``batch_id``, keyed by
        ``combo_key`` -- the resume/dedup lookup `optimizer.run_optimization`
        uses to skip re-running a combo a prior (possibly interrupted) call
        already completed. Rows with no `combo_key` (written before this
        column existed) can't be matched against and are excluded --
        nothing resumes into a pre-migration batch, which is fine since
        resume is a new capability, not a retrofit onto old data."""
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM optimization_trials WHERE batch_id = ? AND combo_key IS NOT NULL", (batch_id,)
        )
        rows = cur.fetchall()
        self._conn.row_factory = None

        out: dict[str, dict] = {}
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"])
            d["train_net_pnl"] = Decimal(d["train_net_pnl"])
            d["train_profit_factor"] = _decimal_or_none(d["train_profit_factor"])
            d["train_max_drawdown"] = Decimal(d["train_max_drawdown"])
            d["train_gross_losses"] = _decimal_or_none(d["train_gross_losses"])
            d["train_largest_win"] = _decimal_or_none(d["train_largest_win"])
            out[d["combo_key"]] = d
        return out

    def fetch_optimization_trials(self, batch_id: str) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM optimization_trials WHERE batch_id = ? ORDER BY rank", (batch_id,)
        )
        rows = cur.fetchall()
        self._conn.row_factory = None

        out = []
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"])
            d["train_net_pnl"] = Decimal(d["train_net_pnl"])
            d["train_profit_factor"] = _decimal_or_none(d["train_profit_factor"])
            d["train_max_drawdown"] = Decimal(d["train_max_drawdown"])
            d["validation_net_pnl"] = _decimal_or_none(d["validation_net_pnl"])
            d["validation_profit_factor"] = _decimal_or_none(d["validation_profit_factor"])
            d["validation_max_drawdown"] = _decimal_or_none(d["validation_max_drawdown"])
            out.append(d)
        return out

    # --- Runs (Phase 6A research API) ---

    def insert_run(
        self,
        *,
        run_id: str,
        kind: str,
        status: str,
        strategy: str,
        contract: str,
        strategy_params: dict,
        csv_path: Optional[str] = None,
        walk_forward: bool = False,
    ) -> None:
        """Creates the row for a run before it's known to have succeeded --
        callers should insert with ``status='running'`` immediately after
        starting work, then call `complete_run`/`fail_run`, so a crash
        mid-run still leaves a row an API client can see (rather than the
        run vanishing silently, which is what happens with an insert-at-the-
        end approach)."""
        self._conn.execute(
            """
            INSERT INTO runs (id, kind, status, strategy, contract, strategy_params, csv_path, walk_forward)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                run_id, kind, status, strategy, contract,
                json.dumps(_jsonable(strategy_params), sort_keys=True),
                csv_path, int(walk_forward),
            ),
        )
        self._conn.commit()

    def complete_run(
        self,
        run_id: str,
        *,
        starting_equity: Decimal,
        trade_count: int,
        net_pnl: Decimal,
        profit_factor: Optional[Decimal],
        win_rate: Optional[Decimal],
        expectancy: Optional[Decimal],
        sharpe_ratio: Optional[Decimal],
        sortino_ratio: Optional[Decimal],
        max_drawdown: Decimal,
        max_drawdown_pct: Optional[Decimal],
        caveats: list[str],
        first_bar=None,
        last_bar=None,
        validation_trade_count: Optional[int] = None,
        validation_net_pnl: Optional[Decimal] = None,
        validation_profit_factor: Optional[Decimal] = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE runs SET
                status = 'completed',
                starting_equity = ?, trade_count = ?, net_pnl = ?, profit_factor = ?,
                win_rate = ?, expectancy = ?, sharpe_ratio = ?, sortino_ratio = ?,
                max_drawdown = ?, max_drawdown_pct = ?, caveats = ?,
                first_bar = ?, last_bar = ?,
                validation_trade_count = ?, validation_net_pnl = ?, validation_profit_factor = ?,
                completed_at = datetime('now')
            WHERE id = ?
            """,
            (
                str(starting_equity), trade_count, str(net_pnl),
                str(profit_factor) if profit_factor is not None else None,
                str(win_rate) if win_rate is not None else None,
                str(expectancy) if expectancy is not None else None,
                str(sharpe_ratio) if sharpe_ratio is not None else None,
                str(sortino_ratio) if sortino_ratio is not None else None,
                str(max_drawdown),
                str(max_drawdown_pct) if max_drawdown_pct is not None else None,
                json.dumps(caveats),
                first_bar.isoformat() if first_bar else None,
                last_bar.isoformat() if last_bar else None,
                validation_trade_count,
                str(validation_net_pnl) if validation_net_pnl is not None else None,
                str(validation_profit_factor) if validation_profit_factor is not None else None,
                run_id,
            ),
        )
        self._conn.commit()

    def fail_run(self, run_id: str, error_message: str) -> None:
        self._conn.execute(
            "UPDATE runs SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?",
            (error_message, run_id),
        )
        self._conn.commit()

    def fetch_runs(
        self, *, strategy: Optional[str] = None, kind: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        query = "SELECT * FROM runs"
        clauses, params = [], []
        if strategy is not None:
            clauses.append("strategy = ?")
            params.append(strategy)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [_decode_run(dict(row)) for row in rows]

    def fetch_run(self, run_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        return _decode_run(dict(row)) if row is not None else None

    # --- Reports ---

    def insert_report(self, *, report_id: str, run_id: str, format: str, path: str) -> None:
        self._conn.execute(
            "INSERT INTO reports (id, run_id, format, path) VALUES (?,?,?,?)",
            (report_id, run_id, format, path),
        )
        self._conn.commit()

    def fetch_reports(self, run_id: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM reports"
        params: list = []
        if run_id is not None:
            query += " WHERE run_id = ?"
            params.append(run_id)
        query += " ORDER BY created_at DESC"

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    # --- Jobs (Phase 6B background job manager) ---

    def insert_job(self, *, job_id: str, kind: str, request: dict) -> None:
        self._conn.execute(
            "INSERT INTO jobs (id, kind, status, progress_total, request) VALUES (?,?,'queued',0,?)",
            (job_id, kind, json.dumps(_jsonable_deep(request), sort_keys=True)),
        )
        self._conn.commit()

    def start_job(self, job_id: str, *, progress_total: int = 0) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = 'running', started_at = datetime('now'), progress_total = ? WHERE id = ?",
            (progress_total, job_id),
        )
        self._conn.commit()

    def update_job_progress(self, job_id: str, *, current: int, total: Optional[int] = None, message: Optional[str] = None) -> None:
        if total is not None:
            self._conn.execute(
                "UPDATE jobs SET progress_current = ?, progress_total = ?, progress_message = ? WHERE id = ?",
                (current, total, message, job_id),
            )
        else:
            self._conn.execute(
                "UPDATE jobs SET progress_current = ?, progress_message = ? WHERE id = ?",
                (current, message, job_id),
            )
        self._conn.commit()

    def complete_job(
        self, job_id: str, *, result_id: Optional[str] = None, result_payload: Optional[dict] = None,
    ) -> None:
        self._conn.execute(
            """UPDATE jobs SET status = 'completed', result_id = ?, result_payload = ?,
               completed_at = datetime('now') WHERE id = ?""",
            (result_id, json.dumps(_jsonable_deep(result_payload)) if result_payload is not None else None, job_id),
        )
        self._conn.commit()

    def fail_job(self, job_id: str, error_message: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?",
            (error_message, job_id),
        )
        self._conn.commit()

    def fetch_job(self, job_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        return _decode_job(dict(row)) if row is not None else None

    def fetch_jobs(self, *, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM jobs"
        params: list = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [_decode_job(dict(row)) for row in rows]

    # --- Experiments (Phase 6B experiment tracking) ---

    def insert_experiment(
        self, *, experiment_id: str, name: str, hypothesis: str, strategy: str,
        parameters: dict, dataset: Optional[str] = None, run_id: Optional[str] = None,
        notes: Optional[str] = None, model_id: Optional[str] = None,
        dataset_version: Optional[str] = None, metrics: Optional[dict] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO experiments (
                id, name, hypothesis, strategy, dataset, parameters, run_id, notes,
                model_id, dataset_version, metrics
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                experiment_id, name, hypothesis, strategy, dataset,
                json.dumps(_jsonable(parameters), sort_keys=True), run_id, notes,
                model_id, dataset_version, json.dumps(_jsonable_deep(metrics)) if metrics is not None else None,
            ),
        )
        self._conn.commit()

    def update_experiment_notes(self, experiment_id: str, notes: str) -> None:
        self._conn.execute("UPDATE experiments SET notes = ? WHERE id = ?", (notes, experiment_id))
        self._conn.commit()

    def fetch_experiment(self, experiment_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM experiments WHERE id = ?", (experiment_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        return _decode_experiment(dict(row)) if row is not None else None

    def fetch_experiments(self, *, strategy: Optional[str] = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM experiments"
        params: list = []
        if strategy is not None:
            query += " WHERE strategy = ?"
            params.append(strategy)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [_decode_experiment(dict(row)) for row in rows]

    # --- ML models (Phase 9) ---

    def insert_model(
        self, *, model_id: str, strategy: str, model_type: str,
        feature_columns: list[str], hyperparameters: dict, evaluation_mode: str,
        dataset_size: int, dataset_version: str, job_id: Optional[str] = None,
    ) -> int:
        """Always inserts a new row -- retraining is never an UPDATE. Returns
        the assigned `version`, computed as one more than the highest
        existing version in this `model_family` (0 if none exist yet), so
        versions are dense, ordered, and never reused even after a delete."""
        model_family = f"{strategy}:{model_type}"
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM ml_models WHERE model_family = ?", (model_family,)
        ).fetchone()
        version = row[0] + 1
        self._conn.execute(
            """
            INSERT INTO ml_models (
                id, model_family, version, strategy, model_type, status, job_id,
                feature_columns, hyperparameters, evaluation_mode,
                dataset_size, dataset_version
            ) VALUES (?,?,?,?,?,'queued',?,?,?,?,?,?)
            """,
            (
                model_id, model_family, version, strategy, model_type, job_id,
                json.dumps(sorted(feature_columns)), json.dumps(_jsonable(hyperparameters), sort_keys=True),
                evaluation_mode, dataset_size, dataset_version,
            ),
        )
        self._conn.commit()
        return version

    def update_model_status(self, model_id: str, status: str) -> None:
        self._conn.execute("UPDATE ml_models SET status = ? WHERE id = ?", (status, model_id))
        self._conn.commit()

    def update_model_job_id(self, model_id: str, job_id: str) -> None:
        self._conn.execute("UPDATE ml_models SET job_id = ? WHERE id = ?", (job_id, model_id))
        self._conn.commit()

    def complete_model(
        self, model_id: str, *, metrics: dict, feature_importance: list[dict],
        artifact_path: str, app_version: str, git_commit: Optional[str],
        overfit_warning: bool = False, overfit_note: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """
            UPDATE ml_models SET
                status = 'finished', metrics = ?, feature_importance = ?,
                artifact_path = ?, app_version = ?, git_commit = ?,
                overfit_warning = ?, overfit_note = ?, completed_at = datetime('now')
            WHERE id = ?
            """,
            (
                json.dumps(_jsonable_deep(metrics)), json.dumps(_jsonable_deep(feature_importance)),
                artifact_path, app_version, git_commit,
                int(overfit_warning), overfit_note, model_id,
            ),
        )
        self._conn.commit()

    def fail_model(self, model_id: str, error_message: str) -> None:
        self._conn.execute(
            "UPDATE ml_models SET status = 'failed', error_message = ?, completed_at = datetime('now') WHERE id = ?",
            (error_message, model_id),
        )
        self._conn.commit()

    def stop_model(self, model_id: str) -> None:
        self._conn.execute(
            "UPDATE ml_models SET status = 'stopped', completed_at = datetime('now') WHERE id = ?",
            (model_id,),
        )
        self._conn.commit()

    def update_model_backtest_metrics(self, model_id: str, derived_backtest_metrics: dict) -> None:
        self._conn.execute(
            "UPDATE ml_models SET derived_backtest_metrics = ? WHERE id = ?",
            (json.dumps(_jsonable_deep(derived_backtest_metrics)), model_id),
        )
        self._conn.commit()

    def update_model_notes(self, model_id: str, notes: str) -> None:
        self._conn.execute("UPDATE ml_models SET notes = ? WHERE id = ?", (notes, model_id))
        self._conn.commit()

    def archive_model(self, model_id: str, archived: bool = True) -> None:
        self._conn.execute("UPDATE ml_models SET archived = ? WHERE id = ?", (int(archived), model_id))
        self._conn.commit()

    def delete_model(self, model_id: str) -> Optional[str]:
        """Deletes the row and returns its `artifact_path` (if any) so the
        caller can remove the file on disk too -- the DB row is not the only
        thing that needs cleaning up."""
        row = self.fetch_model(model_id)
        self._conn.execute("DELETE FROM ml_models WHERE id = ?", (model_id,))
        self._conn.commit()
        return row["artifact_path"] if row else None

    def fetch_model(self, model_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM ml_models WHERE id = ?", (model_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        return _decode_model(dict(row)) if row is not None else None

    def fetch_models(
        self, *, strategy: Optional[str] = None, model_family: Optional[str] = None,
        include_archived: bool = False,
    ) -> list[dict]:
        query = "SELECT * FROM ml_models"
        clauses, params = [], []
        if strategy is not None:
            clauses.append("strategy = ?")
            params.append(strategy)
        if model_family is not None:
            clauses.append("model_family = ?")
            params.append(model_family)
        if not include_archived:
            clauses.append("archived = 0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [_decode_model(dict(row)) for row in rows]

    def fetch_model_versions(self, model_family: str) -> list[dict]:
        """Full version history for a family, oldest first -- the shape
        feature-importance-over-time and rollback pickers both want."""
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM ml_models WHERE model_family = ? ORDER BY version", (model_family,)
        )
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [_decode_model(dict(row)) for row in rows]

    # --- Model deployments (Phase 9) ---

    def insert_deployment(self, *, deployment_id: str, strategy: str, model_id: Optional[str], action: str) -> None:
        self._conn.execute(
            "INSERT INTO model_deployments (id, strategy, model_id, action) VALUES (?,?,?,?)",
            (deployment_id, strategy, model_id, action),
        )
        self._conn.commit()

    def fetch_current_deployment(self, strategy: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM model_deployments WHERE strategy = ? ORDER BY created_at DESC LIMIT 1", (strategy,)
        )
        row = cur.fetchone()
        self._conn.row_factory = None
        if row is None:
            return None
        d = dict(row)
        return d if d["action"] != "undeploy" else None

    def fetch_deployment_history(self, strategy: str) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM model_deployments WHERE strategy = ? ORDER BY created_at DESC", (strategy,)
        )
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    # --- Client trade importer (Phase 10.1) ---

    def insert_client_profile(self, *, profile_id: str, name: str, notes: Optional[str] = None) -> None:
        self._conn.execute(
            "INSERT INTO client_profiles (id, name, notes) VALUES (?,?,?)", (profile_id, name, notes),
        )
        self._conn.commit()

    def fetch_client_profile(self, profile_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM client_profiles WHERE id = ?", (profile_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        return dict(row) if row is not None else None

    def fetch_client_profiles(self) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM client_profiles ORDER BY name")
        rows = cur.fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    def insert_import_staging(
        self, *, staging_id: str, profile_id: str, filename: str, stored_path: str,
        detected_format: str, suggested_mapping: dict,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO import_staging (id, profile_id, filename, stored_path, detected_format, suggested_mapping)
            VALUES (?,?,?,?,?,?)
            """,
            (staging_id, profile_id, filename, stored_path, detected_format, json.dumps(suggested_mapping)),
        )
        self._conn.commit()

    def fetch_import_staging(self, staging_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM import_staging WHERE id = ?", (staging_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        if row is None:
            return None
        d = dict(row)
        d["suggested_mapping"] = json.loads(d["suggested_mapping"])
        return d

    def delete_import_staging(self, staging_id: str) -> Optional[str]:
        """Deletes the row and returns its `stored_path` (if any) so the
        caller can remove the file on disk too."""
        row = self.fetch_import_staging(staging_id)
        self._conn.execute("DELETE FROM import_staging WHERE id = ?", (staging_id,))
        self._conn.commit()
        return row["stored_path"] if row else None

    def fetch_existing_fingerprints(self, profile_id: str, fingerprints: Sequence[str]) -> set[str]:
        """Which of `fingerprints` are already in this profile's fill ledger
        -- the duplicate-detection check, done as one bulk query rather than
        one round-trip per fill."""
        if not fingerprints:
            return set()
        placeholders = ",".join("?" for _ in fingerprints)
        cur = self._conn.execute(
            f"SELECT fingerprint FROM client_import_fills WHERE profile_id = ? AND fingerprint IN ({placeholders})",
            (profile_id, *fingerprints),
        )
        return {row[0] for row in cur.fetchall()}

    def fetch_open_lots(self, profile_id: str, symbol: str) -> list[dict]:
        """The persisted FIFO queue for one (profile, symbol), oldest first
        -- exactly the order `FifoMatcher` must consume them in."""
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(
            "SELECT * FROM client_open_lots WHERE profile_id = ? AND symbol = ? ORDER BY entry_time, id",
            (profile_id, symbol),
        )
        rows = cur.fetchall()
        self._conn.row_factory = None
        out = []
        for row in rows:
            d = dict(row)
            d["quantity_remaining"] = Decimal(d["quantity_remaining"])
            d["entry_price"] = Decimal(d["entry_price"])
            d["raw_entry_row"] = json.loads(d["raw_entry_row"])
            out.append(d)
        return out

    def commit_client_import(
        self, *,
        import_id: str, profile_id: str, filename: str, detected_format: str,
        new_fill_rows: Sequence[dict], consumed_lot_ids: Sequence[str],
        updated_lots: Sequence[tuple], new_open_lots: Sequence[dict],
        trade_records: Sequence[TradeRecord],
        total_fill_rows: int, imported_fill_count: int, duplicate_fill_count: int,
        error_count: int, errors: list, warnings: list, job_id: Optional[str] = None,
    ) -> None:
        """Writes everything one import batch produces -- new fill-ledger
        rows, consumed/updated/new open lots, the closed `TradeRecord`s, and
        the `import_history` row -- in a single transaction, committing
        once at the end. This is what keeps `client_open_lots`'s FIFO
        ordering safe: a failure partway through (a bad trade record, a
        constraint violation) rolls everything in this batch back together,
        rather than leaving the open-lot queue half-updated against a fill
        ledger that thinks the whole batch already landed.
        """
        try:
            for f in new_fill_rows:
                self._conn.execute(
                    """
                    INSERT INTO client_import_fills
                        (id, profile_id, fingerprint, symbol, fill_time, side, quantity, price, raw_row, import_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uuid.uuid4().hex[:12], profile_id, f["fingerprint"], f["symbol"], f["fill_time"],
                        f["side"], str(f["quantity"]), str(f["price"]), json.dumps(_jsonable(f["raw_row"])),
                        import_id,
                    ),
                )

            for lot_id in consumed_lot_ids:
                self._conn.execute("DELETE FROM client_open_lots WHERE id = ?", (lot_id,))

            for lot_id, new_quantity in updated_lots:
                self._conn.execute(
                    "UPDATE client_open_lots SET quantity_remaining = ? WHERE id = ?", (str(new_quantity), lot_id),
                )

            for lot in new_open_lots:
                self._conn.execute(
                    """
                    INSERT INTO client_open_lots
                        (id, profile_id, symbol, side, quantity_remaining, entry_price, entry_time,
                         entry_fingerprint, raw_entry_row)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        uuid.uuid4().hex[:12], profile_id, lot["symbol"], lot["side"],
                        str(lot["quantity_remaining"]), str(lot["entry_price"]), lot["entry_time"],
                        lot["entry_fingerprint"], json.dumps(_jsonable(lot["raw_entry_row"])),
                    ),
                )

            self._insert_trades_no_commit(trade_records)

            self._conn.execute(
                """
                INSERT INTO import_history (
                    id, profile_id, filename, detected_format, status,
                    total_fill_rows, imported_fill_count, duplicate_fill_count, error_count, trades_created,
                    errors, warnings, job_id
                ) VALUES (?,?,?,?,'completed',?,?,?,?,?,?,?,?)
                """,
                (
                    import_id, profile_id, filename, detected_format,
                    total_fill_rows, imported_fill_count, duplicate_fill_count, error_count, len(trade_records),
                    json.dumps(_jsonable_deep(errors)), json.dumps(warnings), job_id,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def fail_client_import(
        self, *, import_id: str, profile_id: str, filename: str, detected_format: str,
        error_message: str, job_id: Optional[str] = None,
    ) -> None:
        """A separate, always-fresh write path for the failure record itself
        -- used when `commit_client_import` above rolled back, so the
        failure is still visible in Import History even though none of the
        batch's data landed."""
        self._conn.execute(
            """
            INSERT INTO import_history (
                id, profile_id, filename, detected_format, status,
                total_fill_rows, imported_fill_count, duplicate_fill_count, error_count, trades_created,
                errors, warnings, job_id, error_message
            ) VALUES (?,?,?,?,'failed',0,0,0,0,0,'[]',NULL,?,?)
            """,
            (import_id, profile_id, filename, detected_format, job_id, error_message),
        )
        self._conn.commit()

    # --- Config parameter deploy/rollback log (Phase 10.2) ---

    def insert_config_deployment(
        self, *, deployment_id: str, strategy: str, action: str, params: dict, backup_path: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO config_deployments (id, strategy, action, params, backup_path) VALUES (?,?,?,?,?)",
            (deployment_id, strategy, action, json.dumps(_jsonable_deep(params)), backup_path),
        )
        self._conn.commit()

    def fetch_config_deployment(self, deployment_id: str) -> Optional[dict]:
        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute("SELECT * FROM config_deployments WHERE id = ?", (deployment_id,))
        row = cur.fetchone()
        self._conn.row_factory = None
        if row is None:
            return None
        d = dict(row)
        d["params"] = json.loads(d["params"])
        return d

    def fetch_config_deployments(self, strategy: Optional[str] = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM config_deployments"
        params: list = []
        if strategy is not None:
            query += " WHERE strategy = ?"
            params.append(strategy)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        out = []
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"])
            out.append(d)
        return out

    def fetch_import_history(self, profile_id: Optional[str] = None, limit: int = 100) -> list[dict]:
        query = "SELECT * FROM import_history"
        params: list = []
        if profile_id is not None:
            query += " WHERE profile_id = ?"
            params.append(profile_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        self._conn.row_factory = sqlite3.Row
        cur = self._conn.execute(query, params)
        rows = cur.fetchall()
        self._conn.row_factory = None
        out = []
        for row in rows:
            d = dict(row)
            d["errors"] = json.loads(d["errors"])
            d["warnings"] = json.loads(d["warnings"]) if d.get("warnings") else []
            out.append(d)
        return out


def _decode_job(d: dict) -> dict:
    d["request"] = json.loads(d["request"])
    d["result_payload"] = json.loads(d["result_payload"]) if d.get("result_payload") else None
    return d


def _decode_experiment(d: dict) -> dict:
    d["parameters"] = json.loads(d["parameters"])
    d["metrics"] = json.loads(d["metrics"]) if d.get("metrics") else None
    return d


def _decode_model(d: dict) -> dict:
    d["feature_columns"] = json.loads(d["feature_columns"])
    d["hyperparameters"] = json.loads(d["hyperparameters"])
    d["metrics"] = json.loads(d["metrics"]) if d.get("metrics") else None
    d["feature_importance"] = json.loads(d["feature_importance"]) if d.get("feature_importance") else None
    d["derived_backtest_metrics"] = (
        json.loads(d["derived_backtest_metrics"]) if d.get("derived_backtest_metrics") else None
    )
    d["overfit_warning"] = bool(d["overfit_warning"])
    d["archived"] = bool(d["archived"])
    return d


_RUN_DECIMAL_FIELDS = (
    "starting_equity", "net_pnl", "profit_factor", "win_rate", "expectancy",
    "sharpe_ratio", "sortino_ratio", "max_drawdown", "max_drawdown_pct",
    "validation_net_pnl", "validation_profit_factor",
)


def _decode_run(d: dict) -> dict:
    d["strategy_params"] = json.loads(d["strategy_params"])
    d["walk_forward"] = bool(d["walk_forward"])
    d["caveats"] = json.loads(d["caveats"]) if d["caveats"] else []
    for field in _RUN_DECIMAL_FIELDS:
        d[field] = _decimal_or_none(d[field])
    return d


def _jsonable(value: dict) -> dict:
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in value.items()}


def _jsonable_deep(value: object) -> object:
    """Recursive version of `_jsonable`, for `jobs.result_payload` -- unlike
    every other JSON column here (strategy_params, entry_metadata, a
    request body), a job result like `CompareResult` is a nested structure
    (a list of entries, each with its own equity-curve list of points, each
    carrying Decimal money fields), so a shallow top-level-only conversion
    isn't enough."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable_deep(v) for v in value]
    return value
