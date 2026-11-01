"""Lifecycle hooks for Stira's deterministic per-crew core baseline.

The hooks perform only local file and AppStorage I/O. They never invoke an LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import re
from typing import Any, Mapping

from .data_guard import (
    backup_crews_fail_soft,
    initialize_schema_fail_soft,
    recover_if_needed_fail_soft,
    validate_integrity_fail_soft,
)
from .engine import compute_core_hash, core_hash_matches
from .knowledge import KnowledgeStore, KnowledgeStoreError

SCHEMA_VERSION = 1
CREW_REGISTRY_KEY = "crew_registry"
ONBOARDING_KEY = "onboarding"
ONBOARDING_QUESTION = "このAIの芯は何ですか？"
_CREW_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Contains no AppContext or SDK capability. It exists only to make shutdown's
# cleanup explicit and testable without retaining privileges after disable.
_runtime_summary: dict[str, Any] | None = None

# Scheduled harvest remains disabled until App Platform exposes a supported
# cron-to-spawn bridge. Manual harvest uses ctx.spawn in backend.routes; hooks
# must not reach through CronSDK's private _cron service.


def _logger(ctx: Any) -> Any:
    return ctx.logger


def _mark_degraded(ctx: Any, issue: str) -> None:
    health = getattr(ctx, "health", None)
    if health is None or not hasattr(health, "mark_degraded"):
        return
    try:
        health.mark_degraded(issue)
    except Exception as exc:
        _logger(ctx).warning(
            "Stira could not update health state; startup will continue: %s", exc
        )


def _crews_root(ctx: Any) -> Path:
    configured = getattr(ctx, "config", {}).get("crews_dir")
    if configured:
        return Path(configured).expanduser()
    return Path(ctx.data_dir) / "crews"


def _valid_crew_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not _CREW_NAME_RE.fullmatch(name) or name in {".", ".."}:
        return None
    return name


def _configured_crew_names(
    ctx: Any,
    root: Path,
    existing: Mapping[str, Any],
) -> list[str]:
    names: set[str] = set()
    configured = getattr(ctx, "config", {}).get("crews", [])
    candidates = configured.keys() if isinstance(configured, Mapping) else configured
    if isinstance(candidates, (list, tuple, set)) or isinstance(configured, Mapping):
        for candidate in candidates:
            name = _valid_crew_name(candidate)
            if name:
                names.add(name)
            else:
                _logger(ctx).warning("Stira ignored invalid configured crew name: %r", candidate)

    for candidate in existing:
        name = _valid_crew_name(candidate)
        if name:
            names.add(name)

    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir():
                name = _valid_crew_name(child.name)
                if name:
                    names.add(name)
    return sorted(names)


def _read_registry(storage: Any) -> dict[str, Any]:
    raw = storage.get(CREW_REGISTRY_KEY)
    if not isinstance(raw, Mapping) or raw.get("version") != SCHEMA_VERSION:
        return {}
    crews = raw.get("crews")
    return dict(crews) if isinstance(crews, Mapping) else {}


def _baseline_crews(ctx: Any) -> dict[str, Any]:
    storage = ctx.storage
    root = _crews_root(ctx)
    existing = _read_registry(storage)
    crew_names = _configured_crew_names(ctx, root, existing)
    records: dict[str, dict[str, Any]] = {}
    onboarding_crews: list[str] = []

    for crew_name in crew_names:
        previous = existing.get(crew_name)
        record = dict(previous) if isinstance(previous, Mapping) else {}
        ipd_path = root / crew_name / "IPD.md"
        content: bytes | None = None
        if ipd_path.is_file():
            try:
                content = ipd_path.read_bytes()
            except OSError as exc:
                _logger(ctx).warning(
                    "Stira could not read IPD.md for crew %s: %s", crew_name, exc
                )

        undefined = content is None or not content.strip()
        expected_hash = record.get("coreHash")
        if undefined:
            onboarding_crews.append(crew_name)
            record.update(
                {
                    "name": crew_name,
                    "ipdPath": str(ipd_path),
                    "coreHashCurrent": None,
                    "coreHashHealthy": None,
                    "onboardingRequired": True,
                }
            )
        else:
            current_hash = compute_core_hash(content)
            if expected_hash is None:
                # Never trust the current IPD as its own baseline. A future
                # lineage ledger may rebuild coreHash only from a verifiable,
                # human-approved revision; the ledger itself is out of scope here.
                onboarding_crews.append(crew_name)
                record.pop("coreHash", None)
                record.update(
                    {
                        "name": crew_name,
                        "ipdPath": str(ipd_path),
                        "coreHashCurrent": current_hash,
                        "coreHashHealthy": False,
                        "onboardingRequired": True,
                    }
                )
                issue = (
                    f"Stira found IPD.md for crew {crew_name} without an approved "
                    "core hash; human approval is required"
                )
                _logger(ctx).warning(issue)
                _mark_degraded(ctx, issue)
                records[crew_name] = record
                continue
            if isinstance(expected_hash, str):
                try:
                    healthy = core_hash_matches(content, expected_hash)
                except ValueError:
                    healthy = False
                    _logger(ctx).warning(
                        "Stira found an invalid stored core hash for crew %s", crew_name
                    )
            else:
                healthy = False
                _logger(ctx).warning(
                    "Stira found a non-string stored core hash for crew %s", crew_name
                )
            record.update(
                {
                    "name": crew_name,
                    "ipdPath": str(ipd_path),
                    "coreHash": expected_hash,
                    "coreHashCurrent": current_hash,
                    "coreHashHealthy": healthy,
                    "onboardingRequired": False,
                }
            )
        records[crew_name] = record

    no_crews_configured = not crew_names
    onboarding = {
        "version": SCHEMA_VERSION,
        "required": bool(onboarding_crews) or no_crews_configured,
        "question": ONBOARDING_QUESTION,
        "crews": onboarding_crews,
        "reason": "no-crews-configured" if no_crews_configured else None,
    }
    storage.set(
        CREW_REGISTRY_KEY,
        {"version": SCHEMA_VERSION, "crews": records},
    )
    storage.set(ONBOARDING_KEY, onboarding)
    return {
        "crewCount": len(records),
        "onboardingRequired": onboarding["required"],
    }


def _sweep_faded_fail_soft(ctx: Any, data_dir: Path, crew_names: list[str]) -> int:
    """Age out faded leaves for every crew at startup, fully fail-soft.

    Write-driven fading only fires for crews that are still harvesting; a crew
    whose harvest has stopped would otherwise keep leaves on the branch
    forever. This startup sweep gives every crew at least one faded
    transition per app start. KnowledgeStore.archive_faded already runs inside
    the crew transaction and purges over the archived cap, so this only fans it
    out per crew. One crew's failure must never abort startup.
    """

    swept = 0
    for crew_name in crew_names:
        try:
            archived = KnowledgeStore(data_dir, crew_name).archive_faded()
            swept += len(archived)
        except (KnowledgeStoreError, OSError, ValueError, TypeError, KeyError) as exc:
            _logger(ctx).warning(
                "Stira could not sweep faded leaves for crew %s: %s", crew_name, exc
            )
    return swept


async def on_startup(ctx: Any) -> None:
    """Recover, validate, baseline, and snapshot Stira's persistent crew data."""
    global _runtime_summary

    logger = _logger(ctx)
    data_dir = Path(ctx.data_dir)
    mark_degraded = lambda issue: _mark_degraded(ctx, issue)

    # All filesystem work is bounded and fail-soft, but remains off the gateway
    # event loop. Recovery runs before baselining so a restored IPD/state is the
    # source of truth for the current startup.
    schema_version = await asyncio.to_thread(
        initialize_schema_fail_soft,
        data_dir,
        logger,
        mark_degraded,
    )

    storage = getattr(ctx, "storage", None)
    existing = (
        await asyncio.to_thread(_read_registry, storage)
        if storage is not None
        else {}
    )
    recovery = await asyncio.to_thread(
        recover_if_needed_fail_soft,
        data_dir,
        existing,
        logger,
        mark_degraded,
    )
    # Snapshot immediately after any recovery. This still runs if a later,
    # pre-existing baseline operation fails.
    backup = await asyncio.to_thread(backup_crews_fail_soft, data_dir, logger)

    if storage is None:
        issue = "Stira storage permission is unavailable; core hashes were not baselined"
        logger.warning(issue)
        _mark_degraded(ctx, issue)
        _runtime_summary = {
            "storageAvailable": False,
            "schemaVersion": schema_version,
            "restoredFrom": recovery.snapshot.name if recovery else None,
            "backupGeneration": backup.name if backup else None,
        }
        return

    # AppStorage and IPD files are synchronous. Keep their bounded local I/O off
    # the gateway event loop and well within the lifecycle's 30-second deadline.
    summary = await asyncio.to_thread(_baseline_crews, ctx)
    current_registry = await asyncio.to_thread(_read_registry, storage)

    # Age out faded leaves for every crew, including ones whose harvest has
    # stopped and would otherwise never trigger the write-driven fade path.
    # Bounded, off the event loop, fully fail-soft.
    faded_swept = await asyncio.to_thread(
        _sweep_faded_fail_soft,
        ctx,
        data_dir,
        _configured_crew_names(ctx, _crews_root(ctx), current_registry),
    )

    integrity = await asyncio.to_thread(
        validate_integrity_fail_soft,
        data_dir,
        current_registry,
        logger,
        mark_degraded,
    )

    _runtime_summary = {
        "storageAvailable": True,
        "harvestCronRegistered": False,
        "schemaVersion": schema_version,
        "restoredFrom": recovery.snapshot.name if recovery else None,
        "backupGeneration": backup.name if backup else None,
        "missingStateCrews": list(integrity.missing_state_crews) if integrity else [],
        "fadedSwept": faded_swept,
        **summary,
    }
    logger.info(
        "Stira startup complete: %d crew(s), onboarding_required=%s, "
        "harvest_cron=%s, backup=%s, restored_from=%s",
        summary["crewCount"],
        summary["onboardingRequired"],
        False,
        backup.name if backup else None,
        recovery.snapshot.name if recovery else None,
    )


async def on_shutdown(ctx: Any) -> None:
    """Release process-local runtime state; AppContext powers then expire."""
    global _runtime_summary
    _runtime_summary = None
    _logger(ctx).info("Stira shutdown complete; process-local capabilities released")


def _reset_for_tests() -> None:
    global _runtime_summary
    _runtime_summary = None
