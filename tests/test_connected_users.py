"""Unit tests for `ConnectedUsersTracker` -- always against a fresh
instance, never the process-wide `TRACKER` singleton, so this file can't
bleed state into (or pick up state from) the API-level health-route tests
that use the real one.
"""

from __future__ import annotations

from futures_bot.api.connected_users import ConnectedUsersTracker


class TestConnectedUsersTracker:
    def test_starts_at_zero(self):
        assert ConnectedUsersTracker().count() == 0

    def test_recording_one_ip_counts_one(self):
        tracker = ConnectedUsersTracker()
        tracker.record("10.0.0.1")
        assert tracker.count() == 1

    def test_same_ip_recorded_twice_still_counts_once(self):
        tracker = ConnectedUsersTracker()
        tracker.record("10.0.0.1")
        tracker.record("10.0.0.1")
        assert tracker.count() == 1

    def test_distinct_ips_count_separately(self):
        tracker = ConnectedUsersTracker()
        tracker.record("10.0.0.1")
        tracker.record("10.0.0.2")
        assert tracker.count() == 2

    def test_none_ip_is_ignored(self):
        tracker = ConnectedUsersTracker()
        tracker.record(None)
        assert tracker.count() == 0

    def test_stale_ip_outside_window_is_purged(self):
        tracker = ConnectedUsersTracker(window_seconds=0)
        tracker.record("10.0.0.1")
        # window_seconds=0 -- any recorded entry is immediately "stale"
        # the next time count() runs, since the cutoff equals "now".
        import time

        time.sleep(0.01)
        assert tracker.count() == 0

    def test_fresh_ip_within_window_is_kept(self):
        tracker = ConnectedUsersTracker(window_seconds=60)
        tracker.record("10.0.0.1")
        assert tracker.count() == 1
