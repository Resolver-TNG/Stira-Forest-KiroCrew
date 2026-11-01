"""Bounded AppStorage history for deterministic Stira cron runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any

CRON_HISTORY_KEY = "cron-run-history"
CRON_HISTORY_SCHEMA = "stira.cron-run-history.v1"
MAX_RUNS_PER_CRON = 20
MAX_SUMMARY_CHARS = 240
_VALID_STATUSES = frozenset({"ok", "skip", "error"})
_CRON_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(value: Mapping[str, Any]) -> dict[str, str] | None:
    name = value.get("name")
    status = value.get("status")
    at = value.get("at")
    summary = value.get("summary")
    if not isinstance(name, str) or not _CRON_NAME_RE.fullmatch(name):
        return None
    if status not in _VALID_STATUSES:
        return None
    if not isinstance(at, str) or not at:
        return None
    if not isinstance(summary, str):
        return None
    return {
        "name": name,
        "status": status,
        "at": at[:64],
        "summary": summary[:MAX_SUMMARY_CHARS],
    }


def read_cron_runs(storage: Any) -> list[dict[str, str]]:
    """Return normalized history oldest-first; corrupt storage is empty."""

    if storage is None:
        return []
    raw = storage.get(CRON_HISTORY_KEY)
    if not isinstance(raw, Mapping) or raw.get("schema") != CRON_HISTORY_SCHEMA:
        return []
    runs = raw.get("runs")
    if not isinstance(runs, list):
        return []
    return [record for item in runs if isinstance(item, Mapping) and (record := _record(item))]


def append_cron_run(
    storage: Any,
    cron_name: str,
    status: str,
    summary: str,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Append one run and retain at most 20 records for each cron."""

    record = _record(
        {"name": cron_name, "status": status, "at": _iso(now), "summary": summary}
    )
    if record is None:
        raise ValueError("invalid cron run record")
    runs = read_cron_runs(storage)
    runs.append(record)

    per_name: dict[str, int] = {}
    bounded_reversed: list[dict[str, str]] = []
    for item in reversed(runs):
        count = per_name.get(item["name"], 0)
        if count >= MAX_RUNS_PER_CRON:
            continue
        per_name[item["name"]] = count + 1
        bounded_reversed.append(item)
    bounded = list(reversed(bounded_reversed))
    storage.set(CRON_HISTORY_KEY, {"schema": CRON_HISTORY_SCHEMA, "runs": bounded})
    return record


def latest_cron_runs(storage: Any) -> dict[str, dict[str, str]]:
    """Return the newest UI projection keyed by logical cron name."""

    latest: dict[str, dict[str, str]] = {}
    for item in read_cron_runs(storage):
        latest[item["name"]] = {
            "status": item["status"],
            "at": item["at"],
            "summary": item["summary"],
        }
    return latest
