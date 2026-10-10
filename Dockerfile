# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
# Remote MCP server (Streamable HTTP at /mcp). Read-only: data and predictions are written
# by `fpl-sync` / `fpl-model` running elsewhere (e.g. your own machine on a schedule).
FROM python:3.12-slim

# libgomp1 is needed by LightGBM's native library.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[supabase]"

RUN useradd --create-home app
USER app

ENV PORT=8000
EXPOSE 8000
HEALTHCHECK CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "fpl-mcp --http --host 0.0.0.0 --port ${PORT}"]
