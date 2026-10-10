import pandas as pd

from fpl_mcp.tracking import summarise


def test_double_gameweeks_and_benchmarks_are_scored_per_gameweek():
    preds = pd.DataFrame(
        [
            # Player 1 has a double gameweek: model and baseline add up, FPL's figure is per gameweek.
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
    # Model: |7-9| + |2-2| + |1-0| over 3 players. Player 3 has no record, so scored 0.
    assert week["mae"]["model"] == 1.0
    # Baseline: player 2 unknown, so only players 1 (|4-9|) and 3 (|1-0|) count.
    assert week["mae"]["baseline"] == 3.0
    # FPL: player 3 unknown; |7-9| and |1-2|.
    assert week["mae"]["fpl"] == 1.5
