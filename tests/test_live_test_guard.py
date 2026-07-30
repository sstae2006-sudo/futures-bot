"""Regression coverage for `tests/_live_test_guard.py` and
`conftest.py::live_database_url` -- KNOWN_ISSUES.md ISSUE-041: a routine
`pytest` run in a shell with `FUTURES_BOT_DATABASE_URL` ambient-set (now
commonly a *persistent* Windows env var, see `TEAM_DEPLOYMENT.md`) must
never be enough on its own to run the destructive live-Postgres suite --
`FUTURES_BOT_ALLOW_LIVE_DB_TESTS=1` must be independently, deliberately
set too. These tests never touch a real database -- pure environment-
variable/monkeypatch logic.
"""

from __future__ import annotations

import pytest

from tests._live_test_guard import ALLOW_LIVE_DB_TESTS_ENV, live_tests_allowed, skip_reason


class TestLiveTestsAllowed:
    def test_false_when_opt_in_var_unset(self, monkeypatch):
        monkeypatch.delenv(ALLOW_LIVE_DB_TESTS_ENV, raising=False)
        assert live_tests_allowed() is False

    def test_false_when_opt_in_var_is_any_other_value(self, monkeypatch):
        monkeypatch.setenv(ALLOW_LIVE_DB_TESTS_ENV, "true")
        assert live_tests_allowed() is False

    def test_true_only_for_the_exact_literal_1(self, monkeypatch):
        monkeypatch.setenv(ALLOW_LIVE_DB_TESTS_ENV, "1")
        assert live_tests_allowed() is True


class TestLiveServerReachable:
    def test_false_when_opt_in_missing_even_if_database_url_is_set_and_reachable(self, monkeypatch):
        """The exact scenario this whole fix exists for: a real, reachable
        FUTURES_BOT_DATABASE_URL (e.g. a persistent Windows env var) must
        NOT be sufficient on its own."""
        monkeypatch.delenv(ALLOW_LIVE_DB_TESTS_ENV, raising=False)
        monkeypatch.setenv("FUTURES_BOT_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:5432/z")

        from tests._live_test_guard import live_server_reachable

        # Must short-circuit on the opt-in check before ever touching the
        # database -- confirmed by NOT needing check_database_health to be
        # mocked/reachable at all for this to return False.
        assert live_server_reachable() is False

    def test_false_when_opt_in_set_but_no_database_url(self, monkeypatch):
        monkeypatch.setenv(ALLOW_LIVE_DB_TESTS_ENV, "1")
        monkeypatch.delenv("FUTURES_BOT_DATABASE_URL", raising=False)

        from tests._live_test_guard import live_server_reachable

        assert live_server_reachable() is False


class TestSkipReason:
    def test_mentions_the_opt_in_var_when_not_opted_in(self, monkeypatch):
        monkeypatch.delenv(ALLOW_LIVE_DB_TESTS_ENV, raising=False)
        assert ALLOW_LIVE_DB_TESTS_ENV in skip_reason()
        assert "TRUNCATE" in skip_reason()

    def test_mentions_reachability_when_opted_in(self, monkeypatch):
        monkeypatch.setenv(ALLOW_LIVE_DB_TESTS_ENV, "1")
        assert "reachable" in skip_reason().lower()


class TestLiveDatabaseUrlFixture:
    """`conftest.py::live_database_url` -- the fixture every live-test
    module's `store`/`client` fixtures depend on. The exact scenario this
    fix exists for: a real, reachable `FUTURES_BOT_DATABASE_URL` (e.g.
    persisted as a Windows env var) must not, on its own, cause this
    fixture to hand back a real DSN."""

    def test_returns_none_when_opt_in_not_set_even_with_a_real_ambient_url(self, request, monkeypatch):
        import tests.conftest as conftest_module

        monkeypatch.delenv(ALLOW_LIVE_DB_TESTS_ENV, raising=False)
        monkeypatch.setattr(conftest_module, "_REAL_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:5432/z")

        result = request.getfixturevalue("live_database_url")

        assert result is None

    def test_fixture_returns_none_when_no_real_database_url_was_ever_configured(self, request, monkeypatch):
        monkeypatch.setenv(ALLOW_LIVE_DB_TESTS_ENV, "1")
        # Even opted in, if this session was never invoked with a real
        # FUTURES_BOT_DATABASE_URL in the first place (the common case,
        # including CI), the fixture must still return None, not error.
        import tests.conftest as conftest_module

        monkeypatch.setattr(conftest_module, "_REAL_DATABASE_URL", None)
        result = request.getfixturevalue("live_database_url")
        assert result is None

    def test_returns_the_real_url_when_both_conditions_are_met(self, request, monkeypatch):
        import tests.conftest as conftest_module

        monkeypatch.setenv(ALLOW_LIVE_DB_TESTS_ENV, "1")
        monkeypatch.setattr(conftest_module, "_REAL_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:5432/z")

        result = request.getfixturevalue("live_database_url")

        assert result == "postgresql+psycopg://x:y@127.0.0.1:5432/z"
        import os

        assert os.environ.get("FUTURES_BOT_DATABASE_URL") == "postgresql+psycopg://x:y@127.0.0.1:5432/z"
