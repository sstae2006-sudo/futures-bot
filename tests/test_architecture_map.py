"""Unit tests for `collaboration.architecture_map` -- SIL Phase 6
Milestone 2's minimal, honestly-scoped subsystem lookup (NOT a real
architecture graph; see that module's docstring).
"""

from __future__ import annotations

from futures_bot.collaboration.architecture_map import affected_subsystems


def test_maps_known_prefixes_to_subsystem_labels():
    result = affected_subsystems(["src/futures_bot/risk/manager.py", "src/futures_bot/strategy/base.py"])

    assert result == ["Risk Management", "Strategy Engine"]


def test_deduplicates_and_preserves_first_seen_order():
    result = affected_subsystems([
        "src/futures_bot/risk/manager.py", "src/futures_bot/risk/other.py", "src/futures_bot/strategy/base.py",
    ])

    assert result == ["Risk Management", "Strategy Engine"]


def test_unrecognized_path_contributes_nothing():
    assert affected_subsystems(["config.yaml", "README.md"]) == []


def test_empty_input_returns_empty_list():
    assert affected_subsystems([]) == []


def test_mission_control_prefix_wins_over_broader_frontend_prefix():
    """More-specific prefixes must be checked before the broader
    `frontend/src/` one -- confirms the ordering in `_SUBSYSTEM_PREFIXES`
    hasn't regressed."""
    result = affected_subsystems(["frontend/src/components/mission-control/WorkforcePanel.tsx"])

    assert result == ["Frontend -- Mission Control"]


def test_general_frontend_path_falls_back_to_broad_label():
    result = affected_subsystems(["frontend/src/api.ts"])

    assert result == ["Frontend"]


def test_engine_py_exact_file_maps_to_trading_engine():
    assert affected_subsystems(["src/futures_bot/engine.py"]) == ["Trading Engine"]
