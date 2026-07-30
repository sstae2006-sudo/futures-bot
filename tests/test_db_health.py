"""`db/health.py::check_database_health` -- the three states it must
distinguish: unconfigured (no team deployment at all), configured but
unreachable (a team deployment whose server is down), and configured and
healthy (the real, working case). PROJECT_STATE.md referenced this
coverage before it existed as a committed test -- added here so it's a
permanent regression check, not a one-off manual verification.

`TestConfiguredAndHealthy`'s real-server case (and `TestConfiguredButUnreachable`'s
wrong-credentials case) go through `conftest.py`'s `live_database_url`
fixture, which -- per KNOWN_ISSUES.md ISSUE-041 -- now additionally
requires `FUTURES_BOT_ALLOW_LIVE_DB_TESTS=1` (not just a reachable
`FUTURES_BOT_DATABASE_URL`) before it will restore a real DSN; both
cases `pytest.skip()` cleanly when that fixture returns `None`. This
module never writes to the database (read-only health probes only), but
is gated the same way as the modules that do, for consistency and
because a probe that reveals real connectivity/credentials shouldn't
run against a shared instance without the same deliberate opt-in either.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("psycopg")

from futures_bot.db.engine import DATABASE_URL_ENV, database_url, dispose_engine  # noqa: E402
from futures_bot.db.health import check_database_health  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_engine_between_tests(monkeypatch):
    """Each test may point FUTURES_BOT_DATABASE_URL at a different target
    -- `get_engine()` caches its Engine as a module-level singleton, so a
    stale one from a previous test/case must never leak into the next."""
    dispose_engine()
    yield
    dispose_engine()


class TestUnconfigured:
    def test_no_env_var_reports_not_configured_and_not_ok(self, monkeypatch):
        monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
        health = check_database_health()
        assert health.configured is False
        assert health.ok is False
        assert health.latency_ms is None
        assert health.error is None

    def test_database_url_helper_returns_none_when_unset(self, monkeypatch):
        monkeypatch.delenv(DATABASE_URL_ENV, raising=False)
        assert database_url() is None

    def test_empty_string_env_var_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv(DATABASE_URL_ENV, "")
        assert database_url() is None
        health = check_database_health()
        assert health.configured is False


class TestConfiguredButUnreachable:
    def test_unreachable_host_reports_configured_not_ok_with_error(self, monkeypatch):
        # A bogus but well-formed DSN -- connect_timeout (db/engine.py's
        # get_engine default) bounds how long this can hang.
        monkeypatch.setenv(
            DATABASE_URL_ENV,
            "postgresql+psycopg://nobody:nobody@10.255.255.1:5432/nonexistent",
        )
        health = check_database_health()
        assert health.configured is True
        assert health.ok is False
        assert health.latency_ms is not None
        assert health.error is not None

    def test_wrong_credentials_against_a_real_reachable_server_reports_not_ok(self, monkeypatch, live_timescaledb_url):
        pytest.importorskip("psycopg")
        if live_timescaledb_url is None:
            pytest.skip("no live TimescaleDB configured for this run")
        bad_url = live_timescaledb_url.replace("futures_bot_dev_only", "definitely_wrong_password")
        if bad_url == live_timescaledb_url:
            pytest.skip("could not construct a deliberately-wrong DSN from the configured URL")
        monkeypatch.setenv(DATABASE_URL_ENV, bad_url)
        health = check_database_health()
        assert health.configured is True
        assert health.ok is False
        assert health.error is not None


@pytest.fixture()
def live_timescaledb_url(live_database_url):
    """The real, working DSN this test run was invoked with (if any) --
    delegates to conftest.py's `live_database_url`, which restores it
    after the session-wide autouse fixture clears it for hermeticity
    (see conftest.py's own docstring)."""
    return live_database_url


class TestConfiguredAndHealthy:
    def test_real_server_reports_ok_with_real_latency(self, live_timescaledb_url):
        if live_timescaledb_url is None:
            pytest.skip(
                f"No reachable Postgres/TimescaleDB configured via {DATABASE_URL_ENV} "
                "-- start deploy/docker-compose.yml's timescaledb service to run this case."
            )
        import os

        os.environ[DATABASE_URL_ENV] = live_timescaledb_url
        health = check_database_health()
        assert health.configured is True
        assert health.ok is True
        assert health.latency_ms is not None and health.latency_ms >= 0
        assert health.error is None
