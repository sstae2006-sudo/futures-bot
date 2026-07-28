import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

from futures_bot.db.research_schema import metadata as research_metadata
from futures_bot.db.schema import metadata as market_data_metadata

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Contains credentials -- never read from alembic.ini (tracked in git).
# Same env-var-only convention as MASSIVE_API_KEY/TRADOVATE_* -- see
# db/engine.py's DATABASE_URL_ENV docstring. `alembic upgrade head` fails
# loudly with a clear message rather than silently trying `driver://...`
# (alembic.ini's own placeholder) if this isn't set.
_database_url = os.environ.get("FUTURES_BOT_DATABASE_URL")
if _database_url:
    config.set_main_option("sqlalchemy.url", _database_url)

# Both databases' schemas -- market_data.db's 5 tables (db/schema.py) and
# research.db's 15 (db/research_schema.py). Alembic's autogenerate accepts
# a list of MetaData objects directly; every table from both ends up in
# the same migration history/versions/ directory since they share one
# Postgres/TimescaleDB instance in team-deployment mode (see
# TEAM_DEPLOYMENT.md) -- there's no need for two separate Alembic setups.
target_metadata = [market_data_metadata, research_metadata]

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    if not _database_url:
        raise RuntimeError(
            "FUTURES_BOT_DATABASE_URL is not set. `alembic upgrade head` needs "
            "a real Postgres/TimescaleDB DSN -- see TEAM_DEPLOYMENT.md."
        )
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
