"""Load FPL data into the database.

    fpl-sync live      players, teams, gameweeks, fixtures (a few seconds)
    fpl-sync current   this season's per-fixture player history from the FPL API (~1-2 min)
    fpl-sync history   past seasons from the vaastav dataset (a few minutes the first time)
    fpl-sync all       everything above
    fpl-sync weekly    live + current, then retrain the model and refresh predictions
"""

import argparse
import asyncio
import io
import logging
from datetime import datetime, timezone

import httpx
import pandas as pd

from . import api
from .config import FIRST_HISTORY_SEASON, VAASTAV_RAW, season_label
from .db import (
    fixtures,
    gameweeks,
    get_engine,
    player_gameweeks,
    players,
    season_teams,
    sync_log,
    teams,
    upsert,
)

log = logging.getLogger("fpl_mcp.ingest")

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD", 5: "AM"}  # AM = 2024-25 assistant managers
# vaastav uses "GK" in later seasons
POSITION_ALIASES = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD", "AM": "AM"}

INT_STATS = [
    "minutes", "starts", "total_points", "goals_scored", "assists", "clean_sheets",
    "goals_conceded", "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "defensive_contribution", "selected",
    "transfers_in", "transfers_out", "transfers_balance", "team_h_score", "team_a_score",
]
FLOAT_STATS = [
    "influence", "creativity", "threat", "ict_index", "expected_goals", "expected_assists",
    "expected_goal_involvements", "expected_goals_conceded",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value) -> datetime | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def _num(value, cast=float):
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return cast(float(value))
    except (TypeError, ValueError):
        return None


def _bool(value) -> bool | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return bool(value)


def current_season_start(events: list[dict]) -> int:
    first_deadline = pd.Timestamp(events[0]["deadline_time"])
    return first_deadline.year if first_deadline.month >= 7 else first_deadline.year - 1


def _record(engine, job: str, detail: str, rows: int) -> None:
    with engine.begin() as conn:
        conn.execute(sync_log.insert().values(job=job, detail=detail, rows=rows, finished_at=_now()))


# --- Live snapshot -----------------------------------------------------------


def sync_live() -> dict:
    """Refresh players, teams, gameweeks and fixtures from the FPL API."""
    engine = get_engine()
    boot = api.bootstrap()
    fx = api.fixtures()
    now = _now()

    team_rows = [
        {
            "id": t["id"],
            "name": t["name"],
            "short_name": t["short_name"],
            "strength": t.get("strength"),
            "strength_overall_home": t.get("strength_overall_home"),
            "strength_overall_away": t.get("strength_overall_away"),
            "strength_attack_home": t.get("strength_attack_home"),
            "strength_attack_away": t.get("strength_attack_away"),
            "strength_defence_home": t.get("strength_defence_home"),
            "strength_defence_away": t.get("strength_defence_away"),
            "updated_at": now,
        }
        for t in boot["teams"]
    ]

    player_rows = []
    for e in boot["elements"]:
        player_rows.append(
            {
                "id": e["id"],
                "code": e.get("code"),
                "web_name": e["web_name"],
                "first_name": e.get("first_name"),
                "second_name": e.get("second_name"),
                "team_id": e["team"],
                "position": POSITIONS.get(e["element_type"], "UNK"),
                "price": e["now_cost"] / 10,
                "status": e.get("status"),
                "news": e.get("news") or None,
                "chance_of_playing_next_round": e.get("chance_of_playing_next_round"),
                "selected_by_percent": _num(e.get("selected_by_percent")),
                "form": _num(e.get("form")),
                "points_per_game": _num(e.get("points_per_game")),
                "total_points": e.get("total_points"),
                "event_points": e.get("event_points"),
                "minutes": e.get("minutes"),
                "starts": e.get("starts"),
                "goals_scored": e.get("goals_scored"),
                "assists": e.get("assists"),
                "clean_sheets": e.get("clean_sheets"),
                "goals_conceded": e.get("goals_conceded"),
                "saves": e.get("saves"),
                "bonus": e.get("bonus"),
                "bps": e.get("bps"),
                "defensive_contribution": e.get("defensive_contribution"),
                "expected_goals": _num(e.get("expected_goals")),
                "expected_assists": _num(e.get("expected_assists")),
                "expected_goal_involvements": _num(e.get("expected_goal_involvements")),
                "expected_goals_conceded": _num(e.get("expected_goals_conceded")),
                "ict_index": _num(e.get("ict_index")),
                "ep_next": _num(e.get("ep_next")),
                "transfers_in_event": e.get("transfers_in_event"),
                "transfers_out_event": e.get("transfers_out_event"),
                "penalties_order": e.get("penalties_order"),
                "updated_at": now,
            }
        )

    gw_rows = [
        {
            "id": ev["id"],
            "name": ev["name"],
            "deadline_time": _ts(ev["deadline_time"]),
            "finished": ev["finished"],
            "data_checked": ev["data_checked"],
            "is_previous": ev["is_previous"],
            "is_current": ev["is_current"],
            "is_next": ev["is_next"],
            "average_entry_score": ev.get("average_entry_score"),
            "highest_score": ev.get("highest_score"),
            "most_captained": ev.get("most_captained"),
            "updated_at": now,
        }
        for ev in boot["events"]
    ]

    fixture_rows = [
        {
            "id": f["id"],
            "event": f["event"],
            "kickoff_time": _ts(f["kickoff_time"]),
            "team_h": f["team_h"],
            "team_a": f["team_a"],
            "team_h_difficulty": f["team_h_difficulty"],
            "team_a_difficulty": f["team_a_difficulty"],
            "team_h_score": f["team_h_score"],
            "team_a_score": f["team_a_score"],
            "started": f.get("started"),
            "finished": f["finished"],
            "updated_at": now,
        }
        for f in fx
    ]

    upsert(engine, teams, team_rows)
    upsert(engine, players, player_rows)
    upsert(engine, gameweeks, gw_rows)
    upsert(engine, fixtures, fixture_rows)

    season = season_label(current_season_start(boot["events"]))
    upsert(
        engine,
        season_teams,
        [{"season": season, "team_id": t["id"], "team_name": t["name"]} for t in boot["teams"]],
    )

    summary = {
        "season": season,
        "teams": len(team_rows),
        "players": len(player_rows),
        "gameweeks": len(gw_rows),
        "fixtures": len(fixture_rows),
    }
    _record(engine, "live", str(summary), len(player_rows))
    return summary


