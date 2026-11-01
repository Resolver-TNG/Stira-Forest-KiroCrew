# Stira Knowledge Store Design

## 1. Scope and invariants

`backend/knowledge.py` implements FR-6 as a deterministic, file-backed store. It does not call an LLM. The caller must always provide a `crew_id`; there is no global knowledge namespace.

Invariants:

- Storage is isolated at `<stira-root>/crews/<crew-id>/knowledge/`.
- `index.json` is the machine-readable source of truth; `index.md` is regenerated from it after each mutation.
- The index schema is versioned. Any version other than `1` is rejected rather than guessed or silently migrated.
- The existing 100-entry cap, effective-score decay, access bonus, and lowest-score eviction are preserved per crew.
- Writes use temporary files plus `os.replace` so readers never see a partially written file.
- This module is single-writer by contract, following ADR-003. Atomic replacement protects readers but is not a distributed lock.

## 2. Per-crew layout

```text
<stira-root>/
└── crews/
    └── <crew-id>/
        └── knowledge/
            ├── index.json
            ├── index.md
            ├── core/                       # score >= 0.8
            │   └── YYYY-MM-DD_<slug>_<id>.md
            ├── knowledge/                  # 0.6 <= score < 0.8
            │   └── <domain>/
            │       └── YYYY-MM-DD_<slug>_<id>.md
            └── logs/                       # score < 0.6
                └── YYYY-MM-DD/
                    └── YYYY-MM-DD_<slug>_<id>.md
```

The `core/` layer is *near the core*, not the immutable core itself. It contains high-value knowledge related to the crew mission. `IPD.md` remains physically separate and is never written by this module.

For reusable knowledge, `domain` is explicit when supplied. Otherwise the first tag becomes the domain; an untagged entry uses `general`.

## 3. Amygdala Pass

Routing is deterministic and happens when an entry is written:

| Score | Layer | Meaning |
|---|---|---|
| `>= 0.8` | `core` | mission-adjacent, long-lived knowledge |
| `>= 0.6` and `< 0.8` | `knowledge` | reusable domain knowledge |
| `< 0.6` | `log` | episodic/raw work log only |

`route_score(score)` is the pure routing primitive. `suggest_score(...)` ports the existing `stira_suggest_score` formula and now returns both `score` and `route`, so score suggestion and destination selection cannot diverge. `write_entry(...)` routes again from the accepted score; callers cannot force a contradictory layer.

## 4. Index contract

`index.json` has this shape:

```json
{
  "schemaVersion": 1,
  "entries": [
    {
      "id": "kn-...",
      "title": "...",
      "summary": "one-line summary",
      "score": 0.82,
      "tags": ["iam"],
      "createdAt": "2026-09-09T00:00:00Z",
      "lastAccessedAt": "2026-09-09T00:00:00Z",
      "accessCount": 0,
      "relatedEvents": [],
      "ttlDays": null,
      "layer": "core",
      "domain": "iam",
      "relativePath": "core/2026-09-09_example_abcd1234.md"
    }
  ]
}
```

`index.md` is the Lucia-style table of contents. It groups entries by layer and emits a relative link, one-line summary, tags, and original score. If no summary is supplied, the first non-empty content line is normalized and truncated to 160 characters.

## 5. Decay, UI projection, and eviction

The TypeScript calculations are preserved:

- With TTL: `decayFactor = max(0.1, 1 - ageDays / ttlDays)`.
- Without TTL: `decayFactor = max(0.3, 1 - ageDays / 365)`.
- `effectiveScore = round(score * decayFactor, 3)`.
- Eviction adds `0.2` when accessed within 7 days, `0.1` within 30 days, otherwise no bonus.
- When a write exceeds 100 entries, the lowest eviction score is removed from both index and filesystem.

Every query/top result includes a UI-ready projection:

```json
{
  "effectiveScore": 0.493,
  "decayFactor": 0.5,
  "expired": false,
  "ui": {
    "leafColorIntensity": 0.493,
    "leafOpacity": 0.493,
    "fadedFraction": 0.507
  }
}
```

The tree UI should bind leaf color directly to `ui.leafColorIntensity`. This keeps visualization free of duplicate decay logic. `leafOpacity` has a floor of `0.15` so a fully faded leaf remains selectable.

## 6. Query and access tracking

`KnowledgeStore.query(...)` retains the old title/tag search and adds one-line summary search and optional layer filtering. `min_score` applies to `effectiveScore`, not the original score. Supported sort orders are `score_desc`, `date_desc`, and `date_asc`.

Returned entries include content, metadata, effective score, decay factor, expiration, and UI projection. Selected entries have `lastAccessedAt` and `accessCount` updated after the read.

## 7. Consistency crawler

`crawl_consistency()` compares `index.json` with markdown files under `core/`, `knowledge/`, and `logs/`:

- `MISSING`: index entry exists but its `relativePath` does not.
- `UNINDEXED`: markdown file exists but no index entry references it.

The function is detection-only and returns structured data suitable for a zero-token script cron. It does not auto-delete or auto-adopt files because either action can destroy intent. A later repair adapter can apply a human-approved policy.

## 8. Adapter-facing API

Primary Python API:

- `KnowledgeStore(root, crew_id)`
- `write_entry(...)`
- `query(...)`
- `get_top(...)`
- `crawl_consistency()`
- `route_score(...)`
- `suggest_score(...)`
- `calc_effective_score(...)`
- `calc_eviction_score(...)`

Function wrappers and camelCase migration aliases are also exported for the future MCP adapter (`writeKnowledgeEntry`, `queryKnowledge`, `getTopKnowledge`, `calcEffectiveScore`, `calcEvictionScore`).
