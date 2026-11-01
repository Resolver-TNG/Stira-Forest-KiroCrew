# Backend skeleton

Implementation is intentionally absent. Planned modules:

- `mcp_server.py`: stdio MCP server exposing per-crew `stira_*` tools.
- `routes.py`: `register_routes(ctx)` for onboarding and drift visibility APIs.
- `engine.py`: pure, deterministic decay/breaker/purpose-drift/XP logic; no LLM calls.
- `knowledge.py`: hierarchical knowledge index and Amygdala Pass routing.
- root `../hooks.py`: `on_startup(ctx)` and `on_shutdown(ctx)` lifecycle hooks. Startup must baseline non-empty `IPD.md` files and trigger onboarding for undefined cores.
- `crons/`: deterministic script-cron entry points.

All package references must remain app-root relative. No absolute local development path may enter the distribution manifest.