# --- Current season per-fixture history (FPL API) -----------------------------


def sync_current_season() -> dict:
    """Pull every player's per-fixture history for this season from element-summary."""
    engine = get_engine()
    boot = api.bootstrap()
    season = season_label(current_season_start(boot["events"]))
    elements = {e["id"]: e for e in boot["elements"]}

    summaries = asyncio.run(api.element_summaries(list(elements)))
    rows = []
    for pid, summary in summaries.items():
        e = elements[pid]
        for h in summary.get("history", []):
            was_home = bool(h["was_home"])
            rows.append(
                _history_row(
                    season=season,
                    element=pid,
                    raw=h,
                    player_code=e.get("code"),
                    name=f"{e.get('first_name', '')} {e.get('second_name', '')}".strip(),
                    position=POSITIONS.get(e["element_type"]),
                    # The player's team at the time of the fixture, not today's club.
                    team_id=None,
                    was_home=was_home,
                    source="fpl_api",
                )
            )

    # Work out each row's team from the fixture (handles mid-season transfers).
    fixture_teams = {
        f["id"]: (f["team_h"], f["team_a"]) for f in api.fixtures()
    }
    for r in rows:
        teams_for_fixture = fixture_teams.get(r["fixture"])
        if teams_for_fixture:
            r["team_id"] = teams_for_fixture[0] if r["was_home"] else teams_for_fixture[1]
        else:
            r["team_id"] = elements[r["element"]]["team"]

    upsert(engine, player_gameweeks, rows)
    result = {"season": season, "players": len(summaries), "rows": len(rows)}
    _record(engine, "current", str(result), len(rows))
    return result


# --- Past seasons (vaastav/Fantasy-Premier-League) ----------------------------


def _read_csv(client: httpx.Client, url: str) -> pd.DataFrame | None:
    resp = client.get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    raw = resp.content
    for encoding in ("utf-8", "latin-1"):  # older seasons are latin-1
        try:
            return pd.read_csv(io.StringIO(raw.decode(encoding)), low_memory=False)
        except UnicodeDecodeError:
            continue
    return None


