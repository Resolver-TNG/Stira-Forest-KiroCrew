from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
from types import SimpleNamespace

from backend.hooks import CREW_REGISTRY_KEY, ONBOARDING_KEY
from backend.schedule import APP_CRONS, _set_enabled, get_schedule, update_schedule


@dataclass
class FakeSchedule:
    kind: str = "every"
    every_secs: int = 300


@dataclass
class FakeJob:
    id: str
    name: str
    enabled: bool = False
    schedule: FakeSchedule | None = None
    created_ts: float = 1_700_000_000.0
    last_run_ts: float | None = None

    def __post_init__(self):
        if self.schedule is None:
            self.schedule = FakeSchedule()


class FakeStorage:
    def __init__(self, core_defined: bool):
        self.values = {
            ONBOARDING_KEY: {"required": not core_defined},
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {"dev-builder": {"onboardingRequired": not core_defined}},
            },
        }

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


class FakeCron:
    def __init__(self):
        self.jobs = [
            FakeJob(
                id=f"job-{index}",
                name=f"stira/{definition['name']}",
                schedule=FakeSchedule(every_secs=definition["defaultIntervalSeconds"]),
            )
            for index, definition in enumerate(APP_CRONS)
        ]
        self.updates = []
        self.enables = []

    def list_jobs(self):
        return self.jobs

    async def update_job_async(self, job_id, **kwargs):
        self.updates.append((job_id, kwargs))
        job = next(job for job in self.jobs if job.id == job_id)
        if "every_secs" in kwargs:
            job.schedule = FakeSchedule(every_secs=kwargs["every_secs"])
        if "model" in kwargs:
            job.model = kwargs["model"]
        return job

    async def enable_job_async(self, job_id, *, enabled):
        self.enables.append((job_id, enabled))
        job = next(job for job in self.jobs if job.id == job_id)
        job.enabled = enabled
        return True


class FakeRequest:
    def __init__(self, cron_name="", body=None):
        self.match_info = {"cron_name": cron_name}
        self._body = body

    async def json(self):
        return self._body


def make_ctx(core_defined=True):
    return SimpleNamespace(
        name="stira",
        storage=FakeStorage(core_defined),
        cron=FakeCron(),
        logger=logging.getLogger("test.stira.schedule"),
    )


def payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_get_schedule_returns_all_registered_stira_crons():
    ctx = make_ctx(core_defined=True)
    ctx.cron.jobs[0].enabled = True

    response = asyncio.run(get_schedule(FakeRequest(), ctx))
    body = payload(response)

    assert response.status == 200
    assert body["coreDefined"] is True
    assert [cron["name"] for cron in body["crons"]] == [item["name"] for item in APP_CRONS]
    assert body["crons"][0]["enabled"] is True
    assert body["crons"][0]["nextRunAt"].endswith("Z")
    assert all(cron["available"] for cron in body["crons"])


def test_update_schedule_changes_interval_then_enables():
    ctx = make_ctx(core_defined=True)

    response = asyncio.run(
        update_schedule(
            FakeRequest(
                "purpose-drift-check",
                {"enabled": True, "intervalSeconds": 3600},
            ),
            ctx,
        )
    )
    body = payload(response)

    assert response.status == 200
    assert ctx.cron.updates == [("job-1", {"every_secs": 3600})]
    assert ctx.cron.enables == [("job-1", True)]
    assert body["cron"]["enabled"] is True
    assert body["cron"]["intervalSeconds"] == 3600


def test_update_schedule_disables_before_interval_change():
    ctx = make_ctx(core_defined=True)
    ctx.cron.jobs[5].enabled = True

    response = asyncio.run(
        update_schedule(
            FakeRequest("knowledge-harvest", {"enabled": False, "intervalSeconds": 86400}),
            ctx,
        )
    )

    assert response.status == 200
    assert ctx.cron.enables == [("job-5", False)]
    assert ctx.cron.updates == [
        ("job-5", {"every_secs": 86400, "model": "claude-haiku-4.5"})
    ]


def test_enable_knowledge_harvest_pins_low_consumption_model_first():
    ctx = make_ctx(core_defined=True)

    response = asyncio.run(
        update_schedule(FakeRequest("knowledge-harvest", {"enabled": True}), ctx)
    )

    assert response.status == 200
    assert ctx.cron.updates == [("job-5", {"model": "claude-haiku-4.5"})]
    assert ctx.cron.enables == [("job-5", True)]


def test_update_schedule_rejects_core_dependent_enable_before_onboarding():
    ctx = make_ctx(core_defined=False)

    response = asyncio.run(
        update_schedule(FakeRequest("knowledge-harvest", {"enabled": True}), ctx)
    )

    assert response.status == 409
    assert payload(response)["error"] == "core_required"
    assert ctx.cron.enables == []
    assert ctx.cron.updates == []


def test_update_schedule_validates_name_and_interval():
    ctx = make_ctx(core_defined=True)

    unknown = asyncio.run(update_schedule(FakeRequest("other", {"enabled": True}), ctx))
    invalid = asyncio.run(
        update_schedule(FakeRequest("core-hash-monitor", {"intervalSeconds": 30}), ctx)
    )

    assert unknown.status == 404
    assert invalid.status == 400



def test_set_enabled_uses_owned_host_service_fallback():
    class HostService:
        def __init__(self):
            self.calls = []

        async def enable_job_async(self, job_id, *, enabled):
            self.calls.append((job_id, enabled))
            return True

    class CurrentCronSdkShape:
        def __init__(self):
            self._cron = HostService()

        def _find_owned_job(self, job_id):
            return object() if job_id == "owned" else None

    sdk = CurrentCronSdkShape()

    asyncio.run(_set_enabled(sdk, "owned", True))

    assert sdk._cron.calls == [("owned", True)]


def test_get_schedule_includes_last_run_projection():
    ctx = make_ctx(core_defined=True)
    ctx.storage.set(
        "cron-run-history",
        {
            "schema": "stira.cron-run-history.v1",
            "runs": [
                {
                    "name": "core-hash-monitor",
                    "status": "skip",
                    "at": "2026-09-18T05:00:00Z",
                    "summary": "all healthy",
                }
            ],
        },
    )

    response = asyncio.run(get_schedule(FakeRequest(), ctx))
    body = payload(response)

    assert body["crons"][0]["lastRun"] == {
        "status": "skip",
        "at": "2026-09-18T05:00:00Z",
        "summary": "all healthy",
    }
    assert all(item["lastRun"] is None for item in body["crons"][1:])
