# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
import pandas as pd

from fpl_mcp.tracking import summarise


def test_double_gameweeks_and_benchmarks_are_scored_per_gameweek():
    preds = pd.DataFrame(
        [
            # Player 1 has a double gameweek.
            {"player_id": 1, "gameweek": 7, "expected_points": 3.0, "baseline_points": 2.0, "fpl_expected_points": 7.0},
            {"player_id": 1, "gameweek": 7, "expected_points": 4.0, "baseline_points": 2.0, "fpl_expected_points": 7.0},
            {"player_id": 2, "gameweek": 7, "expected_points": 2.0, "baseline_points": None, "fpl_expected_points": 1.0},
            {"player_id": 3, "gameweek": 7, "expected_points": 1.0, "baseline_points": 1.0, "fpl_expected_points": None},
        ]
    )
    actual = pd.DataFrame([{"player_id": 1, "gameweek": 7, "actual": 9}, {"player_id": 2, "gameweek": 7, "actual": 2}])
    out = summarise(preds, actual)
    week = out["by_gameweek"][0]

    assert out["gameweeks_scored"] == 1 and week["players"] == 3
    assert week["mae"]["model"] == 1.0
    assert week["mae"]["baseline"] == 3.0
    assert week["mae"]["fpl"] == 1.5
