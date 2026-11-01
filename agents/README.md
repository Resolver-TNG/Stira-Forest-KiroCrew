# Agents

App-owned agent definitions. Each file is declared with an app-root-relative path in `app.json`.

- `stira-distiller.json` - the background distiller. It reaches only the restricted `/mcp/distiller` endpoint (five read/score/write tools) and excludes the general tools Kiro Crew would otherwise add.

Stira never overwrites or packages the user's own `~/.kiro/agents/` definitions.
