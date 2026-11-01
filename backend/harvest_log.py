"""Deterministic, bounded observability log for Stira harvest runs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
import json
import os
from pathlib import Path
import re
from typing import Any

from .core_init import _atomic_write
from .harvest import _validate_crew_id
from .locks import crew_transaction

HARVEST_LOG_SCHEMA = "stira.harvest.run.v1"
MAX_RUNS = 20
MAX_LINES = 200
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
_PHASES = {"started", "collected", "distilled", "completed", "failed"}
_TRIGGERS = {"manual", "scheduled"}
_IPD_STATUSES = {"present", "missing", "blank", "unreadable"}
_COUNT_FIELDS = {
    "sessionsListed", "sessionsMatched", "sessionsFetched", "sessionsFailed",
    "cronRunsMatched", "subagentsMatched", "candidates", "distilled",
    "accepted", "rejected",
}
_WRITE_FIELDS = {"written", "duplicateFingerprint", "duplicateSemantic", "failed"}
_LAYER_FIELDS = {"core", "knowledge", "log"}
_SKIP_REASONS = {
    "no_ipd", "no_sessions_matched", "no_activity", "not_mission_relevant",
    "unverified", "duplicate_fingerprint", "duplicate_semantic", "secret_or_pii",
    "below_threshold", "bounds_exceeded", "error",
}
_ALLOWED_FIELDS = {
    "schema", "runId", "phase", "startedAt", "finishedAt", "trigger", "model",
    "stiraHome", "crewsDiscovered", "ipdStatus", "windowStart", "windowEnd",
    "counts", "agentsSeen", "scores", "layerCounts", "writes", "skips", "errors",
    "notes",
}


def _bounded_string(record: MutableMapping[str, Any], field: str, maximum: int) -> None:
    """Drop a bad string field rather than raise (self-report, not a boundary)."""

    value = record.get(field)
    if value is None:
        return
    if not isinstance(value, str):
        record.pop(field, None)
    elif len(value) > maximum:
        record[field] = value[:maximum]


def _validate_record_shape(record: MutableMapping[str, Any]) -> None:
    # Observability records are self-reports, never a security boundary. A
    # loosely typed distill agent that puts a count at the top level instead of
    # under `counts`, or sends any field the schema does not know, must not lose
    # the whole phase record to a raise — that turned every schema drift into a
    # failed harvest (crewsDiscovered, agentsSeen, distilled-counts, …). Unknown
    # top-level fields are dropped; known fields are still normalized/validated.
    for unknown in set(record) - _ALLOWED_FIELDS:
        record.pop(unknown, None)
    if record.get("schema", HARVEST_LOG_SCHEMA) != HARVEST_LOG_SCHEMA:
        record.pop("schema", None)
    for field, maximum in (
        ("startedAt", 64), ("finishedAt", 64), ("model", 64),
        ("stiraHome", 260), ("windowStart", 64), ("windowEnd", 64),
        ("notes", 500),
    ):
        _bounded_string(record, field, maximum)
    if record.get("trigger") is not None and record["trigger"] not in _TRIGGERS:
        record.pop("trigger", None)
    if record.get("ipdStatus") is not None and record["ipdStatus"] not in _IPD_STATUSES:
        record.pop("ipdStatus", None)
    _normalize_crews_discovered(record)
    _integer_map(record, "counts", _COUNT_FIELDS)
    _integer_map(record, "layerCounts", _LAYER_FIELDS)
    _integer_map(record, "writes", _WRITE_FIELDS)
    _normalize_agents_seen(record)
    _normalize_scores(record)
    _normalize_skips(record)
    _normalize_errors(record)


def _integer_map(record: MutableMapping[str, Any], field: str, allowed: set[str]) -> None:
    """Normalize a nested integer map in place; never raise.

    Observability records are self-reports, not a security boundary. A loosely
    typed distill agent may send unknown keys or non-integer values; we keep
    only the allowed integer keys (coercing float/str), drop the rest, and
    remove the field entirely if nothing survives — so the phase record is
    never lost to a schema mismatch.
    """

    value = record.get(field)
    if value is None:
        return
    if not isinstance(value, Mapping):
        record.pop(field, None)
        return
    clean: dict[str, int] = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            clean[key] = item
        elif isinstance(item, float) and item.is_integer():
            clean[key] = int(item)
        elif isinstance(item, str) and item.strip().lstrip("+-").isdigit():
            clean[key] = int(item.strip())
    if clean:
        record[field] = clean
    else:
        record.pop(field, None)


def _normalize_crews_discovered(record: MutableMapping[str, Any]) -> None:
    discovered = record.get("crewsDiscovered")
    if discovered is None:
        return
    coerced: int | None = None
    if isinstance(discovered, bool):
        coerced = None
    elif isinstance(discovered, int):
        coerced = discovered
    elif isinstance(discovered, float) and discovered.is_integer():
        coerced = int(discovered)
    elif isinstance(discovered, str) and discovered.strip().lstrip("+").isdigit():
        coerced = int(discovered.strip())
    if coerced is not None and coerced >= 0:
        record["crewsDiscovered"] = coerced
    else:
        record.pop("crewsDiscovered", None)


def _normalize_agents_seen(record: MutableMapping[str, Any]) -> None:
    agents = record.get("agentsSeen")
    if agents is None:
        return
    if not isinstance(agents, list):
        record.pop("agentsSeen", None)
        return
    normalized: list[dict[str, Any]] = []
    for item in agents:
        if not isinstance(item, Mapping):
            continue
        agent = item.get("agent")
        count = item.get("count")
        if not isinstance(agent, str) or not agent.strip() or len(agent) > 128:
            continue
        if isinstance(count, bool):
            count = 0
        elif isinstance(count, float) and count.is_integer():
            count = int(count)
        elif isinstance(count, str) and count.strip().lstrip("+").isdigit():
            count = int(count.strip())
        if not isinstance(count, int) or count < 0:
            count = 0
        normalized.append({"agent": agent.strip(), "count": count})
    record["agentsSeen"] = normalized[:20]


def _normalize_scores(record: MutableMapping[str, Any]) -> None:
    scores = record.get("scores")
    if scores is None:
        return
    if not isinstance(scores, list):
        record.pop("scores", None)
        return
    clean = [
        float(s)
        for s in scores
        if not isinstance(s, bool) and isinstance(s, (int, float)) and 0 <= s <= 1
    ]
    if clean:
        record["scores"] = clean[:24]
    else:
        record.pop("scores", None)


def _normalize_skips(record: MutableMapping[str, Any]) -> None:
    skips = record.get("skips")
    if skips is None:
        return
    if not isinstance(skips, list):
        record.pop("skips", None)
        return
    clean: list[dict[str, Any]] = []
    for item in skips:
        if not isinstance(item, Mapping):
            continue
        reason = item.get("reason")
        if reason not in _SKIP_REASONS:
            continue  # unknown reason dropped, not fatal
        count = item.get("count")
        if isinstance(count, bool):
            count = 0
        elif isinstance(count, float) and count.is_integer():
            count = int(count)
        elif isinstance(count, str) and count.strip().lstrip("+").isdigit():
            count = int(count.strip())
        if not isinstance(count, int) or count < 0:
            count = 0
        entry: dict[str, Any] = {"reason": reason, "count": count}
        detail = item.get("detail")
        if isinstance(detail, str):
            entry["detail"] = detail[:200]
        clean.append(entry)
    record["skips"] = clean[:20]


def _normalize_errors(record: MutableMapping[str, Any]) -> None:
    errors = record.get("errors")
    if errors is None:
        return
    if not isinstance(errors, list):
        record.pop("errors", None)
        return
    clean: list[dict[str, Any]] = []
    for item in errors:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, Any] = {}
        for field, maximum in (("stage", 32), ("type", 64), ("message", 200)):
            value = item.get(field)
            if isinstance(value, str):
                entry[field] = value[:maximum]
        if entry:
            clean.append(entry)
    record["errors"] = clean[:10]


def run_log_path(data_dir: str | Path, crew_id: str) -> Path:
    """Return the app-owned run-log path after validating the crew identifier."""

    crew = _validate_crew_id(crew_id)
    return Path(data_dir).expanduser() / "crews" / crew / "harvest-runs.jsonl"


def _record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(value)
    run_id = record.get("runId")
    phase = record.get("phase")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("runId must match ^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
    if phase not in _PHASES:
        raise ValueError("phase must be started, collected, distilled, completed, or failed")
    _validate_record_shape(record)
    record["schema"] = HARVEST_LOG_SCHEMA
    return record


def _parse_line(line: str | Mapping[str, Any]) -> dict[str, Any] | None:
    if isinstance(line, Mapping):
        value: Any = line
    elif isinstance(line, str):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return None
    else:
        return None
    if not isinstance(value, Mapping):
        return None
    try:
        return _record(value)
    except ValueError:
        return None


def fold_runs(lines: Iterable[str | Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge phase records by runId and return runs newest-first.

    Later records win for fields they repeat while earlier phase details remain
    available. Malformed or schema-invalid lines are deliberately ignored.
    """

    folded: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in lines:
        record = _parse_line(line)
        if record is None:
            continue
        run_id = record["runId"]
        if run_id in folded:
            order.remove(run_id)
            folded[run_id].update(record)
        else:
            folded[run_id] = record
        order.append(run_id)
    return [folded[run_id] for run_id in reversed(order)]


