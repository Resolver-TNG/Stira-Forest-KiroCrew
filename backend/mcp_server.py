"""Stira's per-crew MCP server, served over stdio.

The MCP process is separate from the gateway process, so it does not receive an
``AppContext`` object.  It resolves the app-owned data directory with
``app_data_dir('stira')`` and constructs ``AppStorage`` over that same directory;
this is the supported file-backed bridge to lifecycle hooks.  All engine calls
remain deterministic and free of I/O or LLM access (ADR-001).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import uuid
from typing import Any, Mapping, Sequence

from kiro_crew.apps.app_storage import AppStorage
from kiro_crew.atomic_write import atomic_write
from kiro_crew.mcp_shared import call_tool_with_logging, run_mcp_stdio_loop
from kiro_crew.validation import ValidationError, validate_mcp_tool_arguments

from .core_init import validate_crew_id
from .engine import (
    SCHEMA_VERSION,
    apply_event,
    apply_mission_achievement,
    compute_core_hash,
    core_hash_matches,
    decay,
    detect_matrix_drift,
    evaluate_purpose_drift,
    get_fired_breakers,
    resolve_breakers,
)
from .hooks import CREW_REGISTRY_KEY, ONBOARDING_KEY, ONBOARDING_QUESTION
from .harvest_log import append_run
from .knowledge import KnowledgeStore, suggest_score
from .sanitize import DATA_NOTICE, redact

MCP_MAX_SCORE = 0.79  # below the core-near layer (route_score >= 0.8)
# ── SEC-3/DATA-1 shared transaction boundary ───────────────────────────────
from .locks import registry_crew_transaction
# ── End SEC-3/DATA-1 shared transaction boundary ───────────────────────────

logger = logging.getLogger(__name__)

SERVER_NAME = "stira"
SERVER_VERSION = "0.1.0"
CORE_INTEGRITY_BREAKER = "core-integrity"
VALID_CATEGORIES = (
    "achievement",
    "challenge",
    "connection",
    "threat",
    "discovery",
    "correction",
    "routine",
    "idle",
)


def _crew_property() -> dict[str, Any]:
    return {
        "type": "string",
        "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
        "description": (
            "Crew identifier. Required unless STIRA_CREW_ID is set by a dedicated "
            "per-crew server launch."
        ),
    }


def _schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": {"crew_id": _crew_property(), **dict(properties)},
        "additionalProperties": False,
    }
    if required:
        result["required"] = list(required)
    return result


def _harvest_run_properties() -> dict[str, Any]:
    integer = {"type": "integer"}
    return {
        "runId": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
        },
        "phase": {
            "type": "string",
            "enum": ["started", "collected", "distilled", "completed", "failed"],
        },
        "startedAt": {"type": "string", "maxLength": 64},
        "finishedAt": {"type": "string", "maxLength": 64},
        "trigger": {"type": "string", "enum": ["manual", "scheduled"]},
        "model": {"type": "string", "maxLength": 64},
        "stiraHome": {"type": "string", "maxLength": 260},
        "crewsDiscovered": {"type": "integer", "minimum": 0},
        "ipdStatus": {
            "type": "string",
            "enum": ["present", "missing", "blank", "unreadable"],
        },
        "windowStart": {"type": "string", "maxLength": 64},
        "windowEnd": {"type": "string", "maxLength": 64},
        "counts": {
            "type": "object",
            "properties": {
                name: dict(integer)
                for name in (
                    "sessionsListed", "sessionsMatched", "sessionsFetched",
                    "sessionsFailed", "cronRunsMatched", "subagentsMatched",
                    "candidates", "distilled", "accepted", "rejected",
                )
            },
            "additionalProperties": False,
        },
        "agentsSeen": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "maxLength": 128},
                    "count": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        },
        "scores": {
            "type": "array",
            "maxItems": 24,
            "items": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "layerCounts": {
            "type": "object",
            "properties": {name: dict(integer) for name in ("core", "knowledge", "log")},
            "additionalProperties": False,
        },
        "writes": {
            "type": "object",
            "properties": {
                name: dict(integer)
                for name in ("written", "duplicateFingerprint", "duplicateSemantic", "failed")
            },
            "additionalProperties": False,
        },
        "skips": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "enum": [
                            "no_ipd", "no_sessions_matched", "no_activity",
                            "not_mission_relevant", "unverified", "duplicate_fingerprint",
                            "duplicate_semantic", "secret_or_pii", "below_threshold",
                            "bounds_exceeded", "core_integrity", "error",
                        ],
                    },
                    "count": {"type": "integer", "minimum": 0},
                    "detail": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
        },
        "errors": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "stage": {"type": "string", "maxLength": 32},
                    "type": {"type": "string", "maxLength": 64},
                    "message": {"type": "string", "maxLength": 200},
                },
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string", "maxLength": 500},
    }


def _list_tools() -> list[dict[str, Any]]:
    event_category = {"type": "string", "enum": list(VALID_CATEGORIES)}
    return [
        {
            "name": "stira_tick",
            "description": (
                "Refresh one crew's state, evaluate breakers and IPD core integrity, "
                "and return recent knowledge. Call at session start."
            ),
            "inputSchema": _schema(
                {"reason": {"type": "string", "maxLength": 200}}
            ),
        },
        {
            "name": "stira_status",
            "description": (
                "Read one crew's decayed state, breakers, and core health without "
                "persisting state changes."
            ),
            "inputSchema": _schema({}),
        },
        {
            "name": "stira_apply_event",
            "description": (
                "Apply a behavioral event to one crew using the immutable stimulus "
                "matrix, then persist state and audit data."
            ),
            "inputSchema": _schema(
                {
                    "category": event_category,
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "rationale": {"type": "string", "maxLength": 50},
                },
                ("category", "intensity"),
            ),
        },
        {
            "name": "stira_check_drift",
            "description": (
                "Check deterministic matrix drift and, when purpose_context is given, "
                "evaluate that context against the crew's purpose rules. Means-drift "
                "judgment remains in an LLM adapter, never in this engine."
            ),
            "inputSchema": _schema(
                {
                    "threshold": {"type": "number", "minimum": 0},
                    "purpose_context": {"type": "object"},
                }
            ),
        },
        {
            "name": "stira_list_events",
            "description": "List one crew's audit events in reverse chronological order.",
            "inputSchema": _schema(
                {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "since": {"type": "string"},
                }
            ),
        },
        {
            "name": "stira_record_harvest_run",
            "description": (
                "Append one bounded, self-reported harvest phase record without requiring "
                "an initialized crew runtime. Raw transcripts and secrets are not accepted."
            ),
            "inputSchema": _schema(
                _harvest_run_properties(),
                ("runId", "phase"),
            ),
        },
        {
            "name": "stira_get_harvest_candidates",
            "description": (
                "Return the deterministically pre-collected harvest candidate package "
                "(stira.harvest.input.v1) for one crew, as staged by the harvest route. "
                "Read-only."
            ),
            "inputSchema": _schema({}, ()),
        },
        {
            "name": "stira_suggest_score",
            "description": (
                "Deterministic importance-score recommendation using optional live crew "
                "params and caller-classified evidence strength."
            ),
            "inputSchema": _schema(
                {
                    "category": event_category,
                    "intensity": {"type": "number", "minimum": 0, "maximum": 1},
                    "breakerFired": {"type": "boolean"},
                    "stabilityDrop": {"type": "number", "minimum": 0},
                    "operatorEvidenceRatio": {
                        "type": "number", "minimum": 0, "maximum": 1,
                    },
                    "evidenceStrength": {
                        "type": "number", "minimum": 0, "maximum": 1,
                    },
                },
                ("category", "intensity"),
            ),
        },
        {
            "name": "stira_write_knowledge",
            "description": (
                "Write a scored knowledge entry into one crew's isolated Amygdala Pass "
                "store (core/knowledge/log), with bounded eviction."
            ),
            "inputSchema": _schema(
                {
                    # ── SEC-3 pinned core write basis ──────────────────────
                    "basis_core_hash": {
                        "type": "string",
                        "pattern": r"^[0-9a-fA-F]{64}$",
                        "description": (
                            "Approved core hash pinned by the harvest package; direct "
                            "callers must pass the current stira_check_core expectedHash."
                        ),
                    },
                    # ── End SEC-3 pinned core write basis ──────────────────
                    "content": {"type": "string", "minLength": 1},
                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 100},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 10,
                    },
                    "relatedEvents": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "ttlDays": {"type": "number", "exclusiveMinimum": 0},
                    "domain": {"type": "string"},
                    "summary": {"type": "string"},
                },
                ("basis_core_hash", "content", "score", "title", "tags"),
            ),
        },
        {
            "name": "stira_get_knowledge",
            "description": (
                "Search one crew's knowledge by text, tags, score, layer, and sort order."
            ),
            "inputSchema": _schema(
                {
                    "query": {"type": "string"},
                    "searchMode": {
                        "type": "string",
                        "enum": ["substring", "relevance"],
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "minScore": {"type": "number", "minimum": 0, "maximum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "sort": {
                        "type": "string",
                        "enum": ["score_desc", "date_desc", "date_asc"],
                    },
                    "layers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["core", "knowledge", "log"]},
                    },
                }
            ),
        },
        {
            "name": "stira_list_index",
            "description": (
                "Return a body-free table of contents of one crew's active knowledge "
                "(id/title/tags/domain/score/effectiveScore/layer/createdAt), ordered "
                "by effective score. The library model: read the index, then open only "
                "the relevant leaves with stira_get_knowledge or search_relevant."
            ),
            "inputSchema": _schema(
                {
                    "layers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["core", "knowledge", "log"]},
                    }
                }
            ),
        },
        {
            "name": "stira_check_core",
            "description": (
                "Verify one crew's current IPD.md bytes against its stored SHA-256 baseline."
            ),
            "inputSchema": _schema({}),
        },
    ]


def _data_dir() -> Path:
    override = os.environ.get("STIRA_HOME")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    from kiro_crew.apps.manager import app_data_dir

    return app_data_dir(SERVER_NAME)


def _storage() -> AppStorage:
    return AppStorage(SERVER_NAME, _data_dir())


def _resolve_crew_id(args: Mapping[str, Any]) -> str:
    value = args.get("crew_id") or os.environ.get("STIRA_CREW_ID", "")
    try:
        return validate_crew_id(value)
    except ValueError as exc:
        raise ValueError(
            f"{exc}; pooled MCP calls cannot infer an agent name"
        ) from exc


def _crew_dir(crew_id: str) -> Path:
    return _data_dir() / "crews" / crew_id


def _path(crew_id: str, filename: str) -> Path:
    return _crew_dir(crew_id) / filename


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(now: datetime | None = None) -> str:
    value = now or _utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Stira crew is not initialized ({path.name} missing); "
            "initialize it from the Stira Forest dashboard first"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    if payload.get("version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path.name} version mismatch: expected {SCHEMA_VERSION}, "
            f"got {payload.get('version')!r}"
        )
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", mode=0o600)


def _append_event(crew_id: str, payload: Mapping[str, Any]) -> None:
    event_path = _path(crew_id, "events.jsonl")
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _registry() -> dict[str, Any]:
    raw = _storage().get(CREW_REGISTRY_KEY)
    if not isinstance(raw, Mapping) or raw.get("version") != SCHEMA_VERSION:
        return {"version": SCHEMA_VERSION, "crews": {}}
    crews = raw.get("crews")
    return {
        "version": SCHEMA_VERSION,
        "crews": dict(crews) if isinstance(crews, Mapping) else {},
    }


def _save_registry(registry: Mapping[str, Any]) -> None:
    _storage().set(CREW_REGISTRY_KEY, dict(registry))


def _update_onboarding(registry: Mapping[str, Any]) -> None:
    crews = registry.get("crews", {})
    pending = sorted(
        crew_id
        for crew_id, record in crews.items()
        if isinstance(record, Mapping) and record.get("onboardingRequired") is True
    )
    _storage().set(
        ONBOARDING_KEY,
        {
            "version": SCHEMA_VERSION,
            "required": bool(pending),
            "question": ONBOARDING_QUESTION,
            "crews": pending,
            "reason": None,
        },
    )


def _core_record(crew_id: str) -> dict[str, Any] | None:
    record = _registry()["crews"].get(crew_id)
    return dict(record) if isinstance(record, Mapping) else None


def _check_core(crew_id: str) -> dict[str, Any]:
    ipd_path = _path(crew_id, "IPD.md")
    record = _core_record(crew_id)
    expected = record.get("coreHash") if record else None
    if not ipd_path.is_file():
        return {
            "ok": False,
            "crewId": crew_id,
            "initialized": record is not None,
            "healthy": False,
            "reason": "IPD.md missing",
            "expectedHash": expected,
            "currentHash": None,
            "ipdPath": str(ipd_path),
        }
    content = ipd_path.read_bytes()
    if not content.strip():
        return {
            "ok": False,
            "crewId": crew_id,
            "initialized": record is not None,
            "healthy": False,
            "reason": "IPD.md empty",
            "expectedHash": expected,
            "currentHash": compute_core_hash(content),
            "ipdPath": str(ipd_path),
        }
    current = compute_core_hash(content)
    if not isinstance(expected, str):
        return {
            "ok": False,
            "crewId": crew_id,
            "initialized": False,
            "healthy": False,
            "reason": "core baseline missing",
            "expectedHash": None,
            "currentHash": current,
            "ipdPath": str(ipd_path),
        }
    try:
        healthy = core_hash_matches(content, expected)
    except ValueError:
        healthy = False
    return {
        "ok": healthy,
        "crewId": crew_id,
        "initialized": True,
        "healthy": healthy,
        "reason": None if healthy else "core hash mismatch",
        "expectedHash": expected,
        "currentHash": current,
        "ipdPath": str(ipd_path),
    }


# ── SEC-3 live core integrity boundary ─────────────────────────────────────
def _require_live_core_basis(crew_id: str, basis_core_hash: Any) -> dict[str, Any]:
    if not isinstance(basis_core_hash, str) or len(basis_core_hash) != 64:
        raise ValueError("basis_core_hash must be a 64-character SHA-256 hex digest")
    try:
        bytes.fromhex(basis_core_hash)
    except ValueError as exc:
        raise ValueError("basis_core_hash must be hexadecimal") from exc

    report = _check_core(crew_id)
    approved_hash = report.get("expectedHash")
    if report.get("healthy") is not True:
        raise ValueError(
            f"core integrity check failed: {report.get('reason') or 'unhealthy core'}"
        )
    if not isinstance(approved_hash, str) or approved_hash.lower() != basis_core_hash.lower():
        raise ValueError("core basis is stale: approved revision changed during harvest")
    return report
# ── End SEC-3 live core integrity boundary ─────────────────────────────────


def _record_core_check(crew_id: str, report: Mapping[str, Any]) -> None:
    registry = _registry()
    record = registry["crews"].get(crew_id)
    if not isinstance(record, Mapping):
        return
    updated = dict(record)
    updated["coreHashCurrent"] = report.get("currentHash")
    updated["coreHashHealthy"] = report.get("healthy")
    updated["onboardingRequired"] = not bool(report.get("initialized"))
    registry["crews"][crew_id] = updated
    _save_registry(registry)
    _update_onboarding(registry)


def _ensure_initialized(crew_id: str) -> None:
    _read_json(_path(crew_id, "state.json"))
    _read_json(_path(crew_id, "matrix.json"))
    _read_json(_path(crew_id, "config.json"))


def _refresh_state(
    crew_id: str,
    *,
    persist: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current_time = now or _utc_now()
    state = _read_json(_path(crew_id, "state.json"))
    matrix = _read_json(_path(crew_id, "matrix.json"))
    config = _read_json(_path(crew_id, "config.json"))
    updated = _parse_iso(state.get("updated"), "state.updated")
    elapsed_ms = max(0.0, (current_time - updated).total_seconds() * 1000.0)
    decayed = elapsed_ms > 0
    next_state = (
        decay(state, elapsed_ms, config.get("decay", {}), _iso(current_time))
        if decayed
        else deepcopy(state)
    )
    rules = config.get("breakers", [])
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        raise ValueError("config.breakers must be an array")
    fired = get_fired_breakers(next_state, rules)
    resolution = resolve_breakers(next_state, rules)
    active = [item for item in resolution["stillActive"] if item != CORE_INTEGRITY_BREAKER]
    for result in fired:
        rule_id = result.get("ruleId")
        if isinstance(rule_id, str) and rule_id not in active:
            active.append(rule_id)
    core = _check_core(crew_id)
    if not core["healthy"]:
        active.append(CORE_INTEGRITY_BREAKER)
    next_state["activeBreakers"] = active
    if persist and (next_state != state):
        _write_json(_path(crew_id, "state.json"), next_state)
    return {
        "state": next_state,
        "matrix": matrix,
        "config": config,
        "elapsedMs": int(elapsed_ms),
        "decayed": decayed,
        "fired": fired,
        "core": core,
    }


def _tool_check_core_unlocked(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    report = _check_core(crew_id)
    _record_core_check(crew_id, report)
    return report


def _tool_check_core(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    with registry_crew_transaction(_data_dir(), crew_id):
        return _tool_check_core_unlocked(args)


def _tool_tick_unlocked(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    snapshot = _refresh_state(crew_id, persist=True)
    _record_core_check(crew_id, snapshot["core"])
    reason = args.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string")
    _append_event(
        crew_id,
        {
            "kind": "tick",
            "timestamp": snapshot["state"]["updated"],
            "reason": reason,
            "elapsedMs": snapshot["elapsedMs"],
            "paramsAfter": snapshot["state"]["params"],
            "activeBreakers": snapshot["state"].get("activeBreakers", []),
            "coreHealthy": snapshot["core"]["healthy"],
        },
    )
    top = KnowledgeStore(_data_dir(), crew_id).get_top(3)
    return {
        "ok": True,
        "kind": "stira-tick",
        "crewId": crew_id,
        "updated": snapshot["state"]["updated"],
        "params": snapshot["state"]["params"],
        "activeBreakers": snapshot["state"].get("activeBreakers", []),
        "firedBreakers": snapshot["fired"],
        "decay": {
            "decayed": snapshot["decayed"],
            "elapsedMs": snapshot["elapsedMs"],
            "reason": "forced-tick",
        },
        "core": snapshot["core"],
        "recentKnowledge": {"count": len(top), "topN": top},
        "dataNotice": DATA_NOTICE,
    }


def _tool_tick(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    with registry_crew_transaction(_data_dir(), crew_id):
        return _tool_tick_unlocked(args)


def _tool_status(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    snapshot = _refresh_state(crew_id, persist=False)
    state = snapshot["state"]
    return {
        "ok": True,
        "crewId": crew_id,
        "version": state["version"],
        "params": state["params"],
        "xp": state.get("xp", 0),
        "updated": state["updated"],
        "activeBreakers": state.get("activeBreakers", []),
        "core": snapshot["core"],
    }


def _tool_apply_event_unlocked(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    category = args.get("category")
    intensity = args.get("intensity")
    rationale = args.get("rationale")
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(VALID_CATEGORIES)}")
    if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
        raise ValueError("intensity must be a finite number in [0, 1]")
    intensity_value = float(intensity)
    if not math.isfinite(intensity_value) or not 0 <= intensity_value <= 1:
        raise ValueError("intensity must be a finite number in [0, 1]")
    if rationale is not None and (not isinstance(rationale, str) or len(rationale) > 50):
        raise ValueError("rationale must be a string of at most 50 characters")

    snapshot = _refresh_state(crew_id, persist=False)
    timestamp = _iso()
    event = {
        "id": f"evt-{uuid.uuid4().hex}",
        "timestamp": timestamp,
        "category": category,
        "intensity": intensity_value,
        "rationale": rationale,
        "source": "mcp",
    }
    next_state = apply_event(snapshot["state"], event, snapshot["matrix"])
    if category == "achievement":
        next_state = apply_mission_achievement(
            next_state,
            {
                "version": SCHEMA_VERSION,
                "achieved": True,
                "impact": intensity_value,
                "timestamp": timestamp,
            },
            snapshot["config"].get("xp", {}),
        )
    fired = get_fired_breakers(next_state, snapshot["config"].get("breakers", []))
    active = [
        rule_id
        for rule_id in next_state.get("activeBreakers", [])
        if rule_id != CORE_INTEGRITY_BREAKER
    ]
    for result in fired:
        rule_id = result.get("ruleId")
        if isinstance(rule_id, str) and rule_id not in active:
            active.append(rule_id)
    core = _check_core(crew_id)
    if not core["healthy"]:
        active.append(CORE_INTEGRITY_BREAKER)
    next_state["activeBreakers"] = active
    _write_json(_path(crew_id, "state.json"), next_state)
    _append_event(
        crew_id,
        {
            "event": event,
            "paramsAfter": next_state["params"],
            "firedBreakers": [item.get("ruleId") for item in fired],
            "coreHealthy": core["healthy"],
            "lazyDecay": {
                "decayed": snapshot["decayed"],
                "elapsedMs": snapshot["elapsedMs"],
            },
        },
    )
    return {
        "ok": True,
        "crewId": crew_id,
        "event": {
            "id": event["id"],
            "category": category,
            "intensity": intensity_value,
            "timestamp": timestamp,
        },
        "params": next_state["params"],
        "activeBreakers": active,
        "firedBreakers": fired,
        "core": core,
        "lazyDecay": {
            "decayed": snapshot["decayed"],
            "elapsedMs": snapshot["elapsedMs"],
        },
    }


def _tool_apply_event(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    with registry_crew_transaction(_data_dir(), crew_id):
        return _tool_apply_event_unlocked(args)


def _tool_check_drift(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    matrix = _read_json(_path(crew_id, "matrix.json"))
    config = _read_json(_path(crew_id, "config.json"))
    threshold = args.get("threshold", config.get("drift", {}).get("threshold", 0.5))
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("threshold must be a non-negative finite number")
    threshold_value = float(threshold)
    if not math.isfinite(threshold_value) or threshold_value < 0:
        raise ValueError("threshold must be a non-negative finite number")
    baseline = {
        "version": matrix["version"],
        "triggers": deepcopy(matrix.get("baseline", {})),
        "baseline": deepcopy(matrix.get("baseline", {})),
    }
    observed_at = _iso()
    matrix_report = detect_matrix_drift(
        matrix, baseline, threshold=threshold_value, observed_at=observed_at
    )
    purpose_report = None
    if "purpose_context" in args:
        context = args["purpose_context"]
        if not isinstance(context, Mapping):
            raise ValueError("purpose_context must be an object")
        rules = _read_json(_path(crew_id, "purpose_rules.json"))
        purpose_report = evaluate_purpose_drift(
            context, rules, evaluated_at=observed_at
        )
    return {
        "ok": True,
        "crewId": crew_id,
        "threshold": threshold_value,
        "drifted": matrix_report["drifted"] or bool(
            purpose_report and purpose_report["drifted"]
        ),
        "timestamp": observed_at,
        "matrix": matrix_report,
        "purpose": purpose_report,
        "means": {
            "evaluated": False,
            "requiresAdapterJudgment": True,
            "reason": "means drift requires LLM reflection in the adapter layer",
        },
    }


def _event_timestamp(entry: Mapping[str, Any]) -> datetime | None:
    value = entry.get("timestamp")
    if value is None and isinstance(entry.get("event"), Mapping):
        value = entry["event"].get("timestamp")
    try:
        return _parse_iso(value, "event.timestamp")
    except ValueError:
        return None


def _tool_list_events(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    limit = args.get("limit", 20)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer between 1 and 1000")
    since = None
    if args.get("since") is not None:
        since = _parse_iso(args["since"], "since")
    path = _path(crew_id, "events.jsonl")
    entries: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            timestamp = _event_timestamp(item)
            if since is not None and (timestamp is None or timestamp <= since):
                continue
            entries.append(item)
    selected = list(reversed(entries[-limit:]))
    return {
        "ok": True,
        "crewId": crew_id,
        "events": selected,
        "count": len(selected),
        "totalCount": len(entries),
    }


def _tool_record_harvest_run(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    record = {key: value for key, value in args.items() if key != "crew_id"}
    written = append_run(_data_dir(), crew_id, record)
    return {
        "ok": True,
        "crewId": crew_id,
        "runId": written["runId"],
        "phase": written["phase"],
        "recorded": True,
    }


def _tool_get_harvest_candidates(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    package = _storage().get(f"harvest-candidates-{crew_id}")
    if package is None:
        return {
            "ok": False,
            "crewId": crew_id,
            "found": False,
            "candidates": None,
        }
    # ── SEC-3 candidate-read integrity gate ─────────────────────────────────
    if not isinstance(package, Mapping):
        raise ValueError("staged harvest package must be an object")
    if package.get("crewId") != crew_id:
        raise ValueError("staged harvest package crew does not match the request")
    basis_hash = package.get("basisCoreHash")
    basis_revision = package.get("basisCoreRevisionId")
    if basis_revision != basis_hash:
        raise ValueError("staged harvest package core revision/hash pin is inconsistent")
    _require_live_core_basis(crew_id, basis_hash)
    # ── End SEC-3 candidate-read integrity gate ─────────────────────────────
    return {
        "ok": True,
        "crewId": crew_id,
        "found": True,
        "candidates": package,
    }


def _tool_suggest_score(args: dict[str, Any]) -> dict[str, Any]:
    params = None
    if args.get("crew_id") is not None or os.environ.get("STIRA_CREW_ID"):
        crew_id = _resolve_crew_id(args)
        _ensure_initialized(crew_id)
        params = _refresh_state(crew_id, persist=False)["state"]["params"]
    return suggest_score(
        args.get("category"),
        args.get("intensity"),
        breaker_fired=args.get("breakerFired", False),
        stability_drop=args.get("stabilityDrop", 0.0),
        params=params,
        operator_evidence_ratio=args.get("operatorEvidenceRatio", 0.0),
        evidence_strength=args.get("evidenceStrength", 0.0),
    )


def _tool_write_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    # ── SEC-3 knowledge-write integrity gate ────────────────────────────────
    root = _data_dir()
    with registry_crew_transaction(root, crew_id):
        basis_core_hash = args.get("basis_core_hash")
        report = _require_live_core_basis(crew_id, basis_core_hash)
        basis = str(report["expectedHash"]).lower()
        related_events = [
            event
            for event in args.get("relatedEvents", ())
            if isinstance(event, str) and event.strip()
        ]
        for marker in (
            f"core:basis-revision:{basis}",
            f"core:basis-hash:{basis}",
        ):
            if marker not in related_events:
                related_events.append(marker)
        store = KnowledgeStore(root, crew_id)
        # A model-written leaf never lands in the core-near layer by itself: the
        # score comes from the distiller's own judgement, and a core-near leaf is
        # surfaced at every session start. Promotion above this is a human act.
        requested = args.get("score")
        score = (
            min(float(requested), MCP_MAX_SCORE)
            if isinstance(requested, (int, float)) and not isinstance(requested, bool)
            else requested
        )
        result = store._write_entry_unlocked(
            content=args.get("content"),
            score=score,
            title=args.get("title"),
            tags=args.get("tags"),
            related_events=related_events,
            ttl_days=args.get("ttlDays"),
            domain=args.get("domain"),
            summary=args.get("summary"),
        )
    # ── End SEC-3 knowledge-write integrity gate ────────────────────────────
    return {
        "ok": True,
        "crewId": crew_id,
        # ── SEC-3 confirmed write basis ─────────────────────────────────────
        "basisCoreRevisionId": basis,
        "basisCoreHash": basis,
        # ── End SEC-3 confirmed write basis ─────────────────────────────────
        **result,
        "requestedScore": requested,
    }


def _tool_get_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    store = KnowledgeStore(_data_dir(), crew_id)
    if args.get("searchMode", "substring") == "relevance":
        result = store.search_relevant(
            args.get("query") or "",
            limit=args.get("limit", 5),
            layers=args.get("layers"),
            track_access=False,
        )
    else:
        result = store.query(
            query=args.get("query"),
            tags=args.get("tags"),
            min_score=args.get("minScore"),
            limit=args.get("limit", 5),
            sort=args.get("sort", "score_desc"),
            layers=args.get("layers"),
            track_access=True,
        )
    return {"ok": True, "crewId": crew_id, "dataNotice": DATA_NOTICE, **result}


def _tool_list_index(args: dict[str, Any]) -> dict[str, Any]:
    crew_id = _resolve_crew_id(args)
    _ensure_initialized(crew_id)
    result = KnowledgeStore(_data_dir(), crew_id).list_index(
        layers=args.get("layers"),
    )
    return {"ok": True, "crewId": crew_id, "dataNotice": DATA_NOTICE, **result}


_DISPATCH = {
    "stira_tick": _tool_tick,
    "stira_status": _tool_status,
    "stira_apply_event": _tool_apply_event,
    "stira_check_drift": _tool_check_drift,
    "stira_list_events": _tool_list_events,
    "stira_record_harvest_run": _tool_record_harvest_run,
    "stira_get_harvest_candidates": _tool_get_harvest_candidates,
    "stira_suggest_score": _tool_suggest_score,
    "stira_write_knowledge": _tool_write_knowledge,
    "stira_get_knowledge": _tool_get_knowledge,
    "stira_list_index": _tool_list_index,
    "stira_check_core": _tool_check_core,
}


def _validate_args(name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    args = raw_args or {}
    schema = _INPUT_SCHEMAS.get(name)
    if schema is None:
        raise ValidationError("Unknown tool", name)
    try:
        validate_mcp_tool_arguments(args, schema)
    except ValidationError as exc:
        raise ValidationError(f"{name} failed", str(exc)) from exc
    return args


def _call_tool_inner(name: str, args: dict[str, Any]) -> str:
    handler = _DISPATCH.get(name)
    if handler is None:
        return f"Error: Unknown tool: {name}"
    try:
        return json.dumps(handler(args or {}), ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001 - tool failures must not kill stdio
        logger.warning("[stira-mcp] %s failed: %s", name, exc)
        return f"Error: {name} failed: {exc}"


def _call_tool(name: str, args: dict[str, Any]) -> str:
    return call_tool_with_logging(
        name,
        args,
        _validate_args,
        _call_tool_inner,
        session_key=f"mcp_{SERVER_NAME}",
        downstream_service=f"kirocrew-{SERVER_NAME}",
    )


_INPUT_SCHEMAS: dict[str, Any] = {
    tool["name"]: tool.get("inputSchema") for tool in _list_tools()
}


def run_mcp_server() -> None:
    """Run Stira's stdio server; stdout is reserved for JSON-RPC."""
    logging.basicConfig(
        level=os.environ.get("KIROCREW_LOG_LEVEL", "WARNING"),
        stream=None,
    )
    run_mcp_stdio_loop(
        SERVER_NAME,
        SERVER_VERSION,
        _list_tools,
        _call_tool,
        advertise_caller_identity=True,
    )


if __name__ == "__main__":  # pragma: no cover
    run_mcp_server()
