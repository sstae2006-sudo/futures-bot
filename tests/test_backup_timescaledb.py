"""Regression coverage for `tools/backup_timescaledb.py`'s pure logic --
DSN-to-pg_dump-argument translation and the marker file. Loaded directly
by file path, same pattern as `test_tools_turtle_import.py`/
`test_migrate_to_timescaledb.py` (`tools/` isn't on pytest's pythonpath).

Does not require a real `pg_dump` binary on PATH -- that's exercised only
by an operator, on the actual deployment server (see TEAM_DEPLOYMENT.md).
`main()`'s own graceful failure when `pg_dump` is missing is covered here
too (confirmed manually against the live compose instance during this
session: exit code 1, a clear stderr message, no traceback).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"


def _load():
    spec = importlib.util.spec_from_file_location("backup_timescaledb", TOOLS_DIR / "backup_timescaledb.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["backup_timescaledb"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def backup():
    return _load()


class TestPgDumpArgsFromUrl:
    def test_translates_psycopg_dsn_to_pg_dump_flags(self, backup):
        args, password = backup._pg_dump_args_from_url(
            "postgresql+psycopg://futures_bot:secret123@100.64.0.1:5432/futures_bot"
        )
        assert args == ["--host", "100.64.0.1", "--port", "5432", "--username", "futures_bot", "futures_bot"]
        assert password == "secret123"

    def test_password_never_appears_in_the_returned_args_list(self, backup):
        args, _password = backup._pg_dump_args_from_url(
            "postgresql+psycopg://futures_bot:secret123@127.0.0.1:5432/futures_bot"
        )
        assert not any("secret123" in arg for arg in args)

    def test_missing_database_name_raises(self, backup):
        with pytest.raises(ValueError):
            backup._pg_dump_args_from_url("postgresql+psycopg://user:pass@127.0.0.1:5432/")

    def test_non_postgres_scheme_raises(self, backup):
        with pytest.raises(ValueError):
            backup._pg_dump_args_from_url("mysql://user:pass@127.0.0.1:3306/db")

    def test_no_password_in_url_returns_none(self, backup):
        _args, password = backup._pg_dump_args_from_url("postgresql+psycopg://futures_bot@127.0.0.1:5432/futures_bot")
        assert password is None


class TestWriteMarker:
    def test_marker_contains_timestamp_path_and_size(self, backup, tmp_path):
        dump_path = tmp_path / "futures_bot_20260727T000000Z.pgdump"
        dump_path.write_bytes(b"fake dump contents")

        backup._write_marker(tmp_path, dump_path)

        marker = json.loads((tmp_path / backup.MARKER_FILENAME).read_text())
        assert marker["path"] == str(dump_path)
        assert marker["size_bytes"] == len(b"fake dump contents")
        assert "timestamp" in marker


class TestMainWithoutDatabaseUrl:
    def test_exits_1_with_clear_message(self, backup, monkeypatch, capsys):
        monkeypatch.delenv("FUTURES_BOT_DATABASE_URL", raising=False)
        exit_code = backup.main([])
        assert exit_code == 1
        assert "FUTURES_BOT_DATABASE_URL" in capsys.readouterr().err


class TestMainWithoutPgDump:
    def test_exits_1_gracefully_when_pg_dump_missing(self, backup, monkeypatch, capsys):
        monkeypatch.setenv("FUTURES_BOT_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:5432/db")
        monkeypatch.setattr(backup.shutil, "which", lambda _name: None)
        exit_code = backup.main([])
        assert exit_code == 1
        assert "pg_dump not found" in capsys.readouterr().err
