"""Score archived predictions against what actually happened, gameweek by gameweek.

Only the prediction made for the very next gameweek is scored (the last one saved before that
gameweek's deadline), alongside two benchmarks recorded at the same moment: the player's average
over their last 6 matches, and FPL's own expected points (ep_next).
"""

import numpy as np
import pandas as pd
from sqlalchemy import and_, func, select

from .db import gameweeks, get_engine, player_gameweeks, prediction_history

TOP_N = 20


def _mae(actual: pd.Series, pred: pd.Series) -> float | None:
    mask = pred.notna()
    return round(float((pred[mask] - actual[mask]).abs().mean()), 3) if mask.any() else None


def _corr(actual: pd.Series, pred: pd.Series) -> float | None:
    mask = pred.notna()
    if mask.sum() < 3 or pred[mask].std() == 0:
        return None
    return round(float(np.corrcoef(pred[mask], actual[mask])[0, 1]), 3)


def _top_n_actual(frame: pd.DataFrame, column: str) -> float | None:
    ranked = frame[frame[column].notna()].nlargest(TOP_N, column)
    return round(float(ranked["actual"].mean()), 2) if len(ranked) else None


def live_accuracy(season: str) -> dict:
    pg, ph = player_gameweeks.c, prediction_history.c
    with get_engine().connect() as conn:
        finished = [r[0] for r in conn.execute(select(gameweeks.c.id).where(gameweeks.c.finished.is_(True)))]
        preds = pd.read_sql(
            select(ph.player_id, ph.gameweek, ph.expected_points, ph.baseline_points, ph.fpl_expected_points).where(
                and_(ph.season == season, ph.gameweek == ph.first_gameweek, ph.gameweek.in_(finished or [-1]))
            ),
            conn,
        )
        if preds.empty:
            return {
                "gameweeks_scored": 0,
                "note": "No finished gameweeks with archived predictions yet. Scoring starts after the "
                "first gameweek predicted by this version finishes.",
            }
        actual = pd.read_sql(
            select(pg.element.label("player_id"), pg["round"].label("gameweek"), func.sum(pg.total_points).label("actual"))
            .where(and_(pg.season == season, pg["round"].in_(sorted(preds["gameweek"].unique().tolist()))))
            .group_by(pg.element, pg["round"]),
            conn,
        )
    return summarise(preds, actual)


def summarise(preds: pd.DataFrame, actual: pd.DataFrame) -> dict:
    """preds: one row per player per fixture; actual: points per player per gameweek."""
    # One row per player per gameweek (double gameweeks have two fixtures).
    per_gw = (
        preds.groupby(["player_id", "gameweek"])
        .agg(
            model=("expected_points", "sum"),
            baseline=("baseline_points", lambda s: s.sum(min_count=1)),  # stays missing if never known
            fpl=("fpl_expected_points", "max"),  # FPL's figure already covers the whole gameweek
        )
        .reset_index()
        .merge(actual, on=["player_id", "gameweek"], how="left")
    )
    per_gw["actual"] = per_gw["actual"].fillna(0)  # no record = didn't play

    weeks = []
    for gw, frame in per_gw.groupby("gameweek"):
        weeks.append(
            {
                "gameweek": int(gw),
                "players": int(len(frame)),
                "mae": {"model": _mae(frame["actual"], frame["model"]), "baseline": _mae(frame["actual"], frame["baseline"]), "fpl": _mae(frame["actual"], frame["fpl"])},
                "corr": {"model": _corr(frame["actual"], frame["model"]), "fpl": _corr(frame["actual"], frame["fpl"])},
                f"avg_points_of_top_{TOP_N}": {"model": _top_n_actual(frame, "model"), "fpl": _top_n_actual(frame, "fpl")},
            }
        )

    def overall(key: str, who: str) -> float | None:
        values = [w[key][who] for w in weeks if w[key][who] is not None]
        return round(float(np.mean(values)), 3) if values else None

    top_key = f"avg_points_of_top_{TOP_N}"
    return {
        "gameweeks_scored": len(weeks),
        "overall": {
            "mae": {who: overall("mae", who) for who in ("model", "baseline", "fpl")},
            top_key: {who: overall(top_key, who) for who in ("model", "fpl")},
        },
        "by_gameweek": weeks,
        "how_to_read": (
            "MAE = average miss in points per player (lower is better). "
            f"{top_key} = what the {TOP_N} players each method rated highest actually scored (higher is better). "
            "'fpl' is FPL's own expected points at the time of the prediction."
        ),
    }
