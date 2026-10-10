# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
"""Settings loaded from the environment (or a .env file in the project root)."""

import os
from pathlib import Path
from urllib.parse import quote, unquote

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "fpl.db"

FPL_API = "https://fantasy.premierleague.com/api"
VAASTAV_RAW = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

FIRST_HISTORY_SEASON = 2016


def database_url() -> str:
    """SQLite by default; set DATABASE_URL to a Supabase/Postgres URL to use that instead."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + _encode_password(url[len(prefix):])
    return url


def _encode_password(rest: str) -> str:
    """Percent-encode the password; the host follows the last '@'."""
    credentials, sep, location = rest.rpartition("@")
    if not sep or ":" not in credentials:
        return rest
    user, password = credentials.split(":", 1)
    return f"{user}:{quote(unquote(password), safe='')}@{location}"


def season_label(start_year: int) -> str:
    """2025 -> '2025-26'."""
    return f"{start_year}-{(start_year + 1) % 100:02d}"
