"""Expected-points model: features, walk-forward evaluation, training and predictions.

    fpl-model evaluate   walk-forward test on the last complete seasons (nothing saved)
    fpl-model train      evaluate, then train on all data; kept only if it beats the baseline
                         and the previous model
    fpl-model predict    write expected points for the next gameweeks to the predictions table
    fpl-model all        train + predict

The model predicts points per fixture; a gameweek's expected points is the sum over that
gameweek's fixtures (so double gameweeks count twice).
"""

import argparse
import json
import logging
import time
from datetime import datetime, timezone

import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy import delete, select

from .config import PROJECT_ROOT
from .db import (
    fixtures,
    gameweeks,
    get_engine,
    model_runs,
    player_gameweeks,
    players,
    predictions,
    season_teams,
    teams,
    upsert,
)

log = logging.getLogger("fpl_mcp.model")

CACHE_DIR = PROJECT_ROOT / "data" / "cache"
MODEL_DIR = PROJECT_ROOT / "models"

TARGET = "total_points"
STATS = [
    "total_points", "minutes", "starts", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "saves", "bonus", "bps", "influence", "creativity", "threat",
    "ict_index", "expected_goals", "expected_assists", "expected_goal_involvements",
    "expected_goals_conceded", "defensive_contribution",
]
WINDOWS = (3, 6, 12)
TEAM_STATS = ["gf", "ga", "xg", "xga"]
TEAM_WINDOW = 6
POSITION_CODES = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
HORIZON = 5  # gameweeks ahead to predict
EVAL_SEASONS = 3

PLAYER_FEATURES = [f"{s}_r{w}" for w in WINDOWS for s in STATS + ["played60"]]
FEATURES = (
    PLAYER_FEATURES
    + ["n_prev", "season_pts_mean", "days_since_last", "value", "was_home", "position_code"]
    + [f"team_{s}_r{TEAM_WINDOW}" for s in TEAM_STATS]
    + [f"opp_{s}_r{TEAM_WINDOW}" for s in TEAM_STATS]
)

LGB_PARAMS = dict(
    objective="regression",  # squared error -> predicts the mean, i.e. expected points
    learning_rate=0.03,
    num_leaves=31,
    min_data_in_leaf=100,
    bagging_fraction=0.8,
    bagging_freq=1,
    feature_fraction=0.8,
    lambda_l2=1.0,
    seed=7,
    verbose=-1,
)
NUM_ROUNDS = 500


# --- Loading ----------------------------------------------------------------------


def current_season() -> str:
    from .queries import snapshot

    season = snapshot()["season"]
    if not season:
        raise RuntimeError("No gameweeks loaded; run `fpl-sync live` first.")
    return season


def _read_season(engine, season: str) -> pd.DataFrame:
    for attempt in range(3):
        try:
            with engine.connect() as conn:
                return pd.read_sql(select(player_gameweeks).where(player_gameweeks.c.season == season), conn)
        except Exception as exc:  # long reads over the Supabase pooler occasionally drop
            if attempt == 2:
                raise
            log.warning("Reading %s failed (%s), retrying", season, exc.__class__.__name__)
            time.sleep(5)


def load_history(season_now: str, refresh_cache: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """All player-fixture rows plus season team names.

    Past seasons never change, so they are cached locally in data/cache (git-ignored);
    only the current season is read from the database each time.
    """
    engine = get_engine()
    with engine.connect() as conn:
        seasons = sorted(r[0] for r in conn.execute(select(player_gameweeks.c.season).distinct()))
        names = pd.read_sql(select(season_teams), conn)

    frames = []
    for season in seasons:
        cache = CACHE_DIR / f"player_gameweeks_{season}.pkl"
        if season != season_now and cache.exists() and not refresh_cache:
            frames.append(pd.read_pickle(cache))
            continue
        log.info("Downloading %s", season)
        df = _read_season(engine, season)
        if season != season_now:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            df.to_pickle(cache)
        frames.append(df)
    return pd.concat(frames, ignore_index=True), names


def prepare(df: pd.DataFrame, names: pd.DataFrame) -> pd.DataFrame:
    df = df[df["position"].isin(POSITION_CODES) & df["player_code"].notna() & df["kickoff_time"].notna()].copy()
    df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], utc=True)
    df = df.merge(names, on=["season", "team_id"], how="left")
    df = df.merge(
        names.rename(columns={"team_id": "opponent_team", "team_name": "opp_name"}),
        on=["season", "opponent_team"],
        how="left",
    )
    df["pid"] = df["player_code"].astype(int)
    df["was_home"] = df["was_home"].astype(float)
    df["position_code"] = df["position"].map(POSITION_CODES)
    return df


# --- Features ----------------------------------------------------------------------


