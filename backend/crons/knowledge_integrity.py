"""Check Stira knowledge indexes, files, and lifecycle bounds."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path, PurePosixPath
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
from backend.knowledge import ARCHIVE_THRESHOLD, MAX_ARCHIVED, KnowledgeStore, crawl_consistency

CRON_NAME = "knowledge-integrity"
SCHEMA = "stira.knowledge-integrity.v1"
DEFAULTS = {"crews": []}
_REQUIRED_ENTRY_FIELDS = frozenset({"id", "title", "score", "relativePath", "archived"})


def _entry_issues(entry: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    missing = sorted(field for field in _REQUIRED_ENTRY_FIELDS if field not in entry)
    if missing:
        issues.append(f"missing fields: {', '.join(missing)}")
    relative = entry.get("relativePath")
    if not isinstance(relative, str) or not relative:
        issues.append("relativePath is invalid")
    else:
        path = PurePosixPath(relative.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            issues.append("relativePath escapes the knowledge store")
    score = entry.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
        issues.append("score is invalid")
    if entry.get("born_dead") is True or entry.get("bornDead") is True:
        issues.append("born-dead entry persisted in index")
    elif not entry.get("archived", False) and isinstance(score, (int, float)) and not isinstance(score, bool) and float(score) < ARCHIVE_THRESHOLD:
        issues.append("active entry is below born-dead threshold")
    if entry.get("archived", False) and not entry.get("archivedAt"):
        issues.append("archived entry has no archivedAt")
    return issues


def inspect_knowledge(
    home: str | Path,
    storage: Any,
    *,
    crews: Sequence[str] = (),
) -> dict[str, Any]:
    root = Path(home)
    reports: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for crew in configured_crews(root, storage, crews):
        try:
            store = KnowledgeStore(root, crew)
            entries = store.load_index()
            consistency = crawl_consistency(root, crew)
            archived_count = sum(bool(entry.get("archived", False)) for entry in entries)
            broken = [
                {"id": entry.get("id"), "issues": issues}
                for entry in entries
                if (issues := _entry_issues(entry))
            ]
            report = {
                "crewId": crew,
                "ok": consistency["ok"] and archived_count <= MAX_ARCHIVED and not broken,
                "indexedCount": consistency["indexedCount"],
                "fileCount": consistency["fileCount"],
                "missing": consistency["missing"],
                "unindexed": consistency["unindexed"],
                "archivedCount": archived_count,
                "maxArchived": MAX_ARCHIVED,
                "brokenEntries": broken,
            }
        except Exception as exc:
            report = {
                "crewId": crew,
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            }
        reports.append(report)
        if not report["ok"]:
            findings.append(report)

    return {"schema": SCHEMA, "crews": reports, "findings": findings}


def run(ctx: Any) -> None:
    storage = app_storage()
    try:
        config = load_config(ctx.message, DEFAULTS)
        report = inspect_knowledge(stira_home(), storage, crews=config["crews"])
    except Exception as exc:
        record_result(storage, CRON_NAME, "error", f"check failed: {type(exc).__name__}")
        raise

    if not report["findings"]:
        record_result(storage, CRON_NAME, "skip", f"{len(report['crews'])} crews consistent")
        raise Skip()

    summary = f"knowledge integrity alert: {len(report['findings'])}/{len(report['crews'])} crews"
    record_result(storage, CRON_NAME, "ok", summary)
    notify(ctx, summary)
    raise Report(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
