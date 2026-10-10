# fpl-mcp

An MCP server for Fantasy Premier League. It pulls live data from the official FPL API and
past seasons (2016-17 onward) from the [vaastav dataset](https://github.com/vaastav/Fantasy-Premier-League),
predicts every player's points for the next five gameweeks, and recommends squads, transfers,
captains and chips. Data lives in SQLite or Supabase.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[supabase,dev]"
.venv\Scripts\fpl-sync all        # load data (~15 min to Supabase the first time, ~3 min to SQLite)
.venv\Scripts\fpl-model all       # train the model and write predictions (~3-6 min the first time)
```

`fpl-sync` jobs:

| Job | What it does | Time |
|---|---|---|
| `live` | Players, prices, injuries, teams, gameweeks, fixtures | seconds |
| `current` | This season's per-match stats for every player (FPL API) | ~1 min |
| `history` | Past seasons from vaastav (`--seasons 2024-25` to pick some) | minutes, once |
| `all` | All of the above | |
| `weekly` | `live` + `current`, then retrain and refresh predictions | ~5 min |

`fpl-model` jobs: `evaluate` (test only), `train`, `predict`, `all` (train + predict).
Add `--refresh-cache` after re-loading history, so the local cache of past seasons is rebuilt.

## Database

SQLite at `data/fpl.db` by default. To use Supabase, copy `.env.example` to `.env` and paste your
Supabase **Session pooler** connection string as `DATABASE_URL` (the direct `db.<ref>.supabase.co`
host is IPv6-only). Special characters in the password are encoded automatically.
Tables are created automatically.

| Table | Contents |
|---|---|
| `players`, `teams`, `gameweeks`, `fixtures` | Current-season snapshot |
| `player_gameweeks` | One row per player per match, every season. `player_code` links a player across seasons |
| `season_teams` | Team id → name for each season |
| `predictions` | Expected points per player per upcoming fixture |
| `model_runs` | Every training run, its test results, and whether it was accepted |
| `sync_log` | When each sync ran |

Data quirks: 2019-20 gameweeks run to 47 (the COVID restart was numbered 39-47);
2024-25 includes `AM` (assistant manager) rows, which the model ignores; xG/xA exist from 2022-23 onward.

`data/` (SQLite backup, cache, logs), `models/` and `.env` are git-ignored, and a local
pre-commit hook refuses to commit them.

## Prediction model

LightGBM predicts each player's points per fixture from information available before kick-off:
rolling form over the last 3/6/12 matches (points, minutes, xG, xA, bonus, BPS, ICT, defensive
contributions...), price, position, home/away, and both clubs' recent goals and xG for/against.
A gameweek's expected points is the sum over its fixtures, so double gameweeks count twice.
Injury and suspension news scales the next gameweek's prediction by FPL's chance of playing.

**Testing.** Walk-forward: for each of the last three complete seasons, the model is trained only
on earlier seasons and predicts every fixture. The baseline is the player's average points over
their last 6 matches. Latest results:

| Season | Model MAE | Baseline MAE | Regular starters: model / baseline |
|---|---|---|---|
| 2023-24 | 0.955 | 0.999 | 2.21 / 2.40 |
| 2024-25 | 1.006 | 1.053 | 2.22 / 2.41 |
| 2025-26 | 0.982 | 1.055 | 2.29 / 2.55 |

A retrained model is only used if it beats the baseline and is no worse than the current model.
FPL points are very noisy (a 2-pointer and a 15-pointer can come from the same performance), so
treat predictions as estimates. Ask `model_report` for the current numbers.

## Optimizer

Integer programs solved with HiGHS (via PuLP) that follow FPL rules: 2 GKP / 5 DEF / 5 MID / 3 FWD,
max 3 per club, a valid formation, the budget, and −4 per transfer beyond the free ones. Points for
later gameweeks are discounted (×0.85 per week) because predictions further out are less reliable.

For a manager's team it uses public endpoints only (no login). Selling prices are estimated from
the manager's transfer history (FPL keeps half of any price rise), and free transfers from their
gameweek history (up to 5 banked, kept through Wildcard and Free Hit weeks). Transfers made since
the last deadline aren't public yet, so they aren't included.

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
| `predict_points` | Expected points for the next 1-5 gameweeks, ranked |
| `model_report` | How accurate the model is versus the baseline |
| `optimal_squad` | Best 15-man squad from scratch for a budget |
| `suggest_transfers` | Best transfers for a team, or advice to roll the transfer |
| `pick_captain` | Captain, vice-captain and the top 5 options |
| `chip_advice` | Which chips are left this half, what each is worth, play or save |
| `refresh_data` | Re-pull the live snapshot |

### Connecting

- **Claude Code**: `.mcp.json` in this folder registers the server; approve it when Claude Code
  starts here.
- **Claude Desktop / Cursor / VS Code**: add a server with command
  `C:\Users\user\Desktop\fpl_mcp\.venv\Scripts\python.exe` and args `["-m", "fpl_mcp.server"]`.

## Scheduled updates

`scripts\update.ps1` runs the updates and logs to `data\logs`. Two Windows scheduled tasks call it:

| Task | When | Mode |
|---|---|---|
| FPL MCP weekly update | Tuesday 08:00 | `weekly`: refresh data, retrain, predict |
| FPL MCP news refresh | Friday 08:00 | `news`: refresh prices/injuries, predict |

They run only while you're logged in. Manage them in Task Scheduler, or remove them with
`schtasks /Delete /TN "FPL MCP weekly update"` (and the same for "FPL MCP news refresh").

## Tests

```powershell
.venv\Scripts\python -m pytest
```

## Roadmap

1. ~~Data pipeline (FPL API + vaastav → SQLite/Supabase)~~
2. ~~Read-only MCP tools~~
3. ~~Points prediction: LightGBM with walk-forward validation against a rolling-average baseline~~
4. ~~Optimizer: best squad, transfers, captain, chip advice~~
5. ~~Scheduled updates (sync → retrain → predict)~~
6. HTTP hosting for ChatGPT and other remote clients
7. Optional: apply changes to your FPL team (preview-only by default)
