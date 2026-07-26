"""State persistence tests, including the Windows write race.

The original implementation used a fixed temp filename and a single
``os.replace``. That works on Linux, where rename is unconditional, and fails
intermittently on Windows, where a scanner holding the file for a few
milliseconds turns the rename into ``PermissionError``.

Since the failure is platform-specific, the retry behaviour is tested by
simulating the error rather than by hoping CI runs on Windows.
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import pytest

from futures_bot import state as state_module
from futures_bot.state import StateStore


DAY = date(2026, 7, 21)


def test_rapid_successive_writes(tmp_path):
    """Recording a trade then halting writes several times in a row."""
    store = StateStore(tmp_path / "state.json")
    for i in range(25):
        store.record_pnl(DAY, Decimal("-1"))
        store.halt(DAY, f"halt {i}")
        store.clear_halt(DAY)

    reloaded = StateStore(tmp_path / "state.json")
    assert reloaded.state.current.realized_pnl == Decimal("-25")
    assert reloaded.state.current.trade_count == 25


def test_no_temp_files_left_behind(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.record_pnl(DAY, Decimal("-10"))
    store.halt(DAY, "done")

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"temp files leaked: {leftovers}"


def test_retries_transient_permission_error(tmp_path, monkeypatch):
    """A scanner holding the file briefly must not lose the write."""
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:  # fail the first two attempts
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    store = StateStore(tmp_path / "state.json")
    store.session(DAY)  # create the session before patching

    monkeypatch.setattr(state_module.os, "replace", flaky_replace)
    monkeypatch.setattr(state_module, "_WRITE_BACKOFF_SECONDS", 0.001)

    store.state.current.realized_pnl = Decimal("-42")
    store._write()

    assert calls["n"] == 3, "should have retried twice before succeeding"
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["current"]["realized_pnl"] == "-42"


def test_persistent_permission_error_raises_clearly(tmp_path, monkeypatch):
    """If it truly cannot persist, say so — do not silently drop the halt."""

    def always_denied(src, dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(state_module.os, "replace", always_denied)
    monkeypatch.setattr(state_module, "_WRITE_BACKOFF_SECONDS", 0.001)

    store = StateStore.__new__(StateStore)
    store.path = tmp_path / "state.json"
    store.state = state_module.BotState()

    with pytest.raises(RuntimeError, match="Could not write state"):
        store._write()

    assert list(tmp_path.glob("*.tmp")) == [], "failed attempts must clean up"


def test_each_attempt_uses_a_distinct_temp_name(tmp_path, monkeypatch):
    """A shared temp name is what turns a transient conflict into a failure."""
    seen: list[str] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        seen.append(os.path.basename(src))
        if len(seen) <= 2:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(state_module.os, "replace", recording_replace)
    monkeypatch.setattr(state_module, "_WRITE_BACKOFF_SECONDS", 0.001)

    store = StateStore(tmp_path / "state.json")
    store.record_pnl(DAY, Decimal("-5"))

    assert len(seen) == len(set(seen)), f"temp names were reused: {seen}"


def test_survives_restart_after_write_contention(tmp_path, monkeypatch):
    """The kill switch must still be remembered when writes had to retry."""
    real_replace = os.replace
    n = {"count": 0}

    def occasionally_denied(src, dst):
        n["count"] += 1
        if n["count"] % 3 == 0:
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(state_module.os, "replace", occasionally_denied)
    monkeypatch.setattr(state_module, "_WRITE_BACKOFF_SECONDS", 0.001)

    path = tmp_path / "state.json"
    store = StateStore(path)
    store.record_pnl(DAY, Decimal("-130"))
    store.halt(DAY, "Daily loss limit reached.")

    monkeypatch.setattr(state_module.os, "replace", real_replace)
    revived = StateStore(path)
    assert revived.state.current.halted is True
    assert revived.state.current.realized_pnl == Decimal("-130")
