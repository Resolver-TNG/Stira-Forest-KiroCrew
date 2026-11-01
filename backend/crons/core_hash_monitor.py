"""Detect IPD core tampering for every configured Stira crew."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from backend.engine import compute_core_hash, core_hash_matches
from backend.hooks import CREW_REGISTRY_KEY

CRON_NAME = "core-hash-monitor"
SCHEMA = "stira.core-hash-monitor.v1"
DEFAULTS = {"crews": []}


def inspect_cores(
    home: str | Path,
    storage: Any,
    *,
    crews: Sequence[str] = (),
) -> dict[str, Any]:
    root = Path(home)
    registry = storage.get(CREW_REGISTRY_KEY) if storage is not None else None
    records = registry.get("crews") if isinstance(registry, Mapping) else {}
    if not isinstance(records, Mapping):
        records = {}
    checked: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for crew in configured_crews(root, storage, crews):
        record = records.get(crew)
        expected = record.get("coreHash") if isinstance(record, Mapping) else None
        path = root / "crews" / crew / "IPD.md"
        current: str | None = None
        reason: str | None = None
        try:
            content = path.read_bytes()
            current = compute_core_hash(content)
            if not content.strip():
                reason = "IPD.md empty"
            elif not isinstance(expected, str):
                reason = "core baseline missing"
            else:
                try:
                    if not core_hash_matches(content, expected):
                        reason = "core hash mismatch"
                except ValueError:
                    reason = "invalid expected core hash"
        except FileNotFoundError:
            reason = "IPD.md missing"
        except OSError as exc:
            reason = f"IPD.md unreadable: {type(exc).__name__}"

        item = {
            "crewId": crew,
            "healthy": reason is None,
            "reason": reason,
            "expectedHash": expected if isinstance(expected, str) else None,
            "currentHash": current,
        }
        checked.append(item)
        if reason is not None:
            findings.append(item)

    return {"schema": SCHEMA, "checked": checked, "findings": findings}


def run(ctx: Any) -> None:
    storage = app_storage()
    try:
        config = load_config(ctx.message, DEFAULTS)
        report = inspect_cores(stira_home(), storage, crews=config["crews"])
    except Exception as exc:
        record_result(storage, CRON_NAME, "error", f"monitor failed: {type(exc).__name__}")
        raise

    if not report["findings"]:
        record_result(storage, CRON_NAME, "skip", f"{len(report['checked'])} crews healthy")
        raise Skip()

    summary = f"core integrity alert: {len(report['findings'])}/{len(report['checked'])} crews unhealthy"
    record_result(storage, CRON_NAME, "ok", summary)
    notify(ctx, summary)
    raise Report(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
