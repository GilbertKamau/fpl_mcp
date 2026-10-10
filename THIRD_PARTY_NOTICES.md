# Third-party notices

fpl-mcp is licensed under the [MIT License](LICENSE), Copyright (c) 2026 Gilbert Kamau.
This file lists the third-party software and data it relies on. None of it is included in this
repository: dependencies are installed by pip, and data is downloaded at runtime into the
git-ignored `data/` folder.

## Software dependencies

| Package | Licence | Use |
|---|---|---|
| [mcp](https://github.com/modelcontextprotocol/python-sdk) | MIT | MCP server and HTTP transport |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | FPL API client |
| [pandas](https://github.com/pandas-dev/pandas) | BSD-3-Clause | Data processing |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause (and bundled permissive licences) | Numerics |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | MIT | Database access |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | `.env` settings |
| [LightGBM](https://github.com/microsoft/LightGBM) | MIT | Points model |
| [PuLP](https://github.com/coin-or/pulp) | MIT | Optimisation modelling |
| [HiGHS / highspy](https://github.com/ERGO-Code/HiGHS) | MIT | Optimisation solver |
| [psycopg](https://github.com/psycopg/psycopg) (optional `supabase` extra) | LGPL-3.0-only | PostgreSQL driver |
| [Uvicorn](https://github.com/encode/uvicorn), [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | HTTP server (via mcp) |
| [pytest](https://github.com/pytest-dev/pytest) (development only) | MIT | Tests |

psycopg is LGPL-3.0: fpl-mcp only imports it as an unmodified library, which the LGPL permits
in software under any licence. If you distribute a build that includes psycopg (for example a
Docker image), include psycopg's licence and let recipients replace that library.

Each package's full licence text is included in its own distribution.

## Data

**Historical gameweek data** comes from
[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League), whose code is
under the MIT License, Copyright (c) 2017-19 Vaastav Anand. That project states: *"The data
provided is property of http://fantasy.premierleague.com and https://understat.com. I don't own
any of the data."*

**Live data** comes from the public Fantasy Premier League API.

Fantasy Premier League data is the property of the Premier League. fpl-mcp does not claim any
rights to it, is not affiliated with or endorsed by the Premier League or Fantasy Premier League,
and the MIT License above covers the code only, not the data. Use the data in line with the
[Fantasy Premier League terms](https://fantasy.premierleague.com/help/terms).
