"""FPL MCP server.

    fpl-mcp                         stdio, for local clients (Claude Code, Claude Desktop, Cursor, VS Code)
    fpl-mcp --http --host 0.0.0.0   Streamable HTTP at /mcp, for remote clients (claude.ai, ChatGPT, Grok)
"""

import argparse
import hmac
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import ingest, optimizer, queries

mcp = MCPServer(
    "fpl",
    instructions=(
        "Fantasy Premier League data: players, teams, fixtures, gameweeks and managers' squads. "
        "Prices are in £m. Fixture difficulty is FPL's 1 (easy) to 5 (hard) rating. "
        "Positions are GKP, DEF, MID, FWD. Expected points come from a model (see model_report); "
        "treat them as estimates. For a manager's team, ask for their FPL team id (the number in "
        "fantasy.premierleague.com/entry/<id>/). If data looks stale, call refresh_data."
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
def predict_points(
    player: str | None = None,
    position: str | None = None,
    team: str | None = None,
    max_price: float | None = None,
    gameweeks: int = 1,
    limit: int = 20,
) -> dict:
    """Model expected points for the next 1-5 gameweeks, best first.

    Already adjusted for injury/suspension news. Double gameweeks are summed.
    Give `player` for one player, or filter by position/team/max_price to rank candidates.
    """
    return queries.predicted_points(
        player=player, position=position, team=team, max_price=max_price, gameweeks=gameweeks, limit=limit
    )


@mcp.tool()
def model_report() -> dict:
    """How accurate the expected-points model is: walk-forward test results versus a simple baseline."""
    return queries.model_report()


@mcp.tool()
def optimal_squad(budget: float = 100.0, gameweeks: int = 3) -> dict:
    """The best 15-man squad from scratch (e.g. for a Wildcard or a new team) for the next N gameweeks.

    Follows FPL rules: 2 GKP / 5 DEF / 5 MID / 3 FWD, max 3 per club, valid formations, budget in £m.
    """
    return optimizer.optimal_squad(budget=budget, gameweeks=gameweeks)


@mcp.tool()
def suggest_transfers(
    team_id: int, free_transfers: int | None = None, max_transfers: int = 2, gameweeks: int = 3
) -> dict:
    """Best transfers for a manager's team over the next N gameweeks, counting -4 hits.

    free_transfers: leave empty to estimate it from the team's history; set it if you know better.
    Recommends rolling the transfer when no move is worth it.
    """
    return optimizer.suggest_transfers(
        team_id, free_transfers=free_transfers, max_transfers=max_transfers, gameweeks=gameweeks
    )


@mcp.tool()
def pick_captain(team_id: int) -> dict:
    """Captain, vice-captain and the top 5 captaincy options in a manager's current squad for next gameweek."""
    return optimizer.pick_captain(team_id)


@mcp.tool()
def chip_advice(team_id: int, gameweeks: int = 5) -> dict:
    """Which chips (Wildcard, Free Hit, Bench Boost, Triple Captain) are still available this half of
    the season, what each is worth now, the best week for each, and whether to play or save it."""
    return optimizer.chip_advice(team_id, gameweeks=gameweeks)


@mcp.tool()
def refresh_data() -> dict:
    """Re-pull players, prices, injuries, gameweeks and fixtures from the FPL API (takes a few seconds)."""
    result = ingest.sync_live()
    queries.invalidate_snapshot()
    return result


def http_app(token: str | None = None, allow_refresh: bool = False):
    """ASGI app for remote clients (claude.ai, ChatGPT, Grok, Cursor...) at /mcp.

    Read-only unless allow_refresh: data updates belong to the scheduled jobs, not to the public.
    With a token, every request except /health needs `Authorization: Bearer <token>`.
    """
    if not allow_refresh:
        try:
            mcp.remove_tool("refresh_data")
        except ToolError:
            pass  # already removed
    # Stateless: no per-client sessions to lose when a free-tier host sleeps or restarts.
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    expected = f"Bearer {token}".encode() if token else None

    async def guarded(scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/health":
            await _respond(send, 200, b'{"status":"ok"}')
            return
        if expected and scope["type"] == "http":
            supplied = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, expected):
                await _respond(send, 401, b'{"error":"missing or invalid bearer token"}')
                return
        await app(scope, receive, send)

    return guarded


async def _respond(send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


def main() -> None:
    parser = argparse.ArgumentParser(description="FPL MCP server")
    parser.add_argument("--http", action="store_true", help="serve over HTTP (Streamable HTTP at /mcp) instead of stdio")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"), help="use 0.0.0.0 when hosting")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()

    if not args.http:
        mcp.run()
        return

    import uvicorn

    app = http_app(
        token=os.getenv("MCP_AUTH_TOKEN") or None,
        allow_refresh=os.getenv("FPL_MCP_ALLOW_REFRESH", "").lower() in ("1", "true", "yes"),
    )
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
