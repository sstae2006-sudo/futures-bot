from datetime import datetime, timezone
from decimal import Decimal

from futures_bot.models import Bar
from futures_bot.provenance import bars_dataset_version, git_commit


def _bar(day: int) -> Bar:
    return Bar(
        timestamp=datetime(2025, 1, day, tzinfo=timezone.utc),
        open=Decimal("1"), high=Decimal("1"), low=Decimal("1"), close=Decimal("1"),
        volume=1,
    )


class TestGitCommit:
    def test_returns_a_40_character_hash_in_a_real_checkout(self):
        commit = git_commit()
        assert commit is None or len(commit) == 40


class TestBarsDatasetVersion:
    def test_is_deterministic_for_the_same_inputs(self):
        bars = [_bar(1), _bar(2), _bar(3)]
        assert bars_dataset_version("MES", "5min", bars) == bars_dataset_version("MES", "5min", bars)

    def test_differs_when_the_bar_count_differs(self):
        bars = [_bar(1), _bar(2), _bar(3)]
        assert bars_dataset_version("MES", "5min", bars) != bars_dataset_version("MES", "5min", bars[:2])

    def test_differs_when_the_product_code_differs(self):
        bars = [_bar(1), _bar(2)]
        assert bars_dataset_version("MES", "5min", bars) != bars_dataset_version("MNQ", "5min", bars)

    def test_differs_when_the_resolution_differs(self):
        bars = [_bar(1), _bar(2)]
        assert bars_dataset_version("MES", "5min", bars) != bars_dataset_version("MES", "1min", bars)

    def test_handles_an_empty_bar_list(self):
        assert bars_dataset_version("MES", "5min", []) == bars_dataset_version("MES", "5min", [])
        assert bars_dataset_version("MES", "5min", []) != bars_dataset_version("MNQ", "5min", [])

    def test_is_a_short_hex_fingerprint(self):
        fingerprint = bars_dataset_version("MES", "5min", [_bar(1)])
        assert len(fingerprint) == 16
        int(fingerprint, 16)  # raises ValueError if not valid hex
