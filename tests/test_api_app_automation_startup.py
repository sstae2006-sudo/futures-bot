"""Tests for `api.app._maybe_start_automation` -- SIL Phase 4/5's
background-scheduler auto-start gating. This function was restructured
when git-sync was added (from one early-return gate covering
git-watcher+maintenance, to two independent gates), which is exactly the
kind of change that can silently invert a condition -- these tests exist
specifically to catch that class of regression, not just to exercise the
happy path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from futures_bot.api.app import _maybe_start_automation
from futures_bot.collaboration.git_sync import get_git_sync_scheduler, reset_git_sync_scheduler
from futures_bot.collaboration.git_watcher import get_git_watcher, reset_git_watcher
from futures_bot.collaboration.maintenance import get_maintenance_scheduler, reset_maintenance_scheduler

_MINIMAL_RISK_BLOCK = """
risk:
  contracts_per_trade: 1
  stop_loss_points: 10
  take_profit_points: 20
  daily_max_loss: 120
  max_trades_per_session: 3
  account_size: 2500
"""


def _write_config(tmp_path: Path, automation_yaml: str) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MINIMAL_RISK_BLOCK + automation_yaml, encoding="utf-8")
    return config_path


@pytest.fixture(autouse=True)
def _reset_all_schedulers():
    reset_git_watcher()
    reset_maintenance_scheduler()
    reset_git_sync_scheduler()
    yield
    # Stop anything a test actually started, then reset the singletons --
    # a leaked running thread would otherwise bleed into later tests.
    for getter in (get_git_watcher, get_maintenance_scheduler, get_git_sync_scheduler):
        scheduler = getter()
        if scheduler.status()["running"]:
            scheduler.stop(timeout=5)
    reset_git_watcher()
    reset_maintenance_scheduler()
    reset_git_sync_scheduler()


class TestNoConfigFile:
    def test_missing_config_starts_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FUTURES_BOT_CONFIG", str(tmp_path / "does-not-exist.yaml"))

        _maybe_start_automation()

        assert get_git_watcher().status()["running"] is False
        assert get_maintenance_scheduler().status()["running"] is False
        assert get_git_sync_scheduler().status()["running"] is False


class TestBothDisabled:
    def test_default_config_starts_nothing(self, tmp_path, monkeypatch):
        config_path = _write_config(tmp_path, "")  # automation defaults: both False
        monkeypatch.setenv("FUTURES_BOT_CONFIG", str(config_path))

        _maybe_start_automation()

        assert get_git_watcher().status()["running"] is False
        assert get_maintenance_scheduler().status()["running"] is False
        assert get_git_sync_scheduler().status()["running"] is False


class TestAutomationEnabledOnly:
    def test_starts_git_watcher_and_maintenance_but_not_git_sync(self, tmp_path, monkeypatch):
        config_path = _write_config(tmp_path, "\nautomation:\n  enabled: true\n")
        monkeypatch.setenv("FUTURES_BOT_CONFIG", str(config_path))

        _maybe_start_automation()

        assert get_git_watcher().status()["running"] is True
        assert get_maintenance_scheduler().status()["running"] is True
        assert get_git_sync_scheduler().status()["running"] is False


class TestGitSyncEnabledOnly:
    def test_starts_only_git_sync(self, tmp_path, monkeypatch):
        """The regression this test guards against: before the gating was
        split into two independent checks, git_sync_enabled alone (with
        automation.enabled left at its False default) would have hit the
        single early `if not settings.automation.enabled: return` and
        started nothing at all."""
        config_path = _write_config(tmp_path, "\nautomation:\n  git_sync_enabled: true\n")
        monkeypatch.setenv("FUTURES_BOT_CONFIG", str(config_path))

        _maybe_start_automation()

        assert get_git_watcher().status()["running"] is False
        assert get_maintenance_scheduler().status()["running"] is False
        assert get_git_sync_scheduler().status()["running"] is True


class TestBothEnabled:
    def test_starts_all_three(self, tmp_path, monkeypatch):
        config_path = _write_config(tmp_path, "\nautomation:\n  enabled: true\n  git_sync_enabled: true\n")
        monkeypatch.setenv("FUTURES_BOT_CONFIG", str(config_path))

        _maybe_start_automation()

        assert get_git_watcher().status()["running"] is True
        assert get_maintenance_scheduler().status()["running"] is True
        assert get_git_sync_scheduler().status()["running"] is True


class TestBadConfig:
    def test_unparseable_config_starts_nothing_and_does_not_raise(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("not: valid: yaml: [structure", encoding="utf-8")
        monkeypatch.setenv("FUTURES_BOT_CONFIG", str(config_path))

        _maybe_start_automation()  # must not raise

        assert get_git_watcher().status()["running"] is False
        assert get_git_sync_scheduler().status()["running"] is False
