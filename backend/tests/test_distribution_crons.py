from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest

from backend.cron_history import latest_cron_runs
from backend.engine import compute_core_hash
from backend.hooks import CREW_REGISTRY_KEY
from backend.knowledge import KnowledgeStore


@pytest.fixture
def cron_modules(monkeypatch):
    if "kiro_crew.cron_script" not in sys.modules:
        cron_script = types.ModuleType("kiro_crew.cron_script")
        cron_script.Report = type("Report", (Exception,), {})
        cron_script.Skip = type("Skip", (Exception,), {})
        cron_script.Done = type("Done", (Exception,), {})
        kiro_crew = types.ModuleType("kiro_crew")
        kiro_crew.__path__ = []
        kiro_crew.cron_script = cron_script
        monkeypatch.setitem(sys.modules, "kiro_crew", kiro_crew)
        monkeypatch.setitem(sys.modules, "kiro_crew.cron_script", cron_script)
    return {
        "core": importlib.import_module("backend.crons.core_hash_monitor"),
        "purpose": importlib.import_module("backend.crons.purpose_drift_check"),
        "knowledge": importlib.import_module("backend.crons.knowledge_integrity"),
        "harvest": importlib.import_module("backend.crons.knowledge_harvest"),
    }



class FakeStorage:
    def __init__(self, crews):
        self.values = {
            CREW_REGISTRY_KEY: {"version": 1, "crews": crews},
        }

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


def _crew(tmp_path: Path, name: str, content: str = "# P0\nKeep the core immutable.\n"):
    crew_dir = tmp_path / "crews" / name
    crew_dir.mkdir(parents=True)
    ipd = crew_dir / "IPD.md"
    ipd.write_text(content, encoding="utf-8")
    return crew_dir, compute_core_hash(ipd.read_bytes())


def test_core_hash_monitor_is_deterministic_and_detects_tamper(tmp_path: Path, cron_modules):
    inspect_cores = cron_modules["core"].inspect_cores
    crew_dir, expected = _crew(tmp_path, "dev-builder")
    storage = FakeStorage({"dev-builder": {"coreHash": expected}})

    healthy = inspect_cores(tmp_path, storage)
    assert healthy == inspect_cores(tmp_path, storage)
    assert healthy["findings"] == []

    tampered = crew_dir.joinpath("IPD.md")
    tampered.write_text("tampered\n", encoding="utf-8")
    changed = inspect_cores(tmp_path, storage)
    assert changed["findings"][0]["reason"] == "core hash mismatch"
    assert changed["findings"][0]["expectedHash"] == expected
    assert changed["findings"][0]["currentHash"] == compute_core_hash(tampered.read_bytes())


def test_purpose_drift_check_is_deterministic_and_flags_rule_match(tmp_path: Path, cron_modules):
    inspect_purpose_drift = cron_modules["purpose"].inspect_purpose_drift
    crew_dir, expected = _crew(tmp_path, "iam-guardian")
    storage = FakeStorage({"iam-guardian": {"coreHash": expected}})
    rules = {
        "version": 1,
        "crew": "iam-guardian",
        "rules": [{
            "id": "approval-required",
            "severity": "block",
            "message": "Approval is required",
            "all": [{"field": "operation.kind", "operator": "eq", "value": "delete"}],
            "any": [{"field": "approval.granted", "operator": "not_eq", "value": True}],
        }],
    }
    crew_dir.joinpath("purpose_rules.json").write_text(json.dumps(rules), encoding="utf-8")
    crew_dir.joinpath("events.jsonl").write_text(
        json.dumps({"id": "evt-1", "operation": {"kind": "delete"}, "approval": {"granted": False}}) + "\n",
        encoding="utf-8",
    )

    first = inspect_purpose_drift(tmp_path, storage, evaluated_at="2026-09-18T05:00:00Z")
    second = inspect_purpose_drift(tmp_path, storage, evaluated_at="2026-09-18T05:00:00Z")

    assert first == second
    assert first["violations"][0]["eventId"] == "evt-1"
    assert first["violations"][0]["violations"][0]["ruleId"] == "approval-required"
    assert first["requiresAdapterJudgment"] is False


