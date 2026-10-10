"""Read-only queries behind the MCP tools."""

import threading
import time
import unicodedata
from datetime import datetime, timezone

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import and_, func, select

from . import api
from .config import season_label
from .db import fixtures, gameweeks, get_engine, player_gameweeks, players, teams

SORTABLE = {
    "total_points", "form", "price", "points_per_game", "selected_by_percent", "minutes",
    "goals_scored", "assists", "expected_goals", "expected_assists",
    "expected_goal_involvements", "ict_index", "ep_next", "bonus", "clean_sheets",
    "transfers_in_event", "defensive_contribution",
}
STATUS = {"a": "available", "d": "doubtful", "i": "injured", "s": "suspended", "u": "unavailable", "n": "not in squad"}


class NotFound(ToolError):
    """Raised for bad lookups; the message is shown to the AI so it can correct itself."""


def _fold(text: str | None) -> str:
    """Lowercase and strip accents so 'odegaard' matches 'Ødegaard'."""
    if not text:
        return ""
    text = text.replace("ø", "o").replace("Ø", "O").replace("ß", "ss")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def _iso(value: datetime | None) -> str | None:
    """ISO timestamp in UTC (SQLite hands datetimes back without a timezone)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _rows(stmt) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings()]


# --- Current-season snapshot ------------------------------------------------------
# players/teams/fixtures/gameweeks are small, so they are read once and kept in memory.
# A remote database (Supabase) costs ~0.5-1s per round trip; this keeps tools fast.

SNAPSHOT_TTL_SECONDS = 600
_snapshot_lock = threading.Lock()
_snapshot: dict | None = None
_snapshot_loaded_at = 0.0


def invalidate_snapshot() -> None:
    global _snapshot
    with _snapshot_lock:
        _snapshot = None


def snapshot() -> dict:
    global _snapshot, _snapshot_loaded_at
    with _snapshot_lock:
        if _snapshot is not None and time.monotonic() - _snapshot_loaded_at < SNAPSHOT_TTL_SECONDS:
            return _snapshot
        with get_engine().connect() as conn:
            player_rows = [dict(r) for r in conn.execute(select(players)).mappings()]
            team_rows = [dict(r) for r in conn.execute(select(teams)).mappings()]
            fixture_rows = [dict(r) for r in conn.execute(select(fixtures)).mappings()]
            gw_rows = [dict(r) for r in conn.execute(select(gameweeks).order_by(gameweeks.c.id)).mappings()]
        if not player_rows:
            raise NotFound("The database is empty. Run `fpl-sync live` (or the refresh_data tool) first.")
        fixture_rows.sort(key=lambda f: (f["event"] or 99, _iso(f["kickoff_time"]) or ""))
        season = None
        if gw_rows and gw_rows[0]["deadline_time"]:
            first = gw_rows[0]["deadline_time"]
            season = season_label(first.year if first.month >= 7 else first.year - 1)
        _snapshot = {
            "players": player_rows,
            "players_by_id": {p["id"]: p for p in player_rows},
            "teams": {t["id"]: t for t in team_rows},
            "fixtures": fixture_rows,
            "gameweeks": gw_rows,
            "season": season,
        }
        _snapshot_loaded_at = time.monotonic()
        return _snapshot


# --- Lookups -------------------------------------------------------------------


def team_map() -> dict[int, dict]:
    return snapshot()["teams"]


def resolve_team(name: str) -> dict:
    q = _fold(name)
    all_teams = list(team_map().values())
    for t in all_teams:
        if q in (_fold(t["name"]), _fold(t["short_name"])):
            return t
    matches = [t for t in all_teams if q in _fold(t["name"])]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFound(f"No team matches '{name}'. Teams: {', '.join(t['name'] for t in all_teams)}")
    raise NotFound(f"'{name}' is ambiguous: {', '.join(t['name'] for t in matches)}")


def resolve_player(query: str | int) -> dict:
    """Find one player by id, web name or full name (accent-insensitive)."""
    snap = snapshot()
    if isinstance(query, int) or str(query).isdigit():
        if int(query) in snap["players_by_id"]:
            return snap["players_by_id"][int(query)]
        raise NotFound(f"No player with id {query}")

    q = _fold(str(query))
    candidates = snap["players"]
    scored = []
    for p in candidates:
        web = _fold(p["web_name"])
        full = _fold(f"{p['first_name']} {p['second_name']}")
        if q == web or q == full:
            score = 3
        elif q == _fold(p["second_name"]) or full.endswith(" " + q):
            score = 2
        elif q in web or q in full:
            score = 1
        else:
            continue
        scored.append((score, p["total_points"] or 0, p))
    if not scored:
        raise NotFound(f"No player matches '{query}'")
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    best = [s for s in scored if s[0] == scored[0][0]]
    if len(best) == 1:
        return best[0][2]
    tm = team_map()
    options = ", ".join(
        f"{p['web_name']} ({tm.get(p['team_id'], {}).get('short_name', '?')}, id {p['id']})" for _, _, p in best[:8]
    )
    raise NotFound(f"'{query}' matches several players: {options}. Use a fuller name or the id.")


# --- Formatting --------------------------------------------------------------------


def _player_card(p: dict, tm: dict[int, dict]) -> dict:
    team = tm.get(p["team_id"], {})
    card = {
        "id": p["id"],
        "name": p["web_name"],
        "full_name": f"{p['first_name']} {p['second_name']}",
        "team": team.get("short_name"),
        "position": p["position"],
        "price": p["price"],
        "status": STATUS.get(p["status"], p["status"]),
        "total_points": p["total_points"],
        "form": p["form"],
        "points_per_game": p["points_per_game"],
        "minutes": p["minutes"],
        "goals": p["goals_scored"],
        "assists": p["assists"],
        "clean_sheets": p["clean_sheets"],
        "xG": p["expected_goals"],
        "xA": p["expected_assists"],
        "xGI": p["expected_goal_involvements"],
        "bonus": p["bonus"],
        "defensive_contribution": p["defensive_contribution"],
        "selected_by_percent": p["selected_by_percent"],
        "fpl_expected_points_next_gw": p["ep_next"],
    }
    if p["news"]:
        card["news"] = p["news"]
        card["chance_of_playing_next_round"] = p["chance_of_playing_next_round"]
    return card


def _fixture_view(f: dict, team_id: int, tm: dict[int, dict]) -> dict:
    home = f["team_h"] == team_id
    opp = f["team_a"] if home else f["team_h"]
    view = {
        "gameweek": f["event"],
        "opponent": tm.get(opp, {}).get("short_name"),
        "venue": "H" if home else "A",
        "difficulty": f["team_h_difficulty"] if home else f["team_a_difficulty"],
        "kickoff": _iso(f["kickoff_time"]),
    }
    if f["finished"]:
        gf, ga = (f["team_h_score"], f["team_a_score"]) if home else (f["team_a_score"], f["team_h_score"])
        view["score"] = f"{gf}-{ga}"
    return view


# --- Queries ----------------------------------------------------------------------


def gameweek_status() -> dict:
    gws = snapshot()["gameweeks"]
    current = next((g for g in gws if g["is_current"]), None)
    nxt = next((g for g in gws if g["is_next"]), None)

    def view(g):
        if not g:
            return None
        return {
            "gameweek": g["id"],
            "deadline": _iso(g["deadline_time"]),
            "finished": g["finished"],
            "average_score": g["average_entry_score"],
            "highest_score": g["highest_score"],
        }

    updated = max((p["updated_at"] for p in snapshot()["players"] if p["updated_at"]), default=None)
    return {
        "current": view(current),
        "next": view(nxt),
        "data_updated_at": _iso(updated),
    }


def next_gameweek() -> int | None:
    return next((g["id"] for g in snapshot()["gameweeks"] if g["is_next"]), None)


def upcoming_fixtures(team_id: int, count: int = 5) -> list[dict]:
    tm = team_map()
    upcoming = [
        _fixture_view(f, team_id, tm)
        for f in snapshot()["fixtures"]
        if team_id in (f["team_h"], f["team_a"])
        and not f["finished"]
        and not f["started"]
        and f["event"] is not None
    ]
    gws = sorted({f["gameweek"] for f in upcoming})[:count]
    return [f for f in upcoming if f["gameweek"] in gws]


def search_players(
    name: str | None = None,
    position: str | None = None,
    team: str | None = None,
    max_price: float | None = None,
    min_price: float | None = None,
    min_minutes: int = 0,
    available_only: bool = False,
    sort_by: str = "total_points",
    limit: int = 20,
) -> list[dict]:
    if sort_by not in SORTABLE:
        raise ToolError(f"sort_by must be one of: {', '.join(sorted(SORTABLE))}")
    rows = snapshot()["players"]
    if position:
        pos = position.upper()
        pos = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD", "ST": "FWD"}.get(pos, pos)
        rows = [p for p in rows if p["position"] == pos]
    if team:
        team_id = resolve_team(team)["id"]
        rows = [p for p in rows if p["team_id"] == team_id]
    if max_price is not None:
        rows = [p for p in rows if p["price"] is not None and p["price"] <= max_price]
    if min_price is not None:
        rows = [p for p in rows if p["price"] is not None and p["price"] >= min_price]
    if min_minutes:
        rows = [p for p in rows if (p["minutes"] or 0) >= min_minutes]
    if available_only:
        rows = [p for p in rows if p["status"] == "a"]
    # Highest first, missing values last.
    rows = sorted(rows, key=lambda p: (p[sort_by] is not None, p[sort_by] or 0), reverse=True)

    if name:
        q = _fold(name)
        rows = [p for p in rows if q in _fold(f"{p['web_name']} {p['first_name']} {p['second_name']}")]
    tm = team_map()
    return [_player_card(p, tm) for p in rows[: max(1, min(limit, 100))]]


def recent_gameweeks(player_id: int, count: int = 6) -> list[dict]:
    season = snapshot()["season"]
    if not season:
        return []
    tm = team_map()
    stmt = (
        select(player_gameweeks)
        .where(
            and_(
                player_gameweeks.c.season == season,
                player_gameweeks.c.element == player_id,
            )
        )
        .order_by(player_gameweeks.c.kickoff_time.desc())
        .limit(count)
    )
    return [
        {
            "gameweek": r["round"],
            "opponent": tm.get(r["opponent_team"], {}).get("short_name"),
            "venue": "H" if r["was_home"] else "A",
            "minutes": r["minutes"],
            "points": r["total_points"],
            "goals": r["goals_scored"],
            "assists": r["assists"],
            "bonus": r["bonus"],
            "xG": r["expected_goals"],
            "xA": r["expected_assists"],
            "defensive_contribution": r["defensive_contribution"],
        }
        for r in _rows(stmt)
    ]


def get_player(query: str | int) -> dict:
    p = resolve_player(query)
    tm = team_map()
    card = _player_card(p, tm)
    card["recent_gameweeks"] = recent_gameweeks(p["id"])
    card["upcoming_fixtures"] = upcoming_fixtures(p["team_id"])
    if not card["recent_gameweeks"]:
        card["note"] = "No per-gameweek history loaded yet; run `fpl-sync current`."
    return card


def compare_players(queries: list[str]) -> dict:
    if len(queries) < 2:
        raise ToolError("Give at least two players to compare.")
    tm = team_map()
    cards = []
    for q in queries[:6]:
        p = resolve_player(q)
        card = _player_card(p, tm)
        fx = upcoming_fixtures(p["team_id"])
        card["next_5_avg_difficulty"] = round(sum(f["difficulty"] for f in fx) / len(fx), 2) if fx else None
        card["next_fixtures"] = ", ".join(f"{f['opponent']}({f['venue']})" for f in fx)
        mins = p["minutes"] or 0
        card["points_per_90"] = round((p["total_points"] or 0) * 90 / mins, 2) if mins else None
        card["xGI_per_90"] = round((p["expected_goal_involvements"] or 0) * 90 / mins, 2) if mins else None
        card["points_per_million"] = round((p["total_points"] or 0) / p["price"], 2) if p["price"] else None
        cards.append(card)
    return {"players": cards}


def get_fixtures(team: str | None = None, gameweek: int | None = None) -> dict:
    tm = team_map()
    if team:
        t = resolve_team(team)
        return {"team": t["name"], "fixtures": upcoming_fixtures(t["id"], count=6)}

    gw = gameweek or next_gameweek()
    rows = [f for f in snapshot()["fixtures"] if f["event"] == gw]
    out = []
    for f in rows:
        item = {
            "home": tm[f["team_h"]]["short_name"],
            "away": tm[f["team_a"]]["short_name"],
            "home_difficulty": f["team_h_difficulty"],
            "away_difficulty": f["team_a_difficulty"],
            "kickoff": _iso(f["kickoff_time"]),
        }
        if f["finished"] or f["started"]:
            item["score"] = f"{f['team_h_score']}-{f['team_a_score']}"
        out.append(item)
    return {"gameweek": gw, "fixtures": out}


def team_summary(team: str) -> dict:
    t = resolve_team(team)
    fx = upcoming_fixtures(t["id"], count=6)
    played = [f for f in snapshot()["fixtures"] if t["id"] in (f["team_h"], f["team_a"]) and f["finished"]]
    played.reverse()  # most recent first
    tm = team_map()
    results = [_fixture_view(f, t["id"], tm) for f in played]
    scored = conceded = clean_sheets = 0
    for r in results:
        gf, ga = map(int, r["score"].split("-"))
        scored, conceded = scored + gf, conceded + ga
        clean_sheets += ga == 0
    top = search_players(team=t["name"], sort_by="total_points", limit=5)
    return {
        "team": t["name"],
        "played": len(results),
        "goals_for": scored,
        "goals_against": conceded,
        "clean_sheets": clean_sheets,
        "last_5": results[:5],
        "upcoming": fx,
        "avg_upcoming_difficulty": round(sum(f["difficulty"] for f in fx) / len(fx), 2) if fx else None,
        "top_players": [{k: p[k] for k in ("name", "position", "price", "total_points", "form")} for p in top],
    }


def player_season_history(query: str | int) -> dict:
    """Season-by-season totals for a player across every loaded season."""
    p = resolve_player(query)
    if not p["code"]:
        raise NotFound("This player has no stable code, so past seasons can't be linked.")
    pg = player_gameweeks.c
    stmt = (
        select(
            pg.season,
            func.max(pg.position).label("position"),
            func.count().label("appearances_rows"),
            func.sum(pg.minutes).label("minutes"),
            func.sum(pg.total_points).label("points"),
            func.sum(pg.goals_scored).label("goals"),
            func.sum(pg.assists).label("assists"),
            func.sum(pg.clean_sheets).label("clean_sheets"),
            func.sum(pg.bonus).label("bonus"),
            func.sum(pg.expected_goals).label("xG"),
            func.sum(pg.expected_assists).label("xA"),
            func.min(pg.value).label("min_price"),
            func.max(pg.value).label("max_price"),
        )
        .where(pg.player_code == p["code"])
        .group_by(pg.season)
        .order_by(pg.season)
    )
    seasons = []
    for r in _rows(stmt):
        r = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}
        r["fixtures"] = r.pop("appearances_rows")
        if r["xG"] is None:
            r.pop("xG"), r.pop("xA")  # xG only exists from 2022-23
        seasons.append(r)
    if not seasons:
        return {"player": p["web_name"], "seasons": [], "note": "No history loaded; run `fpl-sync history` and `fpl-sync current`."}
    return {"player": p["web_name"], "seasons": seasons}


def my_team(team_id: int, gameweek: int | None = None) -> dict:
    """A manager's squad for a gameweek, using the public entry endpoints (no login)."""
    profile = api.entry(team_id)
    gw = gameweek or profile.get("current_event")
    if not gw:
        raise NotFound("This team has no gameweeks played yet.")
    picks = api.entry_picks(team_id, gw)
    tm = team_map()
    by_id = snapshot()["players_by_id"]

    squad = []
    for pick in picks["picks"]:
        p = by_id.get(pick["element"])
        if not p:
            continue
        fx = upcoming_fixtures(p["team_id"], count=3)
        squad.append(
            {
                "slot": pick["position"],
                "starting": pick["position"] <= 11,
                "captain": pick["is_captain"],
                "vice_captain": pick["is_vice_captain"],
                "name": p["web_name"],
                "team": tm.get(p["team_id"], {}).get("short_name"),
                "position": p["position"],
                "price": p["price"],
                "form": p["form"],
                "total_points": p["total_points"],
                "status": STATUS.get(p["status"], p["status"]),
                "news": p["news"],
                "next_fixtures": ", ".join(f"{f['opponent']}({f['venue']}) d{f['difficulty']}" for f in fx),
            }
        )

    eh = picks.get("entry_history", {})
    return {
        "team_name": profile.get("name"),
        "manager": f"{profile.get('player_first_name', '')} {profile.get('player_last_name', '')}".strip(),
        "gameweek": gw,
        "overall_rank": profile.get("summary_overall_rank"),
        "overall_points": profile.get("summary_overall_points"),
        "gameweek_points": eh.get("points"),
        "bank": (eh.get("bank") or 0) / 10,
        "squad_value": (eh.get("value") or 0) / 10,
        "transfers_made_this_gw": eh.get("event_transfers"),
        "transfer_cost_this_gw": eh.get("event_transfers_cost"),
        "active_chip": picks.get("active_chip"),
        "squad": squad,
    }
