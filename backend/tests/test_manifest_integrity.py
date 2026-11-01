from __future__ import annotations

import json
from pathlib import Path

from backend.schedule import APP_CRONS


def test_manifest_schedule_and_script_implementations_are_consistent():
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "app.json").read_text(encoding="utf-8"))
    installed = json.loads((root / "app-crons.json").read_text(encoding="utf-8"))

    app_by_name = {item["name"]: item for item in manifest["crons"]}
    deployed_by_name = {
        item["name"].removeprefix("stira/"): item for item in installed
    }
    schedule_by_name = {item["name"]: item for item in APP_CRONS}

    expected = {
        "core-hash-monitor",
        "purpose-drift-check",
        "means-drift-reflection",
        "knowledge-integrity",
        "habituation-offset",
        "knowledge-harvest",
    }
    assert set(app_by_name) == expected
    assert set(deployed_by_name) == expected
    assert set(schedule_by_name) == expected

    for name in expected:
        app_entry = app_by_name[name]
        deployed = deployed_by_name[name]
        assert deployed["every"] == app_entry["every"] == schedule_by_name[name]["defaultIntervalSeconds"]
        assert deployed["script"] == app_entry.get("script", "")
        assert deployed["message"] == app_entry.get("message", "")
        if deployed["script"]:
            script_path, function = deployed["script"].split(":", 1)
            assert function == "run"
            assert (root / script_path).is_file()
        else:
            assert name in {"means-drift-reflection", "habituation-offset"}
