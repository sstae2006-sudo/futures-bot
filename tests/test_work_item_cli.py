"""Regression coverage for `tools/work_item_cli.py` -- loaded directly by
file path, same pattern `test_backup_timescaledb.py` already established
(`tools/` isn't on pytest's pythonpath). Exercises the full create/list/
claim/release/complete/status/check lifecycle end-to-end against a
throwaway SQLite database (`FUTURES_BOT_RESEARCH_DB`), never the real
project database.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"


def _load():
    spec = importlib.util.spec_from_file_location("work_item_cli", TOOLS_DIR / "work_item_cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["work_item_cli"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("FUTURES_BOT_RESEARCH_DB", str(tmp_path / "cli_test.db"))
    monkeypatch.setenv("FUTURES_BOT_MARKET_DATA_DB", str(tmp_path / "market_data_test.db"))
    return _load()


class TestCreateAndList:
    def test_create_then_list(self, cli, capsys):
        assert cli.main(["create", "--title", "Task", "--files", "a.py", "b.py"]) == 0
        created = capsys.readouterr().out

        assert cli.main(["list"]) == 0
        listed = capsys.readouterr().out

        assert "Task" in created
        assert "Task" in listed

    def test_create_with_ai_owner_type(self, cli, capsys):
        assert cli.main(["create", "--title", "AI task", "--owner-type", "ai"]) == 0
        out = capsys.readouterr().out
        assert '"owner_type": "ai"' in out


class TestLifecycle:
    def _create_id(self, cli, capsys, **kwargs) -> str:
        args = ["create", "--title", kwargs.pop("title", "Task")]
        for k, v in kwargs.items():
            args += [f"--{k.replace('_', '-')}", v]
        cli.main(args)
        import json
        return json.loads(capsys.readouterr().out)["work_item"]["id"]

    def test_claim_release_complete(self, cli, capsys):
        item_id = self._create_id(cli, capsys)

        assert cli.main(["claim", item_id, "--user", "alice"]) == 0
        assert '"status": "claimed"' in capsys.readouterr().out

        assert cli.main(["release", item_id]) == 0
        assert '"status": "open"' in capsys.readouterr().out

        cli.main(["claim", item_id, "--user", "alice"])
        capsys.readouterr()
        assert cli.main(["complete", item_id]) == 0
        assert '"status": "completed"' in capsys.readouterr().out

    def test_status_transition(self, cli, capsys):
        item_id = self._create_id(cli, capsys)

        assert cli.main(["status", item_id, "in_progress"]) == 0
        assert '"status": "in_progress"' in capsys.readouterr().out

    def test_unknown_item_returns_nonzero_not_a_traceback(self, cli, capsys):
        exit_code = cli.main(["claim", "does-not-exist", "--user", "alice"])
        assert exit_code == 1
        assert "Error" in capsys.readouterr().err


class TestCheck:
    def test_no_overlap_returns_zero(self, cli, capsys):
        assert cli.main(["check", "--files", "brand_new.py"]) == 0
        out = capsys.readouterr().out
        assert '"suggested_action": "proceed"' in out

    def test_critical_overlap_returns_nonzero(self, cli, capsys):
        cli.main(["create", "--title", "Existing", "--files", "a.py", "b.py", "c.py", "d.py", "e.py"])
        capsys.readouterr()

        exit_code = cli.main(["check", "--files", "a.py", "b.py", "c.py", "d.py", "e.py"])

        assert exit_code == 1
        err = capsys.readouterr().err
        assert "Recommendation" in err
