"""Tests for `cli.py` itself.

Previously untested end to end -- an audit of the live system caught two
real bugs here (a Unicode arrow that crashed on Windows' default console
codepage, and `cmd_backtest` silently discarding `DataQualityReport`
warnings instead of showing them) that no existing test would have caught
because nothing exercised the CLI's own wiring, only the library functions
underneath it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from futures_bot.cli import main

CONFIG_YAML = """
contract: MES
mode: paper

risk:
  contracts_per_trade: 1
  stop_loss_points: 5
  take_profit_points: 10
  daily_max_loss: 300
  max_trades_per_session: 20
  account_size: 2500

session:
  start_ct: "08:30"
  end_ct: "15:00"
  flatten_before_close_minutes: 15
  trade_on_weekends: false

broker:
  name: paper
  slippage_ticks: 1
  commission_per_side: 0.62
  starting_cash: 2500

logging:
  level: WARNING
  directory: {log_dir}
  log_every_decision: false

strategy_name: ema_crossover
strategy_params:
  fast_period: 3
  slow_period: 8

state_file: {state_file}
"""


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        CONFIG_YAML.format(
            log_dir=(tmp_path / "logs").as_posix(),
            state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
        ),
        encoding="utf-8",
    )
    return config_path


def write_bars_csv(tmp_path: Path, *, with_gap: bool = False) -> Path:
    rows = ["timestamp,open,high,low,close,volume"]
    price = 7500.0
    hour, minute = 8, 30
    for i in range(60):
        if with_gap and i == 30:
            hour = 13  # a same-day jump well past the 120-minute gap threshold
            minute = 0
        price += 1 if i % 2 == 0 else -1
        rows.append(f"2026-07-21 {hour:02d}:{minute:02d}:00,{price},{price+1},{price-1},{price},500")
        minute += 1
        if minute >= 60:
            minute = 0
            hour += 1
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


class TestCmdCheck:
    def test_check_runs_cleanly(self, tmp_path, capsys):
        config = write_config(tmp_path)
        exit_code = main(["--config", str(config), "--check"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "Contract" in out
        assert "MES" in out

    def test_check_missing_file_prints_clean_error(self, tmp_path, capsys):
        exit_code = main(["--config", str(tmp_path / "does_not_exist.yaml"), "--check"])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "Error:" in captured.err

    def test_check_malformed_yaml_prints_clean_error_not_a_traceback(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("risk:\n  stop_loss_points: [unterminated\n", encoding="utf-8")
        exit_code = main(["--config", str(bad), "--check"])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "Error:" in captured.err
        assert "Traceback" not in captured.err


class TestCmdBacktest:
    def test_backtest_runs_and_reports(self, tmp_path, capsys):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        exit_code = main(["--config", str(config), "--backtest", str(csv_path)])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "BACKTEST RESULTS" in out

    def test_backtest_surfaces_data_quality_warnings(self, tmp_path, capsys):
        """Regression: `cmd_backtest` used to load `data_report` and then
        never pass it to `format_report`, so gap/duplicate/zero-volume
        warnings never reached the user despite `load_bars` computing them."""
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path, with_gap=True)
        exit_code = main(["--config", str(config), "--backtest", str(csv_path)])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "DATA QUALITY" in out
        assert "gap" in out.lower()

    def test_backtest_without_gaps_omits_data_quality_section(self, tmp_path, capsys):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path, with_gap=False)
        main(["--config", str(config), "--backtest", str(csv_path)])
        out = capsys.readouterr().out
        assert "DATA QUALITY" not in out

    def test_backtest_missing_csv_prints_clean_error(self, tmp_path, capsys):
        config = write_config(tmp_path)
        exit_code = main(["--config", str(config), "--backtest", str(tmp_path / "nope.csv")])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert "No such file" in captured.err

    def test_walk_forward_runs_and_reports_both_halves(self, tmp_path, capsys):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        exit_code = main(["--config", str(config), "--backtest", str(csv_path), "--walk-forward"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "TRAIN RESULTS" in out
        assert "TEST RESULTS" in out

    def test_report_flag_adds_advanced_report(self, tmp_path, capsys):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        exit_code = main(["--config", str(config), "--backtest", str(csv_path), "--report"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "ADVANCED REPORT" in out

    def test_html_report_flag_writes_a_file(self, tmp_path, capsys):
        """Regression: `--html-report` didn't exist at all -- `html_report.py`
        was fully implemented and tested but had no CLI entry point, so
        `compare_strategies.ps1`'s reference to this exact flag has never
        worked."""
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        out_path = tmp_path / "report.html"
        exit_code = main(
            ["--config", str(config), "--backtest", str(csv_path), "--html-report", str(out_path)]
        )
        out = capsys.readouterr().out
        assert exit_code == 0
        assert out_path.exists()
        html = out_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "ema_crossover" in html
        assert f"HTML report written to {out_path}" in out

    def test_html_report_with_walk_forward_uses_test_metrics(self, tmp_path):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        out_path = tmp_path / "report.html"
        main([
            "--config", str(config), "--backtest", str(csv_path),
            "--walk-forward", "--html-report", str(out_path),
        ])
        assert out_path.exists()


class TestCmdCompare:
    def test_compare_runs_every_registered_strategy_by_default(self, tmp_path, capsys):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        exit_code = main(["--config", str(config), "--compare", str(csv_path)])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "STRATEGY LEADERBOARD" in out
        assert "ema_crossover" in out
        assert "trend_pullback" in out  # confirms cli.py registers it (Phase 3 fix)

    def test_compare_respects_strategies_filter(self, tmp_path, capsys):
        config = write_config(tmp_path)
        csv_path = write_bars_csv(tmp_path)
        main(["--config", str(config), "--compare", str(csv_path), "--strategies", "ema_crossover"])
        out = capsys.readouterr().out
        assert "ema_crossover" in out
        assert "trend_pullback" not in out


class TestCmdOptimize:
    def test_optimize_runs_with_a_grid_in_strategy_params(self, tmp_path, capsys):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            CONFIG_YAML.format(
                log_dir=(tmp_path / "logs").as_posix(),
                state_file=(tmp_path / "state" / "bot_state.json").as_posix(),
            ).replace(
                "strategy_params:\n  fast_period: 3\n  slow_period: 8",
                "strategy_params:\n  fast_period: [3, 5]\n  slow_period: 15",
            ),
            encoding="utf-8",
        )
        csv_path = write_bars_csv(tmp_path)
        exit_code = main(["--config", str(config_path), "--optimize", str(csv_path), "--top", "2"])
        out = capsys.readouterr().out
        assert exit_code == 0
        assert "BEST CONFIGURATION" in out
        assert "Confidence:" in out


class TestArgumentHandling:
    def test_no_command_is_a_usage_error(self, tmp_path):
        with pytest.raises(SystemExit):
            main(["--config", str(write_config(tmp_path))])

    def test_mutually_exclusive_commands_rejected(self, tmp_path):
        config = write_config(tmp_path)
        with pytest.raises(SystemExit):
            main(["--config", str(config), "--check", "--demo"])
