# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
"""FPL MCP server: `fpl-mcp` (stdio) or `fpl-mcp --http` (Streamable HTTP at /mcp)."""

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
    """Find and rank players; position GKP/DEF/MID/FWD, sort_by any stat (e.g. form, price, ep_next)."""
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
    """A player's stats, injury news, last 6 matches and next fixtures. player: name or FPL id."""
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
    """A manager's squad, bank, rank and availability_alerts. team_id is the number in the FPL entry URL."""
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
    """Expected points for the next 1-5 gameweeks, best first; one player, or filter to rank candidates."""
    return queries.predicted_points(
        player=player, position=position, team=team, max_price=max_price, gameweeks=gameweeks, limit=limit
    )


@mcp.tool()
def model_report() -> dict:
    """Model accuracy: past-season tests and this season's live results vs a baseline and FPL's own."""
    return queries.model_report()


@mcp.tool()
def optimal_squad(budget: float = 100.0, gameweeks: int = 3) -> dict:
    """Best 15-man squad from scratch (e.g. a Wildcard) for a budget in £m, following FPL rules."""
    return optimizer.optimal_squad(budget=budget, gameweeks=gameweeks)


@mcp.tool()
def suggest_transfers(
    team_id: int, free_transfers: int | None = None, max_transfers: int = 2, gameweeks: int = 3
) -> dict:
    """Best transfers over the next N gameweeks after -4 hits; mention any availability_alerts to the user."""
    return optimizer.suggest_transfers(
        team_id, free_transfers=free_transfers, max_transfers=max_transfers, gameweeks=gameweeks
    )


@mcp.tool()
def pick_captain(team_id: int) -> dict:
    """Captain, vice-captain and the top 5 captaincy options in a manager's current squad for next gameweek."""
    return optimizer.pick_captain(team_id)


@mcp.tool()
def chip_advice(team_id: int, gameweeks: int = 5) -> dict:
    """Chips left this half of the season, what each is worth, and whether to play or save it."""
    return optimizer.chip_advice(team_id, gameweeks=gameweeks)


@mcp.tool()
def refresh_data() -> dict:
    """Re-pull players, prices, injuries and fixtures from the FPL API."""
    result = ingest.sync_live()
    queries.invalidate_snapshot()
    return result


def http_app(token: str | None = None, allow_refresh: bool = False):
    """Read-only HTTP app at /mcp; with a token, every request except /health needs it."""
    if not allow_refresh:
        try:
            mcp.remove_tool("refresh_data")
        except ToolError:
            pass
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
