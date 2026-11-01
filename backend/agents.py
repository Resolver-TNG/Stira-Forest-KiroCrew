"""Deterministic discovery of agent names present in local activity records."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .activity import parse_cron_jobs, parse_subagent_state

_SAFE_AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _safe_agent(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_AGENT_RE.fullmatch(candidate) else None


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def discover_agent_names(crew_home: Path) -> list[str]:
    """Return sorted, unique safe agent names found in supported activity files.

    Only the first line of each session JSONL file is read. Symlinks, malformed
    JSON, unreadable paths, and unsafe agent identifiers are ignored.
    """

    discovered: dict[str, str] = {}

    def add(value: Any) -> None:
        candidate = _safe_agent(value)
        if candidate is None:
            return
        folded = candidate.casefold()
        current = discovered.get(folded)
        if current is None or candidate < current:
            discovered[folded] = candidate

    sessions_dir = crew_home / "sessions"
    if not sessions_dir.is_symlink() and sessions_dir.is_dir():
        try:
            session_files = sorted(sessions_dir.glob("*.jsonl"))
        except OSError:
            session_files = []
        for session_path in session_files:
            if session_path.is_symlink() or not session_path.is_file():
                continue
            try:
                with session_path.open("r", encoding="utf-8") as handle:
                    first_line = handle.readline()
                record = json.loads(first_line)
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(record, Mapping):
                add(record.get("agent"))

    cron_payload = _read_json(crew_home / "crons.json")
    if cron_payload is not None:
        try:
            cron_jobs = parse_cron_jobs(cron_payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            cron_jobs = []
        for job in cron_jobs:
            add(job.get("agent_id"))
            for agent in job.get("agent_sequence", []):
                add(agent)

    subagents_dir = crew_home / "subagents"
    if not subagents_dir.is_symlink() and subagents_dir.is_dir():
        try:
            subagent_dirs = sorted(subagents_dir.iterdir())
        except OSError:
            subagent_dirs = []
        for subagent_dir in subagent_dirs:
            if subagent_dir.is_symlink() or not subagent_dir.is_dir():
                continue
            state_payload = _read_json(subagent_dir / "state.json")
            if state_payload is None:
                continue
            try:
                state = parse_subagent_state(state_payload, source_id=subagent_dir.name)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if state is not None:
                add(state.get("agent"))

    return sorted(discovered.values(), key=lambda name: (name.casefold(), name))
