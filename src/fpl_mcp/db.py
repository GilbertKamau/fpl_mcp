"""Database schema and helpers. Works on SQLite and Postgres (Supabase)."""

import logging
from collections.abc import Iterable, Sequence
from functools import lru_cache

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import ProgrammingError

from .config import database_url

metadata = MetaData()

# --- Current season snapshot (refreshed from the live FPL API) -------------

teams = Table(
    "teams",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(64), nullable=False),
    Column("short_name", String(8), nullable=False),
    Column("strength", Integer),
    Column("strength_overall_home", Integer),
    Column("strength_overall_away", Integer),
    Column("strength_attack_home", Integer),
    Column("strength_attack_away", Integer),
    Column("strength_defence_home", Integer),
    Column("strength_defence_away", Integer),
    Column("updated_at", DateTime(timezone=True)),
)

players = Table(
    "players",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("code", Integer),
    Column("web_name", String(64), nullable=False),
    Column("first_name", String(64)),
    Column("second_name", String(64)),
    Column("team_id", Integer, index=True),
    Column("position", String(3), index=True),  # GKP / DEF / MID / FWD
    Column("price", Float),  # £m
    Column("status", String(1)),  # a=available, d=doubtful, i=injured, s=suspended, u=unavailable
    Column("news", Text),
    Column("chance_of_playing_next_round", Integer),
    Column("selected_by_percent", Float),
    Column("form", Float),
    Column("points_per_game", Float),
    Column("total_points", Integer),
    Column("event_points", Integer),
    Column("minutes", Integer),
    Column("starts", Integer),
    Column("goals_scored", Integer),
    Column("assists", Integer),
    Column("clean_sheets", Integer),
    Column("goals_conceded", Integer),
    Column("saves", Integer),
    Column("bonus", Integer),
    Column("bps", Integer),
    Column("defensive_contribution", Integer),
    Column("expected_goals", Float),
    Column("expected_assists", Float),
    Column("expected_goal_involvements", Float),
    Column("expected_goals_conceded", Float),
    Column("ict_index", Float),
    Column("ep_next", Float),  # FPL's own expected points for next GW
    Column("transfers_in_event", Integer),
    Column("transfers_out_event", Integer),
    Column("penalties_order", Integer),
    Column("updated_at", DateTime(timezone=True)),
)

gameweeks = Table(
    "gameweeks",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(32)),
    Column("deadline_time", DateTime(timezone=True)),
    Column("finished", Boolean),
    Column("data_checked", Boolean),
    Column("is_previous", Boolean),
    Column("is_current", Boolean),
    Column("is_next", Boolean),
    Column("average_entry_score", Integer),
    Column("highest_score", Integer),
    Column("most_captained", Integer),
    Column("updated_at", DateTime(timezone=True)),
)

fixtures = Table(
    "fixtures",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event", Integer, index=True),  # gameweek; NULL when not yet scheduled
    Column("kickoff_time", DateTime(timezone=True)),
    Column("team_h", Integer, index=True),
    Column("team_a", Integer, index=True),
    Column("team_h_difficulty", Integer),
    Column("team_a_difficulty", Integer),
    Column("team_h_score", Integer),
    Column("team_a_score", Integer),
    Column("started", Boolean),
    Column("finished", Boolean),
    Column("updated_at", DateTime(timezone=True)),
)

# --- History: one row per player per fixture, all seasons ------------------

season_teams = Table(
    "season_teams",
    metadata,
    Column("season", String(7), primary_key=True),
    Column("team_id", Integer, primary_key=True),
    Column("team_name", String(64), nullable=False),
)

player_gameweeks = Table(
    "player_gameweeks",
    metadata,
    Column("season", String(7), primary_key=True),
    Column("element", Integer, primary_key=True),  # player id within that season
    Column("fixture", Integer, primary_key=True),
    Column("round", Integer, index=True),
    Column("player_code", Integer, index=True),  # stable across seasons
    Column("name", String(128)),
    Column("position", String(3)),
    Column("team_id", Integer),
    Column("opponent_team", Integer),
    Column("was_home", Boolean),
    Column("kickoff_time", DateTime(timezone=True)),
    Column("minutes", Integer),
    Column("starts", Integer),
    Column("total_points", Integer),
    Column("goals_scored", Integer),
    Column("assists", Integer),
    Column("clean_sheets", Integer),
    Column("goals_conceded", Integer),
    Column("own_goals", Integer),
    Column("penalties_saved", Integer),
    Column("penalties_missed", Integer),
    Column("yellow_cards", Integer),
    Column("red_cards", Integer),
    Column("saves", Integer),
    Column("bonus", Integer),
    Column("bps", Integer),
    Column("influence", Float),
    Column("creativity", Float),
    Column("threat", Float),
    Column("ict_index", Float),
    Column("expected_goals", Float),
    Column("expected_assists", Float),
    Column("expected_goal_involvements", Float),
    Column("expected_goals_conceded", Float),
    Column("defensive_contribution", Integer),
    Column("value", Float),  # price in £m at the time
    Column("selected", Integer),
    Column("transfers_in", Integer),
    Column("transfers_out", Integer),
    Column("transfers_balance", Integer),
    Column("team_h_score", Integer),
    Column("team_a_score", Integer),
    Column("source", String(16)),  # "vaastav" or "fpl_api"
)

# --- Model outputs -----------------------------------------------------------------

predictions = Table(
    "predictions",
    metadata,
    Column("player_id", Integer, primary_key=True),
    Column("fixture_id", Integer, primary_key=True),
    Column("gameweek", Integer, index=True),
    Column("expected_points", Float),  # after the availability adjustment
    Column("raw_expected_points", Float),  # model output assuming the player is available
    Column("availability", Float),  # 0-1 multiplier from injury/suspension news
    Column("model_version", String(32)),
    Column("created_at", DateTime(timezone=True)),
)

model_runs = Table(
    "model_runs",
    metadata,
    Column("version", String(32), primary_key=True),
    Column("trained_at", DateTime(timezone=True)),
    Column("accepted", Boolean),  # only accepted models are used for predictions
    Column("mae", Float),  # walk-forward MAE across the evaluation seasons
    Column("baseline_mae", Float),
    Column("metrics", Text),  # JSON: per-season details
    Column("note", Text),
)

sync_log = Table(
    "sync_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job", String(32), nullable=False),
    Column("detail", Text),
    Column("rows", Integer),
    Column("finished_at", DateTime(timezone=True)),
)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    try:
        metadata.create_all(engine)
    except ProgrammingError as exc:
        # A read-only user (the hosted server's) can't create tables; that's fine once they exist.
        logging.getLogger("fpl_mcp.db").warning("Could not create tables (%s); assuming they exist.", exc.orig)
    return engine


def upsert(engine: Engine, table: Table, rows: Sequence[dict], chunk_size: int = 2000) -> int:
    """Insert rows, updating existing ones on primary-key conflict."""
    if not rows:
        return 0
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError(f"Unsupported database: {engine.dialect.name}")

    pk = [c.name for c in table.primary_key.columns]
    columns = {c.name for c in table.columns}
    with engine.begin() as conn:
        for chunk in _chunks(rows, chunk_size):
            clean = [{k: v for k, v in r.items() if k in columns} for r in chunk]
            stmt = insert(table)
            updatable = [c for c in clean[0] if c not in pk]
            if updatable:
                stmt = stmt.on_conflict_do_update(
                    index_elements=pk, set_={c: stmt.excluded[c] for c in updatable}
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=pk)
            conn.execute(stmt, clean)
    return len(rows)


def _chunks(rows: Sequence[dict], size: int) -> Iterable[Sequence[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]
