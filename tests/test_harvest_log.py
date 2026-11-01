from __future__ import annotations

import json

import pytest

from backend.harvest_log import (
    HARVEST_LOG_SCHEMA,
    MAX_LINES,
    MAX_RUNS,
    append_run,
    fold_runs,
    read_runs,
    run_log_path,
)


def test_append_and_read_fold_phases_newest_first(tmp_path):
    append_run(tmp_path, "dev-builder", {"runId": "manual-00000001", "phase": "started", "stiraHome": str(tmp_path)})
    append_run(tmp_path, "dev-builder", {"runId": "manual-00000001", "phase": "collected", "counts": {"sessionsMatched": 2}})
    append_run(tmp_path, "dev-builder", {"runId": "manual-00000001", "phase": "completed", "writes": {"written": 1}})
    append_run(tmp_path, "dev-builder", {"runId": "manual-00000002", "phase": "failed", "errors": [{"stage": "collect"}]})

    runs = read_runs(tmp_path, "dev-builder")

    assert [run["runId"] for run in runs] == ["manual-00000002", "manual-00000001"]
    completed = runs[1]
    assert completed["schema"] == HARVEST_LOG_SCHEMA
    assert completed["phase"] == "completed"
    assert completed["stiraHome"] == str(tmp_path)
    assert completed["counts"]["sessionsMatched"] == 2
    assert completed["writes"]["written"] == 1


def test_fold_runs_skips_corrupt_and_invalid_lines():
    runs = fold_runs([
        "not-json",
        json.dumps({"runId": "short", "phase": "started"}),
        json.dumps({"runId": "manual-00000001", "phase": "unknown"}),
        json.dumps({"runId": "manual-00000001", "phase": "started", "notes": "ok"}),
    ])

    assert runs == [{
        "runId": "manual-00000001",
        "phase": "started",
        "notes": "ok",
        "schema": HARVEST_LOG_SCHEMA,
    }]


def test_read_runs_skips_corrupt_final_line(tmp_path):
    path = run_log_path(tmp_path, "safe-crew")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"runId": "manual-00000001", "phase": "completed"}) + "\n{" ,
        encoding="utf-8",
    )

    assert read_runs(tmp_path, "safe-crew")[0]["phase"] == "completed"


def test_append_rotates_by_run_and_line_bounds(tmp_path):
    for run_index in range(MAX_RUNS + 3):
        run_id = f"manual-{run_index:08d}"
        for phase in ("started", "collected", "distilled", "completed"):
            append_run(tmp_path, "dev-builder", {"runId": run_id, "phase": phase})

    path = run_log_path(tmp_path, "dev-builder")
    lines = path.read_text(encoding="utf-8").splitlines()
    runs = read_runs(tmp_path, "dev-builder", limit=MAX_RUNS)

    assert len(lines) <= MAX_LINES
    assert len(runs) == MAX_RUNS
    assert runs[0]["runId"] == f"manual-{MAX_RUNS + 2:08d}"
    assert runs[-1]["runId"] == "manual-00000003"


def test_rejects_invalid_crew_run_phase_and_limit(tmp_path):
    with pytest.raises(ValueError):
        run_log_path(tmp_path, "../escape")
    with pytest.raises(ValueError):
        append_run(tmp_path, "safe", {"runId": "short", "phase": "started"})
    with pytest.raises(ValueError):
        append_run(tmp_path, "safe", {"runId": "manual-00000001", "phase": "other"})
    # Lenient observability policy: an unknown field is dropped (never leaks,
    # never raises) and the record still persists.
    append_run(tmp_path, "safe", {"runId": "manual-drop", "phase": "started", "rawTranscript": "secret"})
    kept = {r["runId"]: r for r in read_runs(tmp_path, "safe")}
    assert "manual-drop" in kept
    assert "rawTranscript" not in kept["manual-drop"]
    # An over-length error message is truncated, not rejected.
    append_run(tmp_path, "safe", {"runId": "manual-err", "phase": "failed",
                                   "errors": [{"message": "x" * 201}]})
    kept = {r["runId"]: r for r in read_runs(tmp_path, "safe")}
    assert len(kept["manual-err"]["errors"][0]["message"]) == 200
    with pytest.raises(ValueError):
        read_runs(tmp_path, "safe", limit=MAX_RUNS + 1)
