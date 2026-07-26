"""Tests for `api.__main__`'s network-exposure guard -- refusing to bind a
non-loopback host without explicit opt-in, since the API has no
authentication in front of it (see `api/app.py`'s module docstring)."""

from __future__ import annotations

import pytest

from futures_bot.api.__main__ import _is_loopback, main


class TestIsLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "LOCALHOST"])
    def test_loopback_hosts(self, host):
        assert _is_loopback(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50", "10.0.0.1", "example.com"])
    def test_non_loopback_hosts(self, host):
        assert not _is_loopback(host)


class TestMainNetworkExposureGuard:
    def test_default_loopback_host_starts_normally(self, monkeypatch):
        calls = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setattr("sys.argv", ["futures_bot.api"])

        assert main() == 0
        assert len(calls) == 1
        assert calls[0][1]["host"] == "127.0.0.1"

    def test_non_loopback_host_without_opt_in_is_refused(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setattr("sys.argv", ["futures_bot.api", "--host", "0.0.0.0"])

        exit_code = main()

        assert exit_code == 2
        assert calls == []  # uvicorn must never have been started
        assert "--allow-network-exposure" in capsys.readouterr().err

    def test_non_loopback_host_with_opt_in_starts(self, monkeypatch):
        calls = []
        monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
        monkeypatch.setattr("sys.argv", ["futures_bot.api", "--host", "0.0.0.0", "--allow-network-exposure"])

        assert main() == 0
        assert len(calls) == 1
        assert calls[0][1]["host"] == "0.0.0.0"
