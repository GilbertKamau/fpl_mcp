# fpl-mcp

An MCP server for Fantasy Premier League. It pulls live data from the official FPL API and
past seasons (2016-17 onward) from the [vaastav dataset](https://github.com/vaastav/Fantasy-Premier-League),
stores everything in SQLite or Supabase, and exposes it as tools any MCP client can call.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[supabase,dev]"
.venv\Scripts\fpl-sync all        # ~3-5 minutes the first time
```

`fpl-sync` jobs:

| Job | What it loads | Time |
|---|---|---|
| `live` | Players, prices, injuries, teams, gameweeks, fixtures | seconds |
| `current` | This season's per-match stats for every player (FPL API) | ~1 min |
| `history` | Past seasons from vaastav (`--seasons 2024-25` to pick some) | ~2 min |
| `all` | All of the above | |

Run `live` and `current` after each gameweek. History only needs loading once.

## Database

SQLite at `data/fpl.db` by default. To use Supabase, copy `.env.example` to `.env`, paste your
Supabase **Session pooler** connection string as `DATABASE_URL`, and run `fpl-sync all` again.
Tables are created automatically.

| Table | Contents |
|---|---|
| `players`, `teams`, `gameweeks`, `fixtures` | Current-season snapshot |
| `player_gameweeks` | One row per player per match, every season. `player_code` links a player across seasons |
| `season_teams` | Team id → name for each season |
| `sync_log` | When each sync ran |

Data quirks: 2019-20 gameweeks run to 47 (the COVID restart was numbered 39-47);
2024-25 includes `AM` (assistant manager) rows; xG/xA exist from 2022-23 onward.

## MCP tools

| Tool | What it does |
|---|---|
| `gameweek_status` | Current/next gameweek and deadline |
| `search_players` | Filter by position, team, price, minutes; sort by any stat |
| `get_player` | Stats, injury news, last 6 matches, next 5 gameweeks' fixtures |
| `compare_players` | 2-6 players side by side with per-90 and value numbers |
| `get_fixtures` | A team's next fixtures, or a whole gameweek |
| `team_summary` | Results, goals, clean sheets, fixture run, top players |
| `player_season_history` | A player's totals for every season since 2016-17 |
| `get_my_team` | Any manager's squad by team id (public, no login) |
| `refresh_data` | Re-pull the live snapshot |

### Connecting

- **Claude Code**: `.mcp.json` in this folder registers the server; approve it when Claude Code
  starts here.
- **Claude Desktop / Cursor / VS Code**: add a server with command
  `C:\Users\user\Desktop\fpl_mcp\.venv\Scripts\python.exe` and args `["-m", "fpl_mcp.server"]`.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Roadmap

1. ~~Data pipeline (FPL API + vaastav → SQLite/Supabase)~~
2. ~~Read-only MCP tools~~
3. Points prediction: rolling-average baseline, then LightGBM with walk-forward validation
4. Optimizer (PuLP): best squad, transfers, captain, chip advice
5. Weekly update job (sync → retrain → evaluate)
6. HTTP hosting for ChatGPT and other remote clients
7. Optional: apply changes to your FPL team (preview-only by default)
