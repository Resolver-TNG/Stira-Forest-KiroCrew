import json
from pathlib import Path

from backend.agents import discover_agent_names


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_discover_agent_names_aggregates_deduplicates_skips_and_sorts(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "conductor.jsonl").write_text(
        json.dumps({"agent": "default"}) + "\n" + "{not parsed after first line}\n",
        encoding="utf-8",
    )
    (sessions / "duplicate.jsonl").write_text(
        json.dumps({"agent": "dev-builder"}) + "\n",
        encoding="utf-8",
    )
    (sessions / "malformed.jsonl").write_text("{invalid\n", encoding="utf-8")
    (sessions / "unsafe.jsonl").write_text(
        json.dumps({"agent": "../../escape"}) + "\n",
        encoding="utf-8",
    )

    _write_json(
        tmp_path / "crons.json",
        {
            "jobs": [
                {
                    "id": "job-1",
                    "agent_id": "infra-operator",
                    "agent_sequence": ["dev-builder", "m365-operator", "bad agent"],
                }
            ]
        },
    )

    _write_json(
        tmp_path / "subagents" / "run-1" / "state.json",
        {"id": "run-1", "agent": "iam-guardian", "status": "completed"},
    )
    _write_json(
        tmp_path / "subagents" / "run-2" / "state.json",
        {"id": "run-2", "agent": "dev-builder", "status": "completed"},
    )
    (tmp_path / "subagents" / "broken").mkdir()
    (tmp_path / "subagents" / "broken" / "state.json").write_text(
        "not-json", encoding="utf-8"
    )

    linked_session = sessions / "linked.jsonl"
    linked_session.write_text(
        json.dumps({"agent": "symlink-session"}) + "\n", encoding="utf-8"
    )
    linked_subagent = tmp_path / "subagents" / "linked"
    _write_json(
        linked_subagent / "state.json",
        {"id": "linked", "agent": "symlink-subagent"},
    )
    original_is_symlink = Path.is_symlink
    symlink_paths = {linked_session, linked_subagent}
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: candidate in symlink_paths or original_is_symlink(candidate),
    )

    assert discover_agent_names(tmp_path) == [
        "default",
        "dev-builder",
        "iam-guardian",
        "infra-operator",
        "m365-operator",
    ]


def test_discover_agent_names_ignores_symlinked_crons_and_missing_sources(tmp_path, monkeypatch):
    crons = tmp_path / "crons.json"
    _write_json(
        crons,
        {"jobs": [{"id": "job-1", "agent_id": "should-not-appear"}]},
    )
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda candidate: candidate == crons or original_is_symlink(candidate),
    )

    assert discover_agent_names(tmp_path) == []
