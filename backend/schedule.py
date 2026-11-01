"""Deterministic schedule routes for Stira-owned cron jobs.

The handlers use the App Platform's app-scoped CronSDK.  They never invoke an
LLM, and they only resolve the six names installed from Stira's app-crons.json.
"""

from __future__ import annotations

from datetime import UTC, datetime
import time
from typing import Any, Mapping

from aiohttp import web

from .cron_history import latest_cron_runs
from .hooks import CREW_REGISTRY_KEY, ONBOARDING_KEY


APP_CRONS: tuple[dict[str, Any], ...] = (
    {"name": "core-hash-monitor", "label": "芯ハッシュ監視", "defaultIntervalSeconds": 300, "requiresCore": False},
    {"name": "purpose-drift-check", "label": "目的drift", "defaultIntervalSeconds": 900, "requiresCore": True},
    {"name": "means-drift-reflection", "label": "手段drift", "defaultIntervalSeconds": 3600, "requiresCore": True},
    {"name": "knowledge-integrity", "label": "ナレッジ整合性", "defaultIntervalSeconds": 21600, "requiresCore": False},
    {"name": "habituation-offset", "label": "慣れ相殺", "defaultIntervalSeconds": 604800, "requiresCore": True},
    {"name": "knowledge-harvest", "label": "収穫", "defaultIntervalSeconds": 21600, "requiresCore": True},
)
_CRON_BY_NAME = {definition["name"]: definition for definition in APP_CRONS}
_MIN_INTERVAL_SECONDS = 60
_MAX_INTERVAL_SECONDS = 31 * 24 * 60 * 60


def _core_defined(storage: Any) -> bool:
    """Return true only when at least one crew exists and all cores are defined."""
    if storage is None:
        return False

    onboarding = storage.get(ONBOARDING_KEY)
    if isinstance(onboarding, Mapping) and "required" in onboarding:
        return not bool(onboarding.get("required"))

    registry = storage.get(CREW_REGISTRY_KEY)
    crews = registry.get("crews") if isinstance(registry, Mapping) else None
    if not isinstance(crews, Mapping) or not crews:
        return False
    return all(
        isinstance(record, Mapping) and not bool(record.get("onboardingRequired"))
        for record in crews.values()
    )


def _registered_name(ctx: Any, logical_name: str) -> str:
    return f"{getattr(ctx, 'name', 'stira')}/{logical_name}"


def _owned_jobs(ctx: Any) -> dict[str, Any]:
    cron = getattr(ctx, "cron", None)
    if cron is None:
        raise RuntimeError("cron permission is unavailable")
    return {job.name: job for job in cron.list_jobs()}


def _next_run_timestamp(job: Any) -> float | None:
    if not bool(getattr(job, "enabled", False)):
        return None
    schedule = getattr(job, "schedule", None)
    if getattr(schedule, "kind", None) != "every":
        return None
    interval = getattr(schedule, "every_secs", None)
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        return None
    base = getattr(job, "last_run_ts", None)
    if not isinstance(base, (int, float)):
        base = getattr(job, "created_ts", None)
    if not isinstance(base, (int, float)):
        return None
    return max(time.time(), float(base) + float(interval))