def _bounded_lines(lines: list[str]) -> list[str]:
    valid: list[tuple[str, str]] = []
    run_order: list[str] = []
    for line in lines:
        record = _parse_line(line)
        if record is None:
            continue
        run_id = record["runId"]
        valid.append((run_id, line))
        if run_id in run_order:
            run_order.remove(run_id)
        run_order.append(run_id)
    keep_runs = set(run_order[-MAX_RUNS:])
    return [line for run_id, line in valid if run_id in keep_runs][-MAX_LINES:]


def _append_run_unlocked(
    data_dir: str | Path,
    crew_id: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Append while the caller holds the crew transaction lock."""

    normalized = _record(record)
    path = run_log_path(data_dir, crew_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded + "\n")
        handle.flush()
        os.fsync(handle.fileno())

    lines = path.read_text(encoding="utf-8").splitlines()
    bounded = _bounded_lines(lines)
    if len(lines) > MAX_LINES or len(fold_runs(lines)) > MAX_RUNS or len(bounded) != len(lines):
        content = "\n".join(bounded)
        _atomic_write(path, content + ("\n" if content else ""), mode=0o600)
    return normalized


def append_run(
    data_dir: str | Path,
    crew_id: str,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    """Append one bounded phase record under the crew transaction lock."""

    crew = _validate_crew_id(crew_id)
    with crew_transaction(data_dir, crew):
        return _append_run_unlocked(data_dir, crew, record)


def read_runs(
    data_dir: str | Path,
    crew_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Read and fold the latest bounded run records; a missing log is empty."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RUNS:
        raise ValueError(f"limit must be an integer between 1 and {MAX_RUNS}")
    path = run_log_path(data_dir, crew_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return fold_runs(lines)[:limit]
