"""Evaluate deterministic purpose-drift rules against recent crew events."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

_APP_ROOT = Path(__file__).resolve().parents[2]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from kiro_crew.cron_script import Report, Skip

from backend.crons.common import (
    app_storage,
    configured_crews,
    load_config,
    notify,
    record_result,
    stira_home,
)
from backend.engine import SCHEMA_VERSION, evaluate_purpose_drift

CRON_NAME = "purpose-drift-check"
SCHEMA = "stira.purpose-drift-check.v1"
DEFAULTS = {"crews": [], "event_limit": 50}


def _iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _recent_events(path: Path, limit: int) -> list[dict[str, Any]]:
    selected: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            selected.append(value)
    return list(selected)


def inspect_purpose_drift(
    home: str | Path,
    storage: Any,
    *,
    crews: Sequence[str] = (),
    event_limit: int = 50,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    if isinstance(event_limit, bool) or not isinstance(event_limit, int) or not 1 <= event_limit <= 500:
        raise ValueError("event_limit must be an integer between 1 and 500")
    root = Path(home)
    timestamp = evaluated_at or _iso()
    crew_reports: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []

    for crew in configured_crews(root, storage, crews):
        crew_dir = root / "crews" / crew
        try:
            rules = _read_object(crew_dir / "purpose_rules.json")
            events = _recent_events(crew_dir / "events.jsonl", event_limit)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            review = {"crewId": crew, "reason": f"configuration unreadable: {type(exc).__name__}"}
            reviews.append(review)
            crew_reports.append({"crewId": crew, "eventsEvaluated": 0, "violations": [], "reviewRequired": True})
            continue

        crew_violations: list[dict[str, Any]] = []
        try:
            for event in events:
                context = dict(event)
                context["version"] = SCHEMA_VERSION
                result = evaluate_purpose_drift(context, rules, evaluated_at=timestamp)
                if result["drifted"]:
                    finding = {
                        "crewId": crew,
                        "eventId": (
                            event.get("event", {}).get("id")
                            if isinstance(event.get("event"), Mapping)
                            else event.get("id")
                        ),
                        "violations": result["violations"],
                    }
                    crew_violations.append(finding)
                    violations.append(finding)
        except (KeyError, TypeError, ValueError) as exc:
            review = {"crewId": crew, "reason": f"rules require review: {type(exc).__name__}"}
            reviews.append(review)

        crew_reports.append(
            {
                "crewId": crew,
                "eventsEvaluated": len(events),
                "violations": crew_violations,
                "reviewRequired": any(item["crewId"] == crew for item in reviews),
            }
        )

    return {
        "schema": SCHEMA,
        "evaluatedAt": timestamp,
        "crews": crew_reports,
        "violations": violations,
        "reviewRequired": reviews,
        "requiresAdapterJudgment": bool(reviews),
    }


def run(ctx: Any) -> None:
    storage = app_storage()
    try:
        config = load_config(ctx.message, DEFAULTS)
        report = inspect_purpose_drift(
            stira_home(),
            storage,
            crews=config["crews"],
            event_limit=config["event_limit"],
        )
    except Exception as exc:
        record_result(storage, CRON_NAME, "error", f"check failed: {type(exc).__name__}")
        raise

    finding_count = len(report["violations"]) + len(report["reviewRequired"])
    if finding_count == 0:
        event_count = sum(item["eventsEvaluated"] for item in report["crews"])
        record_result(storage, CRON_NAME, "skip", f"no drift in {event_count} recent events")
        raise Skip()

    summary = (
        f"purpose drift: {len(report['violations'])} violations, "
        f"{len(report['reviewRequired'])} review flags"
    )
    record_result(storage, CRON_NAME, "ok", summary)
    notify(ctx, summary)
    raise Report(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
