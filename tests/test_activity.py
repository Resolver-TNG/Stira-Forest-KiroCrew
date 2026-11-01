from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.activity import (
    attribute_cron_jobs,
    collect_activity_snapshot,
    filter_recent_cron_runs,
    filter_recent_subagents,
    parse_cron_history,
    parse_cron_jobs,
    parse_subagent_state,
)


NOW = datetime(2026, 9, 9, 4, 0, tzinfo=timezone.utc)


def test_parse_and_attribute_cron_jobs_by_exact_agent() -> None:
    jobs = parse_cron_jobs(
        {
            "version": 2,
            "jobs": [
                {"id": "job-a", "name": "Build", "agent_id": "dev-builder"},
                {"id": "job-b", "name": "Sequence", "agent_sequence": ["iam-guardian", "dev-builder"]},
                {"id": "job-c", "name": "Near miss", "agent_id": "dev-builder-helper"},
                {"id": "../escape", "agent_id": "dev-builder"},
            ],
        }
    )

    grouped = attribute_cron_jobs(jobs, ["dev-builder", "iam-guardian"])

    assert [job["id"] for job in grouped["dev-builder"]] == ["job-a", "job-b"]
    assert [job["id"] for job in grouped["iam-guardian"]] == ["job-b"]


def test_cron_history_parse_filters_24h_and_deduplicates() -> None:
    recent = (NOW - timedelta(hours=1)).timestamp()
    old = (NOW - timedelta(hours=25)).timestamp()
    payload = "\n".join(
        [
            json.dumps({"job_id": "job-a", "run_id": "run-1", "trigger": "scheduled", "started_at": recent - 5, "finished_at": recent, "duration_ms": 5000, "status": "success", "summary": "tests passed", "trace": "must be dropped"}),
            json.dumps({"job_id": "job-a", "run_id": "run-old", "finished_at": old, "status": "success", "summary": "old"}),
            "{partial",
        ]
    )
    records = parse_cron_history(payload)
    grouped = attribute_cron_jobs(
        [{"id": "job-a", "name": "Build", "agent_id": "dev-builder", "agent_sequence": []}],
        ["dev-builder"],
    )

    activity = filter_recent_cron_runs(
        [*records, records[0]], grouped, now=NOW, window_hours=24
    )

    assert len(activity["dev-builder"]) == 1
    run = activity["dev-builder"][0]
    assert run["summary"] == "tests passed"
    assert run["durationMs"] == 5000
    assert "trace" not in run
    assert len(run["fingerprint"]) == 64


def test_subagent_parse_filters_24h_and_exact_crew() -> None:
    recent = (NOW - timedelta(minutes=30)).timestamp()
    old = (NOW - timedelta(days=2)).timestamp()
    states = [
        parse_subagent_state(
            {"id": "sub-1", "agent": "DEV-BUILDER", "status": "completed", "task": "Implement activity", "last_tool": "pytest", "started": recent - 60, "updated_at": recent},
            result_text="All tests passed",
        ),
        parse_subagent_state(
            {"id": "sub-2", "agent": "dev-builder-helper", "status": "completed", "updated_at": recent},
            result_text="near miss",
        ),
        parse_subagent_state(
            {"id": "sub-3", "agent": "dev-builder", "status": "completed", "updated_at": old},
            result_text="old",
        ),
    ]

    activity = filter_recent_subagents(
        [state for state in states if state is not None],
        ["dev-builder"],
        now=NOW,
        window_hours=24,
    )

    assert len(activity["dev-builder"]) == 1
    assert activity["dev-builder"][0]["subagentId"] == "sub-1"
    assert activity["dev-builder"][0]["result"] == "All tests passed"


def test_collect_activity_snapshot_reads_only_supported_direct_sources(tmp_path: Path) -> None:
    recent = (NOW - timedelta(hours=2)).timestamp()
    (tmp_path / "crons.json").write_text(
        json.dumps({"version": 2, "jobs": [{"id": "job-a", "name": "Build", "agent_id": "dev-builder"}]}),
        encoding="utf-8",
    )
    history = tmp_path / "cron-history"
    history.mkdir()
    record = {"job_id": "job-a", "run_id": "run-1", "started_at": recent - 2, "finished_at": recent, "duration_ms": 2000, "status": "success", "summary": "build complete"}
    (history / "_index.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    (history / "job-a.jsonl").write_text(json.dumps({**record, "trace": "ignored"}) + "\n", encoding="utf-8")

    subagent = tmp_path / "subagents" / "sub-1"
    subagent.mkdir(parents=True)
    (subagent / "state.json").write_text(
        json.dumps({"id": "sub-1", "agent": "dev-builder", "status": "completed", "task": "Fix bug", "last_tool": "pytest", "started": recent - 10, "updated_at": recent}),
        encoding="utf-8",
    )
    (subagent / "result.txt").write_text("fixed and verified", encoding="utf-8")
    # A forbidden-source-shaped file must not be consulted by the collector.
    (tmp_path / "security_events.jsonl").write_text("not-json and unreadable-as-activity", encoding="utf-8")

    snapshot = collect_activity_snapshot(tmp_path, ["dev-builder"], now=NOW)

    assert snapshot["untrustedData"] is True
    crew = snapshot["crews"][0]
    assert crew["activityCount"] == 2
    assert len(crew["cronRuns"]) == 1  # index + per-job duplicate collapses
    assert crew["subagents"][0]["result"] == "fixed and verified"
    assert len(crew["fingerprint"]) == 64
