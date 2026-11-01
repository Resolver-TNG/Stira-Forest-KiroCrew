# Stira Engine Design

Status: implemented Python v1 contract. This document narrows the Crew design into the executable boundary of `engine.py`.

## 1. ADR-001 boundary

The engine is a collection of pure deterministic functions. It does not read files, clocks, environment variables, random sources, networks, KiroCrew APIs, or LLMs. Adapters must provide timestamps, IDs, parsed JSON documents, and IPD bytes explicitly. For identical values at the function boundary, outputs are identical.

Side effects belong outside the engine:

- `IPD.md` reads/writes and baseline persistence: hooks or MCP adapter.
- Current time and request/event ID generation: adapter.
- Purpose-drift notifications and breaker actions: adapter.
- Means-drift judgment and habituation reflection: LLM adapter/agentTurn cron.
- State, event, and knowledge persistence: storage adapter.

## 2. Compatibility with the TypeScript engine

The Python functions preserve these v1 behaviors:

- `apply_event`: `delta = trigger weight * intensity`, clamp `[0, 1]`, three-decimal rounding, unknown category as no-op.
- `decay`: linear `ratePerHour`, floor, three-decimal rounding.
- `check_breakers` / `get_fired_breakers`: strict below/above thresholds.
- `resolve_breakers`: auto/manual reset and default hysteresis `0.05`; unknown active IDs remain active.
- `detect_matrix_drift`: threshold deviations and non-zero sign flips.

The Python port strengthens validation: breaker rules can target only `stability` and `vigilance`, and clock-dependent outputs require an explicit timestamp.

## 3. Version and immutability contract

`SCHEMA_VERSION` is `1`. State, matrix, purpose-drift context/rule set, means-drift context, achievement, and XP policy reject any missing or different version with `SchemaVersionError`. This is fail-closed; the engine never guesses a migration.

Every returned state/report is newly allocated. Inputs are not mutated. The baseline matrix has no update function. `apply_mission_achievement` does not accept a matrix, so XP/growth cannot accidentally train or rewrite either `triggers` or `baseline`. Layer-B matrix learning remains prohibited.

## 4. Purpose drift: deterministic rule schema

Purpose drift is evaluated by `evaluate_purpose_drift(context, rule_set, evaluated_at=...)`. A matching rule is a mission violation. Rules are data only; Python expressions, callbacks, regex execution, and dynamic code are not accepted.

```json
{
  "version": 1,
  "crew": "iam-guardian",
  "rules": [
    {
      "id": "approval-required-for-change",
      "severity": "block",
      "message": "Infrastructure changes require explicit human approval.",
      "all": [
        {
          "field": "operation.kind",
          "operator": "in",
          "value": ["create", "update", "delete"]
        },
        {
          "field": "approval.granted",
          "operator": "not_eq",
          "value": true
        }
      ]
    }
  ]
}
```

The context is a versioned fact document supplied by the adapter:

```json
{
  "version": 1,
  "operation": {"kind": "update"},
  "approval": {"granted": false}
}
```

Supported operators are `eq`, `not_eq`, `in`, `not_in`, `exists`, `truthy`, and `falsy`. Dot-separated fields traverse nested mappings. All `all` conditions must match. If `any` is non-empty, at least one `any` condition must also match. Missing approval combined with `not_eq: true` fails closed and therefore matches the example rule. Duplicate rule IDs and unknown operators are rejected.

## 5. Means drift: adapter hook only

The engine does not decide means drift. `build_means_drift_request` produces a stable, versioned envelope for an external LLM adapter. The adapter must return/store its judgment elsewhere.

The request includes `habituationReviewRequired`. It becomes true when growth reaches the caller-provided reflection threshold (default `0.7`). This is only a deterministic scheduling signal implementing the growth/habituation offset; it is not an LLM judgment.

## 6. XP and growth

`apply_mission_achievement` accepts a versioned achievement and policy:

```json
{
  "version": 1,
  "achieved": true,
  "impact": 0.8,
  "timestamp": "2026-09-09T02:00:00Z"
}
```

```json
{
  "version": 1,
  "baseXp": 100,
  "growthPerXp": 0.001
}
```

Awarded XP is `round_half_up(baseXp * impact)`. It is added to top-level `state.xp`; `award * growthPerXp` is added to `state.params.growth` and clamped to `1.0`. An unachieved mission is a no-op. The function changes neither mission/IPD nor matrix baseline.

## 7. Core hash helper

`compute_core_hash` returns lowercase SHA-256 over exact bytes. A string is encoded as UTF-8; no whitespace or newline normalization occurs. Adapters read `IPD.md`, pass its bytes, compare with `core_hash_matches`, and own the human-report/re-baseline workflow. This exact-byte policy makes silent changes detectable and keeps file I/O outside ADR-001.

## 8. Determinism and tests

`tests/test_engine.py` covers the TypeScript-compatible calculations, input immutability, fixed-core breaker enforcement, declarative purpose drift, means-drift hook isolation, XP/growth without baseline mutation, core hashing, schema rejection, and repeated-call determinism.
