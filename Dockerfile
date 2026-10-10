# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Gilbert Kamau
# Read-only remote MCP server at /mcp.
FROM python:3.12-slim

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
