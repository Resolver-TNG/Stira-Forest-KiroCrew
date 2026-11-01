"""Shared adapters for Stira's deterministic script crons."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
from typing import Any

from backend.cron_history import append_cron_run
from backend.harvest import discover_crews
from backend.hooks import CREW_REGISTRY_KEY


def stira_home() -> Path:
    override = os.environ.get("STIRA_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    from kiro_crew.apps.manager import app_data_dir

    return app_data_dir("stira")


def app_storage() -> Any:
    from kiro_crew.apps.app_storage import AppStorage

    return AppStorage("stira", stira_home())


def configured_crews(
    home: str | Path,
    storage: Any,
    requested: Sequence[str] = (),
) -> list[str]:
    """Return validated crew ids from request, registry, and on-disk runtime."""

    disk = discover_crews(home)
    registry = storage.get(CREW_REGISTRY_KEY) if storage is not None else None
    records = registry.get("crews") if isinstance(registry, Mapping) else None
    registered = sorted(records) if isinstance(records, Mapping) else []
    available = sorted(set(disk).union(registered))
    if not requested:
        return available
    wanted = set(requested)
    return [crew for crew in available if crew in wanted]


def load_config(raw: Any, defaults: Mapping[str, Any]) -> dict[str, Any]:
    config = dict(defaults)
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        return config
    supplied = json.loads(text)
    if not isinstance(supplied, Mapping):
        raise ValueError("ctx.message must be a JSON object")
    unknown = sorted(set(supplied) - set(defaults))
    if unknown:
        raise ValueError(f"unknown configuration keys: {', '.join(unknown)}")
    config.update(supplied)
    crews = config.get("crews", [])
    if not isinstance(crews, list) or any(not isinstance(item, str) for item in crews):
        raise ValueError("crews must be a list of crew ids")
    return config


def record_result(storage: Any, cron_name: str, status: str, summary: str) -> None:
    append_cron_run(storage, cron_name, status, summary)


def notify(ctx: Any, summary: str) -> None:
    method = getattr(ctx, "notify", None)
    if callable(method):
        method(summary)