def add_player_features(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling form from each player's previous fixtures (never the current one)."""
    df = df.sort_values(["pid", "kickoff_time", "fixture"]).reset_index(drop=True)
    g = df.groupby("pid", sort=False)

    lagged = g[STATS].shift(1)
    prev_minutes = g["minutes"].shift(1)
    lagged["played60"] = prev_minutes.ge(60).astype(float).where(prev_minutes.notna())
    lagged["pid"] = df["pid"]
    cols = STATS + ["played60"]
    for w in WINDOWS:
        roll = lagged.groupby("pid", sort=False)[cols].rolling(w, min_periods=1).mean()
        roll = roll.reset_index(level=0, drop=True).sort_index()
        df[[f"{c}_r{w}" for c in cols]] = roll[cols].to_numpy()

    df["prev_kickoff"] = g["kickoff_time"].shift(1)
    df["n_prev"] = g.cumcount()
    days = (df["kickoff_time"] - df["prev_kickoff"]).dt.total_seconds() / 86400
    df["days_since_last"] = days.clip(0, 120)

    # Points per match earlier in the same season.
    pts = df[TARGET].fillna(0)
    by_season = [df["pid"], df["season"]]
    before = pts.groupby(by_season).cumsum() - pts
    count = df.groupby(by_season).cumcount()
    df["season_pts_mean"] = (before / count).where(count > 0)
    return df


def team_fixture_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per team per fixture: goals and xG for and against."""
    keys = ["season", "fixture", "team_name"]
    t = df.groupby(keys, sort=False).agg(
        kickoff_time=("kickoff_time", "first"),
        was_home=("was_home", "first"),
        team_h_score=("team_h_score", "first"),
        team_a_score=("team_a_score", "first"),
        xg_sum=("expected_goals", "sum"),
        xg_n=("expected_goals", "count"),
        n_players=("kickoff_time", "size"),
    ).reset_index()
    t = t[t["team_name"].notna()]
    # Each side of a fixture is one club; if mislabelled players add extra "clubs", keep the majority.
    t = t.sort_values("n_players", ascending=False).drop_duplicates(["season", "fixture", "was_home"])
    home = t["was_home"] == 1
    t["gf"] = np.where(home, t["team_h_score"], t["team_a_score"])
    t["ga"] = np.where(home, t["team_a_score"], t["team_h_score"])
    t["xg"] = t["xg_sum"].where(t["xg_n"] > 0)  # xG only exists from 2022-23
    opp = t[["season", "fixture", "team_name", "xg"]].rename(columns={"team_name": "other", "xg": "xga"})
    t = t.merge(opp, on=["season", "fixture"])
    t = t[t["team_name"] != t["other"]]
    return t[keys + ["kickoff_time"] + TEAM_STATS].reset_index(drop=True)


def add_team_features(t: pd.DataFrame) -> pd.DataFrame:
    """Rolling team strength from previous fixtures, linked across seasons by team name."""
    t = t.sort_values(["team_name", "kickoff_time"]).reset_index(drop=True)
    lagged = t.groupby("team_name", sort=False)[TEAM_STATS].shift(1)
    lagged["team_name"] = t["team_name"]
    roll = lagged.groupby("team_name", sort=False)[TEAM_STATS].rolling(TEAM_WINDOW, min_periods=1).mean()
    roll = roll.reset_index(level=0, drop=True).sort_index()
    for s in TEAM_STATS:
        t[f"r_{s}"] = roll[s].to_numpy()
    return t


def _attach_team(df: pd.DataFrame, tf: pd.DataFrame, name_col: str, prefix: str) -> pd.DataFrame:
    cols = {f"r_{s}": f"{prefix}_{s}_r{TEAM_WINDOW}" for s in TEAM_STATS}
    right = tf[["season", "fixture", "team_name", *cols]].rename(columns={**cols, "team_name": name_col})
    return df.merge(right, on=["season", "fixture", name_col], how="left")


def build_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    feat = add_player_features(df)
    tf = add_team_features(team_fixture_table(df))
    feat = _attach_team(feat, tf, "team_name", "team")
    feat = _attach_team(feat, tf, "opp_name", "opp")
    return feat[feat[TARGET].notna()]


# --- Evaluation and training -------------------------------------------------------------


def _fit(train: pd.DataFrame) -> lgb.Booster:
    return lgb.train(LGB_PARAMS, lgb.Dataset(train[FEATURES], train[TARGET]), num_boost_round=NUM_ROUNDS)


def _scores(y: pd.Series, pred: np.ndarray) -> dict:
    err = pred - y.to_numpy()
    return {
        "mae": round(float(np.abs(err).mean()), 4),
        "rmse": round(float(np.sqrt((err**2).mean())), 4),
        "corr": round(float(np.corrcoef(pred, y)[0, 1]), 4),
    }


def evaluate(feat: pd.DataFrame, test_seasons: list[str]) -> dict:
    """Train on seasons before S, test on S, for each S. Baseline = average points over the last 6 fixtures."""
    out = {}
    for season in test_seasons:
        train, test = feat[feat["season"] < season], feat[feat["season"] == season]
        model = _fit(train)
        pred = model.predict(test[FEATURES])
        baseline = test["total_points_r6"].fillna(0).to_numpy()
        regulars = (test["minutes_r3"] >= 60).to_numpy()  # players who have been starting
        out[season] = {
            "rows": int(len(test)),
            "model": _scores(test[TARGET], pred),
            "baseline": _scores(test[TARGET], baseline),
            "regulars_rows": int(regulars.sum()),
            "regulars_model": _scores(test[TARGET][regulars], pred[regulars]),
            "regulars_baseline": _scores(test[TARGET][regulars], baseline[regulars]),
        }
        log.info("%s  model MAE %.3f | baseline MAE %.3f", season, out[season]["model"]["mae"], out[season]["baseline"]["mae"])
    return out


def _evaluation_seasons(feat: pd.DataFrame, season_now: str) -> list[str]:
    complete = sorted(s for s in feat["season"].unique() if s < season_now)
    return complete[-EVAL_SEASONS:]


def _latest_accepted() -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(model_runs).where(model_runs.c.accepted.is_(True)).order_by(model_runs.c.trained_at.desc()).limit(1)
        ).mappings().first()
    return dict(row) if row else None


def train(refresh_cache: bool = False) -> dict:
    season_now = current_season()
    df, names = load_history(season_now, refresh_cache)
    feat = build_training_frame(prepare(df, names))
    test_seasons = _evaluation_seasons(feat, season_now)
    metrics = evaluate(feat, test_seasons)

    mae = float(np.mean([m["model"]["mae"] for m in metrics.values()]))
    baseline_mae = float(np.mean([m["baseline"]["mae"] for m in metrics.values()]))
    previous = _latest_accepted()
    note = []
    accepted = mae < baseline_mae
    if not accepted:
        note.append("did not beat the rolling-average baseline")
    if previous and accepted:
        prev_seasons = sorted(json.loads(previous["metrics"]))
        if prev_seasons == test_seasons and mae > previous["mae"] + 1e-4:
            accepted = False
            note.append(f"worse than the current model ({mae:.4f} vs {previous['mae']:.4f})")

    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if accepted:
        model = _fit(feat)  # final model uses every season, including this one so far
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model.save_model(str(MODEL_DIR / f"lgbm-{version}.txt"))
        note.append(f"trained on {len(feat)} fixtures")

    with get_engine().begin() as conn:
        conn.execute(
            model_runs.insert().values(
                version=version,
                trained_at=datetime.now(timezone.utc),
                accepted=accepted,
                mae=mae,
                baseline_mae=baseline_mae,
                metrics=json.dumps(metrics),
                note="; ".join(note),
            )
        )
    return {"version": version, "accepted": accepted, "mae": round(mae, 4), "baseline_mae": round(baseline_mae, 4), "note": "; ".join(note)}


# --- Predictions -------------------------------------------------------------------------


def availability(status: str | None, chance: float | None, ahead: int) -> float:
    """Multiplier for injury/suspension news. FPL's chance-of-playing only covers the next gameweek."""
    if status in ("u", "n"):  # left the club / not in the squad
        return 0.0
    if chance is not None and not pd.isna(chance):
        base = float(chance) / 100
    elif status in ("i", "s"):
        base = 0.0
    else:
        base = 1.0
    if ahead == 0:
        return base
    if ahead == 1:
        return min(1.0, base + 0.5)
    return 1.0


def predict(horizon: int = HORIZON) -> dict:
    previous = _latest_accepted()
    if not previous:
        raise RuntimeError("No accepted model yet; run `fpl-model train` first.")
    path = MODEL_DIR / f"lgbm-{previous['version']}.txt"
    if not path.exists():
        raise RuntimeError(f"Model file {path} is missing; run `fpl-model train` on this machine.")
    booster = lgb.Booster(model_file=str(path))

    season_now = current_season()
    engine = get_engine()
    with engine.connect() as conn:
        cur_players = pd.read_sql(select(players), conn)
        cur_teams = pd.read_sql(select(teams.c.id, teams.c.name), conn)
        fx = pd.read_sql(select(fixtures), conn)
        gws = pd.read_sql(select(gameweeks), conn)

    next_gw = gws.loc[gws["is_next"] == True, "id"]  # noqa: E712
    if next_gw.empty:
        return {"predictions": 0, "note": "No upcoming gameweek."}
    first_gw = int(next_gw.iloc[0])
    target_gws = list(range(first_gw, first_gw + horizon))

    history, names = load_history(season_now)
    hist = prepare(history, names)
    team_name = dict(zip(cur_teams["id"], cur_teams["name"]))
    now = pd.Timestamp.now(tz="UTC")

    # One placeholder row per current player at "now": its lagged features are the player's
    # form going into the next match, computed by exactly the same code as in training.
    cur = cur_players[cur_players["position"].isin(POSITION_CODES) & cur_players["code"].notna()]
    dummy = pd.DataFrame(
        {
            "pid": cur["code"].astype(int).to_numpy(),
            "season": season_now,
            "fixture": -1,
            "kickoff_time": now,
            "position_code": cur["position"].map(POSITION_CODES).to_numpy(),
        }
    )
    state = add_player_features(pd.concat([hist, dummy], ignore_index=True))
    state = state[state["fixture"] == -1].set_index("pid")

    tf = team_fixture_table(hist)
    team_dummy = pd.DataFrame({"season": season_now, "fixture": -1, "team_name": sorted(team_name.values()), "kickoff_time": now})
    team_state = add_team_features(pd.concat([tf, team_dummy], ignore_index=True))
    team_state = team_state[team_state["fixture"] == -1].set_index("team_name")

    upcoming = fx[fx["event"].isin(target_gws) & (fx["started"] != True)].copy()  # noqa: E712
    upcoming["kickoff_time"] = pd.to_datetime(upcoming["kickoff_time"], utc=True)
    rows = []
    for side, other, home in (("team_h", "team_a", 1.0), ("team_a", "team_h", 0.0)):
        part = upcoming[["id", "event", "kickoff_time", side, other]].rename(
            columns={"id": "fixture_id", "event": "gameweek", side: "team_id", other: "opp_id"}
        )
        part["was_home"] = home
        rows.append(part)
    team_fx = pd.concat(rows, ignore_index=True).sort_values(["team_id", "kickoff_time"])
    team_fx["prev_team_kickoff"] = team_fx.groupby("team_id")["kickoff_time"].shift(1)

    X = cur[["id", "code", "team_id", "price", "status", "chance_of_playing_next_round", "position"]].merge(
        team_fx, on="team_id"
    )
    X["pid"] = X["code"].astype(int)
    X = X.join(state[PLAYER_FEATURES + ["n_prev", "season_pts_mean", "prev_kickoff"]], on="pid")
    last_played = X["prev_team_kickoff"].fillna(X["prev_kickoff"])
    X["days_since_last"] = ((X["kickoff_time"] - last_played).dt.total_seconds() / 86400).clip(0, 120)
    X["value"] = X["price"]
    X["position_code"] = X["position"].map(POSITION_CODES)
    for prefix, id_col in (("team", "team_id"), ("opp", "opp_id")):
        names_ = X[id_col].map(team_name)
        for s in TEAM_STATS:
            X[f"{prefix}_{s}_r{TEAM_WINDOW}"] = names_.map(team_state[f"r_{s}"])

    X["raw"] = booster.predict(X[FEATURES])
    X["ahead"] = X["gameweek"] - first_gw
    X["availability"] = [
        availability(s, c, a) for s, c, a in zip(X["status"], X["chance_of_playing_next_round"], X["ahead"])
    ]
    X["expected"] = X["raw"].clip(lower=0) * X["availability"]

    created = datetime.now(timezone.utc)
    out = [
        {
            "player_id": int(r.id),
            "fixture_id": int(r.fixture_id),
            "gameweek": int(r.gameweek),
            "expected_points": round(float(r.expected), 3),
            "raw_expected_points": round(float(r.raw), 3),
            "availability": round(float(r.availability), 2),
            "model_version": previous["version"],
            "created_at": created,
        }
        for r in X.itertuples(index=False)
    ]
    with engine.begin() as conn:
        conn.execute(delete(predictions))
    upsert(engine, predictions, out)
    return {"model_version": previous["version"], "gameweeks": target_gws, "predictions": len(out)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and run the expected-points model")
    parser.add_argument("job", choices=["evaluate", "train", "predict", "all"])
    parser.add_argument("--refresh-cache", action="store_true", help="re-download past seasons from the database")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.job == "evaluate":
        season_now = current_season()
        df, names = load_history(season_now, args.refresh_cache)
        feat = build_training_frame(prepare(df, names))
        print(json.dumps(evaluate(feat, _evaluation_seasons(feat, season_now)), indent=2))
    if args.job in ("train", "all"):
        print("train:", train(args.refresh_cache))
    if args.job in ("predict", "all"):
        print("predict:", predict())


if __name__ == "__main__":
    main()
