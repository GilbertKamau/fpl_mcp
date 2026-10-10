# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
from sqlalchemy import create_engine, select

from fpl_mcp import config
from fpl_mcp.db import metadata, teams, upsert
from fpl_mcp.ingest import _history_row, current_season_start
from fpl_mcp.queries import _fold


def test_supabase_urls_use_psycopg_driver(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/postgres")
    assert config.database_url() == "postgresql+psycopg://u:p@host:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/postgres")
    assert config.database_url() == "postgresql+psycopg://u:p@host:5432/postgres"


def test_password_special_characters_are_encoded(monkeypatch):
    expected = "postgresql+psycopg://postgres.ref:p%40ss%23word@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres.ref:p@ss#word@aws-0-eu-west-1.pooler.supabase.com:5432/postgres")
    assert config.database_url() == expected
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres.ref:p%40ss%23word@aws-0-eu-west-1.pooler.supabase.com:5432/postgres")
    assert config.database_url() == expected


def test_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert config.database_url().startswith("sqlite:///")


def test_season_label():
    assert config.season_label(2016) == "2016-17"
    assert config.season_label(2099) == "2099-00"


def test_current_season_start():
    assert current_season_start([{"deadline_time": "2026-08-21T17:30:00Z"}]) == 2026


def test_upsert_inserts_then_updates():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    upsert(engine, teams, [{"id": 1, "name": "Arsenal", "short_name": "ARS", "strength": 4}])
    upsert(engine, teams, [{"id": 1, "name": "Arsenal", "short_name": "ARS", "strength": 5}])
    with engine.connect() as conn:
        rows = conn.execute(select(teams.c.id, teams.c.strength)).all()
    assert rows == [(1, 5)]


def test_fold_strips_accents():
    assert _fold("Ødegaard") == "odegaard"
    assert _fold("Gyökeres ") == "gyokeres"


def test_history_row_parses_vaastav_record():
    raw = {
        "fixture": "6", "round": "1", "opponent_team": "16", "kickoff_time": "2024-08-17T14:00:00Z",
        "minutes": "62", "total_points": "2", "expected_goals": "0.01", "value": "50",
        "influence": "22.8", "selected": "4339",
    }
    row = _history_row(
        season="2024-25", element=77, raw=raw, player_code=123, name="Alex Scott",
        position="MID", team_id=3, was_home=False, source="vaastav",
    )
    assert row["fixture"] == 6 and row["round"] == 1
    assert row["minutes"] == 62 and row["total_points"] == 2
    assert row["value"] == 5.0
    assert row["expected_goals"] == 0.01
    assert row["assists"] is None
    assert row["kickoff_time"].tzinfo is not None


def test_availability_alerts_flag_out_and_doubtful_players(monkeypatch):
    from fpl_mcp import queries

    def player(pid, status, chance=None, news=None):
        return {"id": pid, "web_name": f"P{pid}", "team_id": 1, "position": "DEF",
                "status": status, "chance_of_playing_next_round": chance, "news": news}

    squad = [player(1, "a"), player(2, "u", news="Loaned out"), player(3, "d", 75, "Knock"), player(4, "s", 0)]
    snap = {"players_by_id": {p["id"]: p for p in squad}, "teams": {1: {"short_name": "AAA"}}}
    monkeypatch.setattr(queries, "snapshot", lambda: snap)

    alerts = queries.availability_alerts([1, 2, 3, 4], starters={1, 3, 4})
    assert [a["player"] for a in alerts] == ["P4", "P2", "P3"]
    assert alerts[0]["role"] == "starting XI" and alerts[0]["severity"] == "out"
    assert alerts[1]["role"] == "bench" and "No cover" in alerts[1]["impact"]
    assert alerts[2]["severity"] == "doubtful" and "75%" in alerts[2]["impact"]
