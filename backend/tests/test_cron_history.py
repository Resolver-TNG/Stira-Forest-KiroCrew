from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.cron_history import (
    CRON_HISTORY_KEY,
    CRON_HISTORY_SCHEMA,
    MAX_RUNS_PER_CRON,
    append_cron_run,
    latest_cron_runs,
    read_cron_runs,
)


class FakeStorage:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


def test_cron_history_shape_and_latest_projection():
    storage = FakeStorage()
    now = datetime(2026, 9, 18, 5, 0, tzinfo=UTC)

    append_cron_run(storage, "core-hash-monitor", "skip", "all healthy", now=now)
    append_cron_run(storage, "core-hash-monitor", "ok", "one mismatch", now=now + timedelta(minutes=5))

    assert storage.values[CRON_HISTORY_KEY]["schema"] == CRON_HISTORY_SCHEMA
    assert latest_cron_runs(storage) == {
        "core-hash-monitor": {
            "status": "ok",
            "at": "2026-09-18T05:05:00Z",
            "summary": "one mismatch",
        }
    }


def test_cron_history_is_bounded_per_cron_and_truncates_summary():
    storage = FakeStorage()
    now = datetime(2026, 9, 18, tzinfo=UTC)
    for index in range(MAX_RUNS_PER_CRON + 3):
        append_cron_run(
            storage,
            "knowledge-integrity",
            "skip",
            ("x" * 300) + str(index),
            now=now + timedelta(minutes=index),
        )
    append_cron_run(storage, "knowledge-harvest", "error", "failed", now=now)

    runs = read_cron_runs(storage)
    integrity = [run for run in runs if run["name"] == "knowledge-integrity"]
    assert len(integrity) == MAX_RUNS_PER_CRON
    assert integrity[0]["at"] == "2026-09-18T00:03:00Z"
    assert len(integrity[-1]["summary"]) == 240
    assert len([run for run in runs if run["name"] == "knowledge-harvest"]) == 1
