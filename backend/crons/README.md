# Cron entry-point skeleton

Planned deterministic, LLM-free script jobs:

- `core_hash_monitor.py:run`: verify each configured crew's `IPD.md` hash.
- `purpose_drift_check.py:run`: evaluate explicit mission/role rules.
- `knowledge_integrity.py:run`: detect and repair MISSING/UNINDEXED knowledge entries.

Means-drift reflection and habituation-offset impact review stay as manifest `message` jobs (agentTurn) because they require LLM judgment. Every job is initially paused with `enabled: false`.

Current platform gap: the manifest accepts these app-root-relative script declarations, but the current runtime only executes scripts under `~/.kiro/crew/crons/`. Implementation must resolve this without embedding an absolute path or modifying Crew configuration silently.
