# fpl-mcp-server

Fantasy Premier League for any MCP client: live FPL data, points predictions for the next five
gameweeks, and transfer, captain and chip advice for any team. No Python or setup needed; this
package connects to the hosted server.

```json
{
  "mcpServers": {
    "fpl": { "command": "npx", "args": ["-y", "fpl-mcp-server"] }
  }
}
```

Works in Claude Desktop, Cursor, VS Code, Windsurf and other clients that run `npx` servers.
Set `FPL_MCP_URL` to use your own deployment, and `MCP_AUTH_TOKEN` if it needs a token.

Ask things like *"Suggest transfers for team 1234567"* or *"Best midfielders under £7m for the
next 3 gameweeks?"*. Your team ID is the number in your FPL website URL.

Source, self-hosting and model details: https://github.com/GilbertKamau/fpl_mcp

MIT License, Copyright (c) 2026 Gilbert Kamau. Not affiliated with the Premier League or Fantasy
Premier League.