def _serialize_cron(
    ctx: Any,
    definition: Mapping[str, Any],
    job: Any | None,
    core_defined: bool,
    last_run: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    interval = definition["defaultIntervalSeconds"]
    if job is not None:
        schedule = getattr(job, "schedule", None)
        actual_interval = getattr(schedule, "every_secs", None)
        if isinstance(actual_interval, int) and not isinstance(actual_interval, bool):
            interval = actual_interval
    next_run_ts = _next_run_timestamp(job) if job is not None else None
    return {
        "name": definition["name"],
        "registeredName": _registered_name(ctx, definition["name"]),
        "label": definition["label"],
        "available": job is not None,
        "enabled": bool(getattr(job, "enabled", False)) if job is not None else False,
        "intervalSeconds": interval,
        "nextRunAt": (
            datetime.fromtimestamp(next_run_ts, tz=UTC).isoformat().replace("+00:00", "Z")
            if next_run_ts is not None
            else None
        ),
        "requiresCore": bool(definition["requiresCore"]),
        "guarded": bool(definition["requiresCore"]) and not core_defined,
        "lastRun": dict(last_run) if isinstance(last_run, Mapping) else None,
    }


def _schedule_payload(ctx: Any) -> dict[str, Any]:
    storage = getattr(ctx, "storage", None)
    core_defined = _core_defined(storage)
    last_runs = latest_cron_runs(storage)
    jobs = _owned_jobs(ctx)
    crons = [
        _serialize_cron(
            ctx,
            definition,
            jobs.get(_registered_name(ctx, definition["name"])),
            core_defined,
            last_runs.get(definition["name"]),
        )
        for definition in APP_CRONS
    ]
    return {"coreDefined": core_defined, "crons": crons}


async def _set_enabled(cron: Any, job_id: str, enabled: bool) -> None:
    """Use a public SDK method when available, with a bounded host fallback.

    KiroCrew's current CronSDK exposes update_job_async but not pause/resume.
    The fallback repeats CronSDK's ownership check before reaching its wrapped
    CronService.  Keeping this compatibility seam here makes it removable as
    soon as the host publishes enable_job_async on CronSDK.
    """
    public_method = getattr(cron, "enable_job_async", None)
    if callable(public_method):
        changed = await public_method(job_id, enabled=enabled)
    else:
        find_owned = getattr(cron, "_find_owned_job", None)
        service = getattr(cron, "_cron", None)
        service_method = getattr(service, "enable_job_async", None)
        if not callable(find_owned) or find_owned(job_id) is None:
            raise PermissionError("cron job is not owned by Stira")
        if not callable(service_method):
            raise RuntimeError("cron pause/resume is unavailable")
        changed = await service_method(job_id, enabled=enabled)
    if not changed:
        raise LookupError("cron job no longer exists")


async def get_schedule(_request: web.Request, ctx: Any) -> web.Response:
    """Return enabled state, interval, and next run for all six Stira crons."""
    try:
        return web.json_response(_schedule_payload(ctx))
    except (OSError, RuntimeError, ValueError) as exc:
        ctx.logger.warning("Stira could not list its cron schedule: %s", exc)
        return web.json_response({"error": "schedule_unavailable"}, status=503)


async def update_schedule(request: web.Request, ctx: Any) -> web.Response:
    """Pause/resume one Stira cron and/or update its interval."""
    cron_name = request.match_info.get("cron_name", "")
    definition = _CRON_BY_NAME.get(cron_name)
    if definition is None:
        return web.json_response(
            {"error": "cron_not_found", "cronName": cron_name},
            status=404,
        )

    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"error": "invalid_json"}, status=400)
    if not isinstance(body, Mapping):
        return web.json_response({"error": "invalid_request"}, status=400)

    has_enabled = "enabled" in body
    has_interval = "intervalSeconds" in body
    enabled = body.get("enabled")
    interval = body.get("intervalSeconds")
    if not has_enabled and not has_interval:
        return web.json_response(
            {"error": "invalid_request", "message": "enabled or intervalSeconds is required"},
            status=400,
        )
    if has_enabled and not isinstance(enabled, bool):
        return web.json_response(
            {"error": "invalid_request", "message": "enabled must be a boolean"},
            status=400,
        )
    if has_interval and (
        isinstance(interval, bool)
        or not isinstance(interval, int)
        or not _MIN_INTERVAL_SECONDS <= interval <= _MAX_INTERVAL_SECONDS
    ):
        return web.json_response(
            {
                "error": "invalid_request",
                "message": f"intervalSeconds must be an integer within {_MIN_INTERVAL_SECONDS}..{_MAX_INTERVAL_SECONDS}",
            },
            status=400,
        )

    core_defined = _core_defined(getattr(ctx, "storage", None))
    if enabled is True and definition["requiresCore"] and not core_defined:
        return web.json_response(
            {
                "error": "core_required",
                "cronName": cron_name,
                "message": "Define every configured crew core before enabling this schedule.",
            },
            status=409,
        )

    cron = getattr(ctx, "cron", None)
    if cron is None:
        return web.json_response({"error": "schedule_unavailable"}, status=503)

    try:
        jobs = _owned_jobs(ctx)
        job = jobs.get(_registered_name(ctx, cron_name))
        if job is None:
            return web.json_response(
                {"error": "cron_not_registered", "cronName": cron_name},
                status=404,
            )

        # Disable first for fail-safe shutdown; enable only after a valid
        # interval update has persisted.
        if enabled is False:
            await _set_enabled(cron, job.id, False)
        update_fields: dict[str, Any] = {}
        if has_interval:
            update_fields["every_secs"] = interval
        # app.json CronEntry has no model field. Pin the semantic harvest model
        # through the cron update path before any UI-driven enable can fire it.
        if cron_name == "knowledge-harvest":
            update_fields["model"] = "claude-haiku-4.5"
        if update_fields:
            updated = await cron.update_job_async(job.id, **update_fields)
            if updated is None:
                raise LookupError("cron job no longer exists")
        if enabled is True:
            await _set_enabled(cron, job.id, True)

        refreshed = _owned_jobs(ctx).get(_registered_name(ctx, cron_name))
        if refreshed is None:
            raise LookupError("cron job no longer exists")
        return web.json_response(
            {
                "coreDefined": core_defined,
                "cron": _serialize_cron(
                    ctx,
                    definition,
                    refreshed,
                    core_defined,
                    latest_cron_runs(getattr(ctx, "storage", None)).get(cron_name),
                ),
            }
        )
    except PermissionError as exc:
        ctx.logger.warning("Stira rejected cron ownership violation for %s: %s", cron_name, exc)
        return web.json_response({"error": "cron_forbidden"}, status=403)
    except LookupError:
        return web.json_response(
            {"error": "cron_not_registered", "cronName": cron_name},
            status=404,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        ctx.logger.warning("Stira could not update cron %s: %s", cron_name, exc)
        return web.json_response({"error": "schedule_update_failed"}, status=503)