def sync_history(seasons: list[str] | None = None) -> dict:
    """Load per-fixture player history for past seasons from the vaastav dataset."""
    engine = get_engine()
    boot = api.bootstrap()
    current_start = current_season_start(boot["events"])
    if not seasons:
        # The current season comes from the live API (vaastav lags behind it).
        seasons = [season_label(y) for y in range(FIRST_HISTORY_SEASON, current_start)]

    totals: dict[str, int] = {}
    with httpx.Client(timeout=httpx.Timeout(120.0), follow_redirects=True) as client:
        team_list = _read_csv(client, f"{VAASTAV_RAW}/master_team_list.csv")
        for season in seasons:
            log.info("Loading %s", season)
            gw = _read_csv(client, f"{VAASTAV_RAW}/{season}/gws/merged_gw.csv")
            if gw is None:
                log.warning("No merged_gw.csv for %s, skipping", season)
                continue
            raw_players = _read_csv(client, f"{VAASTAV_RAW}/{season}/players_raw.csv")
            season_fixtures = _read_csv(client, f"{VAASTAV_RAW}/{season}/fixtures.csv")

            meta = {}
            if raw_players is not None:
                for p in raw_players.itertuples(index=False):
                    meta[int(p.id)] = {
                        "code": _num(getattr(p, "code", None), int),
                        "position": POSITIONS.get(int(p.element_type)),
                        "team": int(p.team),
                        "name": f"{p.first_name} {p.second_name}",
                    }

            fixture_teams = {}
            if season_fixtures is not None:
                for f in season_fixtures.itertuples(index=False):
                    fixture_teams[int(f.id)] = (int(f.team_h), int(f.team_a))

            rows = []
            for rec in gw.to_dict("records"):
                element = int(rec["element"])
                m = meta.get(element, {})
                was_home = _bool(rec.get("was_home"))
                fixture_id = int(rec["fixture"])
                team_id = None
                if fixture_id in fixture_teams and was_home is not None:
                    h, a = fixture_teams[fixture_id]
                    team_id = h if was_home else a
                else:
                    team_id = m.get("team")
                position = POSITION_ALIASES.get(str(rec.get("position", "")).upper()) or m.get("position")
                rows.append(
                    _history_row(
                        season=season,
                        element=element,
                        raw=rec,
                        player_code=m.get("code"),
                        name=m.get("name") or str(rec.get("name", "")).replace("_", " "),
                        position=position,
                        team_id=team_id,
                        was_home=was_home,
                        source="vaastav",
                    )
                )

            if not fixture_teams:
                _fix_teams_from_majority(rows)

            # A few seasons contain exact duplicate rows; keep the last one per key.
            dedup = {(r["season"], r["element"], r["fixture"]): r for r in rows}
            upsert(engine, player_gameweeks, list(dedup.values()))
            totals[season] = len(dedup)

            upsert(engine, season_teams, _season_team_rows(client, season, team_list))

    _record(engine, "history", str(totals), sum(totals.values()))
    return totals


def _fix_teams_from_majority(rows: list[dict]) -> None:
    """Without fixtures.csv, team_id comes from players_raw (the club a player ended the season at),
    which is wrong for players who moved mid-season. Each side of a fixture is one club, so use the
    club most of that side's players belong to."""
    from collections import Counter

    sides: dict[tuple, Counter] = {}
    for r in rows:
        if r["team_id"] is not None:
            sides.setdefault((r["fixture"], r["was_home"]), Counter())[r["team_id"]] += 1
    for r in rows:
        counts = sides.get((r["fixture"], r["was_home"]))
        if counts:
            r["team_id"] = counts.most_common(1)[0][0]


def _season_team_rows(client: httpx.Client, season: str, team_list: pd.DataFrame | None) -> list[dict]:
    """Team id -> name for a season: master_team_list.csv, else that season's teams.csv."""
    if team_list is not None:
        st = team_list[team_list["season"] == season]
        if not st.empty:
            return [{"season": season, "team_id": int(t.team), "team_name": t.team_name} for t in st.itertuples(index=False)]
    teams_csv = _read_csv(client, f"{VAASTAV_RAW}/{season}/teams.csv")
    if teams_csv is None:
        log.warning("No team names for %s", season)
        return []
    return [{"season": season, "team_id": int(t.id), "team_name": t.name} for t in teams_csv.itertuples(index=False)]


def _history_row(*, season, element, raw, player_code, name, position, team_id, was_home, source) -> dict:
    row = {
        "season": season,
        "element": element,
        "fixture": int(raw["fixture"]),
        "round": _num(raw.get("round", raw.get("GW")), int),
        "player_code": player_code,
        "name": name,
        "position": position,
        "team_id": team_id,
        "opponent_team": _num(raw.get("opponent_team"), int),
        "was_home": was_home,
        "kickoff_time": _ts(raw.get("kickoff_time")),
        "value": value / 10 if (value := _num(raw.get("value"))) else None,  # stored in tenths
        "source": source,
    }
    for col in INT_STATS:
        row[col] = _num(raw.get(col), int)
    for col in FLOAT_STATS:
        row[col] = _num(raw.get(col))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Load FPL data into the database")
    parser.add_argument("job", choices=["live", "current", "history", "all", "weekly"])
    parser.add_argument("--seasons", nargs="*", help="e.g. 2023-24 2024-25 (history only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.job in ("live", "all", "weekly"):
        print("live:", sync_live())
    if args.job in ("history", "all"):
        print("history:", sync_history(args.seasons))
    if args.job in ("current", "all", "weekly"):
        print("current:", sync_current_season())
    if args.job == "weekly":
        from . import model

        print("train:", model.train())
        print("predict:", model.predict())


if __name__ == "__main__":
    main()
