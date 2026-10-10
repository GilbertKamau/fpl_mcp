"""FPL MCP server (stdio). Run with `fpl-mcp` or `python -m fpl_mcp.server`."""

from mcp.server.mcpserver import MCPServer

from . import ingest, queries

mcp = MCPServer(
    "fpl",
    instructions=(
        "Fantasy Premier League data: players, teams, fixtures, gameweeks and managers' squads. "
        "Prices are in £m. Fixture difficulty is FPL's 1 (easy) to 5 (hard) rating. "
        "Positions are GKP, DEF, MID, FWD. If data looks stale, call refresh_data."
    ),
)


@mcp.tool()
def gameweek_status() -> dict:
    """Current and next gameweek, the next deadline, and when the data was last refreshed."""
    return queries.gameweek_status()


@mcp.tool()
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
    """Find and rank players.

    position: GKP, DEF, MID or FWD. team: name or short name (e.g. "ARS").
    sort_by: total_points, form, price, points_per_game, selected_by_percent, minutes,
    goals_scored, assists, expected_goals, expected_assists, expected_goal_involvements,
    ict_index, ep_next, bonus, clean_sheets, transfers_in_event, defensive_contribution.
    """
    return queries.search_players(
        name=name,
        position=position,
        team=team,
        max_price=max_price,
        min_price=min_price,
        min_minutes=min_minutes,
        available_only=available_only,
        sort_by=sort_by,
        limit=limit,
    )


@mcp.tool()
def get_player(player: str) -> dict:
    """One player's season stats, injury news, last 6 matches and next 5 gameweeks of fixtures.

    player: name (accents optional, e.g. "Odegaard") or FPL id.
    """
    return queries.get_player(player)


@mcp.tool()
def compare_players(players: list[str]) -> dict:
    """Compare 2-6 players side by side, including per-90 output, value and fixture difficulty."""
    return queries.compare_players(players)


@mcp.tool()
def get_fixtures(team: str | None = None, gameweek: int | None = None) -> dict:
    """Fixtures for a team (next 6 gameweeks), or every fixture in a gameweek (default: next gameweek)."""
    return queries.get_fixtures(team=team, gameweek=gameweek)


@mcp.tool()
def team_summary(team: str) -> dict:
    """A club's results so far, goals for/against, clean sheets, upcoming fixtures and top FPL players."""
    return queries.team_summary(team)


@mcp.tool()
def player_season_history(player: str) -> dict:
    """A player's FPL totals for every season loaded (back to 2016-17), linked across seasons."""
    return queries.player_season_history(player)


@mcp.tool()
def get_my_team(team_id: int, gameweek: int | None = None) -> dict:
    """A manager's 15-man squad, captain, bank and rank for a gameweek (default: current).

    team_id is the number in the FPL website URL: fantasy.premierleague.com/entry/<team_id>/event/<gw>.
    Picks for a gameweek only become public after its deadline.
    """
    return queries.my_team(team_id, gameweek)


@mcp.tool()
def refresh_data() -> dict:
    """Re-pull players, prices, injuries, gameweeks and fixtures from the FPL API (takes a few seconds)."""
    result = ingest.sync_live()
    queries.invalidate_snapshot()
    return result


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
