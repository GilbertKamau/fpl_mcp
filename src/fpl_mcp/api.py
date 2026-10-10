# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
"""Thin client for the public (read-only, no login) FPL API."""

import asyncio
from typing import Any

import httpx

from .config import FPL_API

HEADERS = {"User-Agent": "fpl-mcp/0.1 (personal research tool)"}
TIMEOUT = httpx.Timeout(30.0)


def get_json(path: str) -> Any:
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        return _get(client, path)


def _get(client: httpx.Client, path: str) -> Any:
    resp = client.get(f"{FPL_API}/{path.lstrip('/')}")
    resp.raise_for_status()
    return resp.json()


def bootstrap() -> dict:
    return get_json("bootstrap-static/")


def fixtures() -> list[dict]:
    return get_json("fixtures/")


def entry(team_id: int) -> dict:
    """A manager's public profile (name, overall rank, current gameweek...)."""
    return get_json(f"entry/{team_id}/")


def entry_picks(team_id: int, gameweek: int) -> dict:
    """A manager's 15 picks for a gameweek. Only available once that gameweek's deadline has passed."""
    return get_json(f"entry/{team_id}/event/{gameweek}/picks/")


def entry_history(team_id: int) -> dict:
    return get_json(f"entry/{team_id}/history/")


async def element_summaries(player_ids: list[int], concurrency: int = 8) -> dict[int, dict]:
    """Fetch element-summary/{id}/ for many players, politely and with retries."""
    sem = asyncio.Semaphore(concurrency)
    results: dict[int, dict] = {}

    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:

        async def fetch(pid: int) -> None:
            async with sem:
                for attempt in range(4):
                    try:
                        resp = await client.get(f"{FPL_API}/element-summary/{pid}/")
                        if resp.status_code == 429 or resp.status_code >= 500:
                            raise httpx.HTTPStatusError("retry", request=resp.request, response=resp)
                        resp.raise_for_status()
                        results[pid] = resp.json()
                        return
                    except (httpx.HTTPStatusError, httpx.TransportError):
                        if attempt == 3:
                            raise
                        await asyncio.sleep(2**attempt)

        await asyncio.gather(*(fetch(pid) for pid in player_ids))
    return results