def test_purpose_drift_invalid_rules_request_review_without_llm(tmp_path: Path, cron_modules):
    inspect_purpose_drift = cron_modules["purpose"].inspect_purpose_drift
    crew_dir, expected = _crew(tmp_path, "infra-operator")
    storage = FakeStorage({"infra-operator": {"coreHash": expected}})
    crew_dir.joinpath("purpose_rules.json").write_text('{"version":1,"rules":"bad"}', encoding="utf-8")
    crew_dir.joinpath("events.jsonl").write_text('{"id":"evt-1"}\n', encoding="utf-8")

    report = inspect_purpose_drift(tmp_path, storage, evaluated_at="2026-09-18T05:00:00Z")

    assert report["violations"] == []
    assert report["requiresAdapterJudgment"] is True
    assert report["reviewRequired"][0]["crewId"] == "infra-operator"


def test_knowledge_integrity_reuses_crawler_and_detects_missing_file(tmp_path: Path, cron_modules):
    inspect_knowledge = cron_modules["knowledge"].inspect_knowledge
    _, expected = _crew(tmp_path, "dev-builder")
    storage = FakeStorage({"dev-builder": {"coreHash": expected}})
    store = KnowledgeStore(tmp_path, "dev-builder")
    written = store.write_entry(content="Reusable finding.", score=0.8, title="Finding", tags=["dev"])

    healthy = inspect_knowledge(tmp_path, storage)
    assert healthy == inspect_knowledge(tmp_path, storage)
    assert healthy["findings"] == []

    Path(written["filepath"]).unlink()
    report = inspect_knowledge(tmp_path, storage)
    assert report["findings"][0]["missing"] == [
        {"id": written["id"], "relativePath": written["relativePath"]}
    ]


@pytest.mark.parametrize(
    ("module_key", "inspector_name", "report"),
    [
        ("core", "inspect_cores", {"checked": [], "findings": []}),
        (
            "purpose",
            "inspect_purpose_drift",
            {"crews": [], "violations": [], "reviewRequired": []},
        ),
        ("knowledge", "inspect_knowledge", {"crews": [], "findings": []}),
    ],
)
def test_monitor_run_records_skip_history(
    tmp_path: Path,
    monkeypatch,
    cron_modules,
    module_key,
    inspector_name,
    report,
):
    module = cron_modules[module_key]
    storage = FakeStorage({})
    monkeypatch.setattr(module, "app_storage", lambda: storage)
    monkeypatch.setattr(module, "stira_home", lambda: tmp_path)
    monkeypatch.setattr(module, inspector_name, lambda *args, **kwargs: report)
    ctx = SimpleNamespace(message="", notify=lambda _message: None)

    with pytest.raises(module.Skip):
        module.run(ctx)

    assert latest_cron_runs(storage)[module.CRON_NAME]["status"] == "skip"


def test_existing_harvest_run_records_ok_history_and_notifies(
    monkeypatch,
    cron_modules,
):
    module = cron_modules["harvest"]
    storage = FakeStorage({})
    notifications = []
    monkeypatch.setattr(module, "app_storage", lambda: storage)
    monkeypatch.setattr(module, "collect", lambda _ctx: {"candidate_count": 2})
    ctx = SimpleNamespace(message="", notify=notifications.append)

    with pytest.raises(module.Report):
        module.run(ctx)

    last_run = latest_cron_runs(storage)["knowledge-harvest"]
    assert last_run["status"] == "ok"
    assert last_run["summary"] == "knowledge harvest collected 2 candidates"
    assert notifications == ["knowledge harvest collected 2 candidates"]
