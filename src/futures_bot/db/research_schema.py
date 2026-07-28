"""SQLAlchemy Core table definitions for `research.db`'s Postgres/
TimescaleDB port -- the second half of the team-deployment plan
(`market_data.db`'s port, `db/schema.py`, was done and verified first, on
purpose, as the smaller of the two databases).

Defines the same 15 tables as `research/trade_store.py::_SCHEMA`,
including every column that string's own `ensure_schema()` later added via
`ALTER TABLE ADD COLUMN` (`_TRADE_ANALYTICS_COLUMNS`, `_TRADE_STORAGE_COLUMNS`,
`_TRIAL_RESUME_COLUMNS`, `_EXPERIMENT_ML_COLUMNS`) -- there's no migration
history to replay here, so this schema is written as the final shape from
the start.

Type translation follows the exact precedent `db/schema.py` already
established for `bars`/etc.: `NUMERIC` for every Decimal-as-TEXT money/
quantity column (exact precision, psycopg maps it to `Decimal`
automatically -- genuinely better than TEXT, not just a mechanical port),
`TIMESTAMPTZ` for real timestamp columns, `JSONB` for every JSON-as-TEXT
column (native, queryable, and the driver decodes it automatically -- so
`PgTradeStore` never calls `json.loads()` on these), `Boolean` for the
INTEGER-as-0/1 flags (`walk_forward`, `overfit_warning`, `archived`).

**One deliberate, disclosed divergence from `TradeStore`'s exact return
shapes**: a few SQLite methods only conditionally decode certain Decimal
fields into `Decimal` (e.g. `fetch_optimization_trials` decodes
`train_net_pnl` but leaves `train_gross_losses` as a raw string) --
apparently unintentional asymmetry in the original code, not a documented
contract. `PgTradeStore` returns `Decimal` for every `NUMERIC` column
everywhere, consistently, matching the "native types" improvement already
established for `market_data.db`. Timestamp columns that `TradeStore`
returns as a raw ISO string (`entry_time`, `created_at`, etc. -- it never
parses these back into `datetime`) are converted back to `.isoformat()`
strings by `PgTradeStore` before returning, specifically to avoid that
divergence -- see `pg_trade_store.py::_isoformat_timestamps`.

**Verified against a real server** (see PROJECT_STATE.md) via
`alembic upgrade head` against `deploy/docker-compose.yml`'s `timescaledb`
service and `tests/test_pg_trade_store_live.py`.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Identity,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

metadata = MetaData()

#: `TIMESTAMP(timezone=True)` -- Postgres `TIMESTAMPTZ`, the native
#: equivalent of `trade_store.py`'s "ISO 8601 TEXT" convention, same as
#: `db/schema.py::_TSTZ`. Not shared with that module -- these two schema
#: modules are deliberately independent (see this module's own docstring
#: and `db/schema.py`'s), matching the "two independent store classes"
#: design.
_TSTZ = TIMESTAMP(timezone=True)

trades = Table(
    "trades",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True),
    Column("run_id", String, nullable=False),
    Column("contract", String, nullable=False),
    Column("strategy", String, nullable=False),
    Column("strategy_params", JSONB, nullable=False),
    Column("entry_time", _TSTZ, nullable=False),
    Column("exit_time", _TSTZ, nullable=False),
    Column("side", String, nullable=False),
    Column("entry_price", Numeric, nullable=False),
    Column("exit_price", Numeric, nullable=False),
    Column("gross_pnl", Numeric, nullable=False),
    Column("commission", Numeric, nullable=False),
    Column("net_pnl", Numeric, nullable=False),
    Column("holding_minutes", Numeric, nullable=False),
    Column("exit_reason", String, nullable=False),
    Column("session_date", String, nullable=False),
    Column("day_of_week", String, nullable=False),
    Column("hour", Integer, nullable=False),
    Column("entry_reason", String, nullable=False),
    Column("entry_metadata", JSONB, nullable=False),
    Column("outcome", String, nullable=False),
    # _TRADE_ANALYTICS_COLUMNS (Phase 6B, added later via ALTER TABLE on SQLite):
    Column("mfe_points", Numeric),
    Column("mae_points", Numeric),
    Column("efficiency", Numeric),
    Column("regime_trend", String),
    Column("regime_volatility", String),
    Column("regime_session", String),
    # _TRADE_STORAGE_COLUMNS (Phase 10.3, added later via ALTER TABLE on SQLite):
    Column("trade_id", String),
    Column("stop_loss", Numeric),
    Column("take_profit", Numeric),
    Column("entry_slippage", Numeric),
    Column("exit_slippage", Numeric),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_trades_run_id", "run_id"),
    Index("idx_trades_strategy", "strategy"),
    Index("idx_trades_session_date", "session_date"),
    UniqueConstraint("trade_id", name="uq_trades_trade_id"),
)

optimization_trials = Table(
    "optimization_trials",
    metadata,
    Column("id", BigInteger, Identity(always=False), primary_key=True),
    Column("batch_id", String, nullable=False),
    Column("strategy", String, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("combo_key", String),
    Column("train_trades", Integer, nullable=False),
    Column("train_net_pnl", Numeric, nullable=False),
    Column("train_profit_factor", Numeric),
    Column("train_max_drawdown", Numeric, nullable=False),
    Column("train_gross_losses", Numeric),
    Column("train_win_count", Integer),
    Column("train_largest_win", Numeric),
    Column("validation_trades", Integer),
    Column("validation_net_pnl", Numeric),
    Column("validation_profit_factor", Numeric),
    Column("validation_max_drawdown", Numeric),
    Column("rank", Integer),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_trials_batch_id", "batch_id"),
    UniqueConstraint("batch_id", "combo_key", name="uq_trials_batch_combo"),
)

runs = Table(
    "runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("strategy", String, nullable=False),
    Column("contract", String, nullable=False),
    Column("strategy_params", JSONB, nullable=False),
    Column("csv_path", String),
    Column("walk_forward", Boolean, nullable=False, server_default="false"),
    Column("starting_equity", Numeric),
    Column("trade_count", Integer),
    Column("net_pnl", Numeric),
    Column("profit_factor", Numeric),
    Column("win_rate", Numeric),
    Column("expectancy", Numeric),
    Column("sharpe_ratio", Numeric),
    Column("sortino_ratio", Numeric),
    Column("max_drawdown", Numeric),
    Column("max_drawdown_pct", Numeric),
    Column("validation_trade_count", Integer),
    Column("validation_net_pnl", Numeric),
    Column("validation_profit_factor", Numeric),
    Column("caveats", JSONB),
    Column("first_bar", _TSTZ),
    Column("last_bar", _TSTZ),
    Column("error_message", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Column("completed_at", _TSTZ),
    Index("idx_runs_strategy", "strategy"),
    Index("idx_runs_created_at", "created_at"),
)

reports = Table(
    "reports",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("format", String, nullable=False),
    Column("path", String, nullable=False),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_reports_run_id", "run_id"),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("progress_current", Integer, nullable=False, server_default="0"),
    Column("progress_total", Integer, nullable=False, server_default="0"),
    Column("progress_message", String),
    Column("request", JSONB, nullable=False),
    Column("result_id", String),
    Column("result_payload", JSONB),
    Column("error_message", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Column("started_at", _TSTZ),
    Column("completed_at", _TSTZ),
    Index("idx_jobs_created_at", "created_at"),
    Index("idx_jobs_status", "status"),
)

experiments = Table(
    "experiments",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("hypothesis", String, nullable=False),
    Column("strategy", String, nullable=False),
    Column("dataset", String),
    Column("parameters", JSONB, nullable=False),
    Column("run_id", String),
    Column("notes", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    # _EXPERIMENT_ML_COLUMNS (Phase 9, added later via ALTER TABLE on SQLite):
    Column("model_id", String),
    Column("dataset_version", String),
    Column("metrics", JSONB),
    Index("idx_experiments_strategy", "strategy"),
)

ml_models = Table(
    "ml_models",
    metadata,
    Column("id", String, primary_key=True),
    Column("model_family", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("strategy", String, nullable=False),
    Column("model_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("job_id", String),
    Column("feature_columns", JSONB, nullable=False),
    Column("hyperparameters", JSONB, nullable=False),
    Column("evaluation_mode", String, nullable=False),
    Column("dataset_size", Integer, nullable=False),
    Column("dataset_version", String, nullable=False),
    Column("metrics", JSONB),
    Column("feature_importance", JSONB),
    Column("overfit_warning", Boolean, nullable=False, server_default="false"),
    Column("overfit_note", String),
    Column("derived_backtest_metrics", JSONB),
    Column("artifact_path", String),
    Column("app_version", String),
    Column("git_commit", String),
    Column("notes", String),
    Column("archived", Boolean, nullable=False, server_default="false"),
    Column("error_message", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Column("completed_at", _TSTZ),
    Index("idx_ml_models_family", "model_family"),
    Index("idx_ml_models_strategy", "strategy"),
)

model_deployments = Table(
    "model_deployments",
    metadata,
    Column("id", String, primary_key=True),
    Column("strategy", String, nullable=False),
    Column("model_id", String),
    Column("action", String, nullable=False),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_model_deployments_strategy", "strategy", "created_at"),
)

client_profiles = Table(
    "client_profiles",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("notes", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
)

import_staging = Table(
    "import_staging",
    metadata,
    Column("id", String, primary_key=True),
    Column("profile_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("stored_path", String, nullable=False),
    Column("detected_format", String, nullable=False),
    Column("suggested_mapping", JSONB, nullable=False),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
)

client_import_fills = Table(
    "client_import_fills",
    metadata,
    Column("id", String, primary_key=True),
    Column("profile_id", String, nullable=False),
    Column("fingerprint", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("fill_time", _TSTZ, nullable=False),
    Column("side", String, nullable=False),
    Column("quantity", Numeric, nullable=False),
    Column("price", Numeric, nullable=False),
    Column("raw_row", JSONB, nullable=False),
    Column("import_id", String, nullable=False),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    UniqueConstraint("profile_id", "fingerprint", name="uq_client_import_fills_profile_fingerprint"),
    Index("idx_client_import_fills_profile", "profile_id"),
)

client_open_lots = Table(
    "client_open_lots",
    metadata,
    Column("id", String, primary_key=True),
    Column("profile_id", String, nullable=False),
    Column("symbol", String, nullable=False),
    Column("side", String, nullable=False),
    Column("quantity_remaining", Numeric, nullable=False),
    Column("entry_price", Numeric, nullable=False),
    Column("entry_time", _TSTZ, nullable=False),
    Column("entry_fingerprint", String, nullable=False),
    Column("raw_entry_row", JSONB, nullable=False),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_client_open_lots_profile_symbol", "profile_id", "symbol", "entry_time"),
)

import_history = Table(
    "import_history",
    metadata,
    Column("id", String, primary_key=True),
    Column("profile_id", String, nullable=False),
    Column("filename", String, nullable=False),
    Column("detected_format", String, nullable=False),
    Column("status", String, nullable=False),
    Column("total_fill_rows", Integer, nullable=False),
    Column("imported_fill_count", Integer, nullable=False),
    Column("duplicate_fill_count", Integer, nullable=False),
    Column("error_count", Integer, nullable=False),
    Column("trades_created", Integer, nullable=False),
    Column("errors", JSONB, nullable=False),
    Column("warnings", JSONB),
    Column("job_id", String),
    Column("error_message", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_import_history_profile", "profile_id", "created_at"),
)

config_deployments = Table(
    "config_deployments",
    metadata,
    Column("id", String, primary_key=True),
    Column("strategy", String, nullable=False),
    Column("action", String, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("backup_path", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_config_deployments_strategy", "strategy", "created_at"),
)

# --- Lightweight user/organization accounts (Team Collaboration MVP) ---
# See src/futures_bot/accounts/store.py's module docstring for the full
# design rationale (not an auth system -- a data model plus basic CRUD).
# Same two tables, same shape as accounts/store.py's SQLite _SCHEMA.

organizations = Table(
    "organizations",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False, unique=True),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
)

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("username", String, nullable=False, unique=True),
    Column("email", String),
    Column("avatar_url", String),
    Column("org_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Column("last_active_at", _TSTZ),
    # Profile fields added for the registration/profile feature (2026-07-28)
    # -- see accounts/store.py's module docstring for why api_key isn't an
    # enforced credential yet.
    Column("api_key", String, unique=True),
    Column("timezone", String),
    Column("preferred_ai_model", String),
    Column("default_branch_prefix", String),
    Column("notification_preferences", JSONB),
    Index("idx_users_org_id", "org_id"),
)

# --- Active Work Registry (Team Collaboration MVP) ---
# See src/futures_bot/collaboration/store.py's module docstring. Same two
# tables, same shape as that module's SQLite _SCHEMA.

work_items = Table(
    "work_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("description", String),
    Column("owner_user_id", String),
    Column("branch", String),
    Column("status", String, nullable=False, server_default="open"),
    Column("estimated_files", JSONB, nullable=False, server_default="[]"),
    Column("priority", String, nullable=False, server_default="medium"),
    Column("owner_type", String, nullable=False, server_default="human"),
    Column("org_id", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Column("updated_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_work_items_status", "status"),
    Index("idx_work_items_org_id", "org_id"),
)

work_item_activity = Table(
    "work_item_activity",
    metadata,
    Column("id", String, primary_key=True),
    Column("work_item_id", String, nullable=False),
    Column("event", String, nullable=False),
    Column("actor_user_id", String),
    Column("detail", String),
    Column("created_at", _TSTZ, nullable=False, server_default=func.now()),
    Index("idx_work_item_activity_item", "work_item_id", "created_at"),
)
