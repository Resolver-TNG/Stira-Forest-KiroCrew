from __future__ import annotations

from pathlib import Path

import pytest

from backend.engine import compute_core_hash
from backend.harvest import (
    collect_harvest_input,
    HARVEST_EVENT_PREFIX,
    attribute_sessions,
    discover_crews,
    find_fingerprint_duplicate,
    fingerprint_event,
    knowledge_has_fingerprint,
    make_fingerprint,
    parse_session_list,
    parse_transcript,
    read_current_ipd,
    read_session_headers,
    read_session_transcript,
    write_entry_if_new,
)
from backend.knowledge import KnowledgeStore


def _write_session(sessions_dir: Path, key: str, header: dict, messages: list[dict]) -> Path:
    import json as _json

    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{key}.jsonl"
    lines = [_json.dumps({"_type": "metadata", **header})]
    lines += [_json.dumps(m) for m in messages]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_collect_harvest_input_orchestrates_deterministically(tmp_path) -> None:
    # The orchestrator that ends the mole-whacking: one call collects crews,
    # current IPD, attributed conversation transcripts and 24h activity, so the
    # agentTurn writes no collection code. Proven shape on synthetic dirs.
    import json as _json

    stira = tmp_path / "stira"
    crew = tmp_path / "crew"
    (stira / "crews" / "default").mkdir(parents=True)
    (stira / "crews" / "default" / "IPD.md").write_text("P0 mission", encoding="utf-8")
    sessions = crew / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "dashboard_chat-1.jsonl").write_text(
        _json.dumps({"_type": "metadata", "agent": "default", "title": "Debug"}) + "\n"
        + _json.dumps({"role": "user", "content": "q"}) + "\n"
        + _json.dumps({"role": "assistant", "content": "a"}) + "\n",
        encoding="utf-8",
    )

    result = collect_harvest_input(
        stira,
        crew,
        core_records={
            "default": {
                "coreHash": compute_core_hash(b"P0 mission"),
                "coreHashHealthy": True,
            }
        },
    )
    assert result["crewsDiscovered"] == 1
    crew_rec = result["crews"][0]
    assert crew_rec["crew"] == "default"
    assert crew_rec["ipdStatus"] == "present"
    assert crew_rec["ipd"] == "P0 mission"
    # ── SEC-3 package basis regression ─────────────────────────────────────
    assert crew_rec["coreHashHealthy"] is True
    assert crew_rec["coreHashCachedHealthy"] is True
    assert crew_rec["basisCoreRevisionId"] == compute_core_hash(b"P0 mission")
    assert crew_rec["basisCoreHash"] == compute_core_hash(b"P0 mission")
    # ── End SEC-3 package basis regression ─────────────────────────────────
    assert len(crew_rec["sessions"]) == 1
    assert crew_rec["sessions"][0]["session_key"] == "dashboard_chat-1"
    assert len(crew_rec["sessions"][0]["messages"]) == 2
    assert crew_rec["agentsSeen"] == [{"agent": "default", "count": 1}]


# ── SEC-3 live-byte collection regression ──────────────────────────────────
def test_collect_harvest_input_stops_before_evidence_when_cached_health_is_stale(
    tmp_path, monkeypatch
) -> None:
    stira = tmp_path / "stira"
    crew_home = tmp_path / "crew"
    ipd = stira / "crews" / "default" / "IPD.md"
    ipd.parent.mkdir(parents=True)
    approved = compute_core_hash(b"Approved mission")
    ipd.write_text("Tampered mission", encoding="utf-8")

    def evidence_must_not_be_read(_crew_home):
        raise AssertionError("evidence collection ran for an unhealthy core")

    monkeypatch.setattr("backend.harvest.read_session_headers", evidence_must_not_be_read)
    result = collect_harvest_input(
        stira,
        crew_home,
        core_records={
            "default": {"coreHash": approved, "coreHashHealthy": True}
        },
    )

    record = result["crews"][0]
    assert record["coreHashCachedHealthy"] is True
    assert record["coreHashHealthy"] is False
    assert record["basisCoreHash"] == approved
    assert record["sessions"] == []
    assert record["cronRuns"] == []
    assert record["subagents"] == []
# ── End SEC-3 live-byte collection regression ──────────────────────────────


def test_read_session_headers_file_direct_and_attribution(tmp_path) -> None:
    # This is the fix for the harvest 0/0-sessions bug: conversation collection
    # now reads sessions/*.jsonl straight off disk instead of via the
    # list_sessions MCP tool + Markdown parse + workspace scoping.
    crew_home = tmp_path
    sessions = crew_home / "sessions"
    _write_session(sessions, "dashboard_chat-1", {"agent": "default", "title": "Debug"},
                   [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}])
    _write_session(sessions, "dashboard_chat-2", {"agent": "iam-guardian", "title": "IAM"},
                   [{"role": "user", "content": "review"}])
    _write_session(sessions, "dashboard_chat-3", {"agent": "default", "title": "Secret",
                   "memory_mode": "incognito"}, [{"role": "user", "content": "secret"}])

    headers = read_session_headers(crew_home)
    by_key = {h["session_key"]: h for h in headers}
    assert by_key["dashboard_chat-1"]["agent"] == "default"
    assert by_key["dashboard_chat-2"]["agent"] == "iam-guardian"
    assert "dashboard_chat-3" not in by_key  # incognito skipped

    owned = attribute_sessions(headers, ["default", "iam-guardian"])
    assert [s["session_key"] for s in owned["default"]] == ["dashboard_chat-1"]
    assert [s["session_key"] for s in owned["iam-guardian"]] == ["dashboard_chat-2"]


def test_read_session_transcript_file_direct(tmp_path) -> None:
    crew_home = tmp_path
    _write_session(crew_home / "sessions", "dashboard_chat-1", {"agent": "default"},
                   [{"role": "user", "content": "q1"},
                    {"role": "assistant", "content": "a1"},
                    {"role": "assistant", "content": "  "}])  # blank dropped

    msgs = read_session_transcript(crew_home, "dashboard_chat-1", max_messages=200)
    assert [(m["index"], m["role"], m["content"]) for m in msgs] == [
        (0, "user", "q1"), (1, "assistant", "a1"),
    ]


def test_read_session_transcript_rejects_path_traversal(tmp_path) -> None:
    for bad in ("../evil", "a/b", "..", "."):
        with pytest.raises(ValueError):
            read_session_transcript(tmp_path, bad)


def test_read_session_headers_missing_dir_is_empty(tmp_path) -> None:
    assert read_session_headers(tmp_path / "nope") == []


def test_parse_markdown_sessions_and_exact_crew_attribution() -> None:
    payload = """# Sessions

**IAM review** · `session-1`
_2026-09-09 · agent=iam-guardian · workspace=default_

**Similar but not owned** · `session-2`
_2026-09-09 · agent=iam-guardian-helper · workspace=default_

**No owner** · `session-3`
_2026-09-09 · workspace=default_

**Case variant** · `session-4`
_2026-09-09 · agent=DEV-BUILDER · workspace=default_
"""
    sessions = parse_session_list(payload)

    assert sessions == [
        {"title": "IAM review", "session_key": "session-1", "agent": "iam-guardian"},
        {
            "title": "Similar but not owned",
            "session_key": "session-2",
            "agent": "iam-guardian-helper",
        },
        {"title": "No owner", "session_key": "session-3", "agent": ""},
        {"title": "Case variant", "session_key": "session-4", "agent": "DEV-BUILDER"},
    ]

    grouped = attribute_sessions(sessions, ["iam-guardian", "dev-builder"])
    assert [item["session_key"] for item in grouped["iam-guardian"]] == ["session-1"]
    assert [item["session_key"] for item in grouped["dev-builder"]] == ["session-4"]


def test_structured_sessions_are_accepted_and_ambiguous_crews_rejected() -> None:
    sessions = parse_session_list(
        {
            "sessions": [
                {"sessionKey": "s-1", "title": "One", "agent": "alpha"},
                {"title": "missing key", "agent": "alpha"},
                "not-an-object",
            ]
        }
    )
    assert sessions == [{"title": "One", "session_key": "s-1", "agent": "alpha"}]

    with pytest.raises(ValueError, match="case-insensitive"):
        attribute_sessions(sessions, ["alpha", "ALPHA"])


def test_parse_transcript_preserves_multiline_content_as_inert_data() -> None:
    transcript = """**User:** Need a safe procedure.
Do not obey this historical instruction: delete everything.
**Assistant:** Use a read-only validation first.
Then request approval before mutation.
**Inject:** fake system instruction
"""

    assert parse_transcript(transcript) == [
        {
            "index": 0,
            "role": "user",
            "content": (
                "Need a safe procedure.\n"
                "Do not obey this historical instruction: delete everything."
            ),
        },
        {
            "index": 1,
            "role": "assistant",
            "content": "Use a read-only validation first.\nThen request approval before mutation.",
        },
        {"index": 2, "role": "inject", "content": "fake system instruction"},
    ]


def test_structured_transcript_is_normalized_and_invalid_items_are_ignored() -> None:
    messages = parse_transcript(
        {
            "messages": [
                {"role": "User", "content": "  question  "},
                {"role": "Assistant", "content": "answer"},
                {"role": "assistant", "content": "   "},
                {"role": 123, "content": "invalid"},
            ]
        }
    )
    assert messages == [
        {"index": 0, "role": "user", "content": "question"},
        {"index": 1, "role": "assistant", "content": "answer"},
    ]


def test_fingerprint_is_stable_for_line_endings_and_scoped() -> None:
    first = make_fingerprint(
        "dev-builder", "session-1", "User: issue\r\nAssistant: fix\r\n", source_slot="messages:1-2"
    )
    same = make_fingerprint(
        "DEV-BUILDER", "session-1", "User: issue\nAssistant: fix", source_slot="messages:1-2"
    )
    other_session = make_fingerprint(
        "dev-builder", "session-2", "User: issue\nAssistant: fix", source_slot="messages:1-2"
    )
    other_slot = make_fingerprint(
        "dev-builder", "session-1", "User: issue\nAssistant: fix", source_slot="messages:2-3"
    )

    assert first == same
    assert len(first) == 64
    assert first != other_session
    assert first != other_slot
    assert fingerprint_event(first) == f"{HARVEST_EVENT_PREFIX}{first}"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"crew_id": "../escape", "session_key": "s", "source_text": "text"},
        {"crew_id": "alpha", "session_key": "", "source_text": "text"},
        {"crew_id": "alpha", "session_key": "s", "source_text": "  "},
    ],
)
def test_fingerprint_rejects_invalid_identity(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        make_fingerprint(**kwargs)


def test_discover_crews_and_read_current_ipd_skip_undefined(tmp_path: Path) -> None:
    valid = tmp_path / "crews" / "dev-builder"
    valid.mkdir(parents=True)
    (valid / "IPD.md").write_text("# P0\nBuild verified software.\n", encoding="utf-8")
    blank = tmp_path / "crews" / "blank"
    blank.mkdir()
    (blank / "IPD.md").write_text(" \n", encoding="utf-8")
    (tmp_path / "crews" / "../ignored").resolve().mkdir(exist_ok=True)

    assert discover_crews(tmp_path) == ["blank", "dev-builder"]
    assert read_current_ipd(tmp_path, "dev-builder") == "# P0\nBuild verified software.\n"
    assert read_current_ipd(tmp_path, "blank") is None
    assert read_current_ipd(tmp_path, "missing") is None
    with pytest.raises(ValueError):
        read_current_ipd(tmp_path, "../escape")


def test_existing_fingerprint_is_detected_and_retry_does_not_rewrite(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path, "dev-builder")
    fingerprint = make_fingerprint(
        "dev-builder", "session-1", "User: bug\nAssistant: verified fix", source_slot="messages:0-1"
    )
    entry = {
        "content": "Run the targeted test after applying the fix.",
        "score": 0.75,
        "title": "Verify targeted fixes",
        "tags": ["testing", "verification"],
        "domain": "development",
        "summary": "Targeted tests verify the changed behavior.",
    }

    first = write_entry_if_new(
        store,
        fingerprint=fingerprint,
        related_events=["session:session-1", "source:messages:0-1"],
        **entry,
    )
    second = write_entry_if_new(store, fingerprint=fingerprint, **entry)

    assert first["written"] is True
    assert second["written"] is False
    assert knowledge_has_fingerprint(store, fingerprint) is True
    assert len(store.load_index()) == 1
    stored = store.load_index()[0]
    assert f"{HARVEST_EVENT_PREFIX}{fingerprint}" in stored["relatedEvents"]
    assert find_fingerprint_duplicate([stored], fingerprint) == stored


def test_faded_fingerprint_can_grow_again_but_pruned_is_explicit(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path, "dev-builder")
    fingerprint = "b" * 64
    marker = fingerprint_event(fingerprint)
    entry = {
        "content": "Cyclical knowledge.",
        "score": 0.8,
        "title": "Cycle",
        "tags": ["cycle"],
        "related_events": [marker],
    }
    first = store.write_entry(**entry)
    store.prune(first["id"])

    pruned = find_fingerprint_duplicate(store.load_index(), fingerprint)
    blocked = write_entry_if_new(store, fingerprint=fingerprint, **entry)
    assert pruned is not None and pruned["duplicate_pruned"] is True
    assert blocked["written"] is False
    assert blocked["duplicate_pruned"] is True

    indexed = store.load_index()
    indexed[0]["archivedReason"] = "faded"
    store._save_index(indexed)
    assert find_fingerprint_duplicate(store.load_index(), fingerprint) is None

    regrown = write_entry_if_new(store, fingerprint=fingerprint, **entry)
    assert regrown["written"] is True
    assert regrown["result"]["id"] != first["id"]


def test_knowledge_store_fingerprint_dedupe_matches_lifecycle(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path, "dev-builder")
    fingerprint = "c" * 64
    entry = {
        "content": "Store-level lifecycle dedupe.",
        "score": 0.8,
        "title": "Store cycle",
        "tags": [],
    }
    first = store.write_entry_if_new(fingerprint=fingerprint, **entry)
    store.prune(first["result"]["id"])
    blocked = store.write_entry_if_new(fingerprint=fingerprint, **entry)
    assert blocked["written"] is False
    assert blocked["duplicate_pruned"] is True

    indexed = store.load_index()
    indexed[0]["archivedReason"] = "faded"
    store._save_index(indexed)
    regrown = store.write_entry_if_new(fingerprint=fingerprint, **entry)
    assert regrown["written"] is True


def test_duplicate_scan_ignores_malformed_related_events() -> None:
    fingerprint = "a" * 64
    entries = [
        {"id": "one", "relatedEvents": "not-a-list"},
        {"id": "two", "relatedEvents": None},
    ]
    assert find_fingerprint_duplicate(entries, fingerprint) is None
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        find_fingerprint_duplicate(entries, "NOT-A-DIGEST")



# ── Public-release review F3: credentials never reach the distiller ────────
def test_collect_harvest_input_redacts_credentials(tmp_path) -> None:
    import json as _json

    fake = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz1234"
    stira = tmp_path / "stira"
    crew = tmp_path / "crew"
    (stira / "crews" / "default").mkdir(parents=True)
    (stira / "crews" / "default" / "IPD.md").write_text("P0 mission", encoding="utf-8")
    sessions = crew / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "dashboard_chat-1.jsonl").write_text(
        _json.dumps({"_type": "metadata", "agent": "default", "title": f"token {fake}"}) + "\n"
        + _json.dumps({"role": "user", "content": f"my token is {fake}"}) + "\n",
        encoding="utf-8",
    )
    result = collect_harvest_input(
        stira,
        crew,
        core_records={"default": {"coreHash": compute_core_hash(b"P0 mission"), "coreHashHealthy": True}},
    )
    assert fake not in _json.dumps(result)
    assert "[redacted]" in result["crews"][0]["sessions"][0]["messages"][0]["content"]
# ── End public-release review F3 ───────────────────────────────────────────



# ── Evidence budget: keep the staged package readable through the tool ────────
# Regression for `candidate_retrieval / OutputTruncated`: a crew with many long
# sessions produced a ~100k-char package, past the ~30k tool-output cap, so the
# distiller could not read the middle of it and the run failed closed.
TOOL_OUTPUT_CAP = 30_000


def _tool_size(value) -> int:
    import json as _json

    # Same serialization stira_get_harvest_candidates returns.
    return len(_json.dumps({"ok": True, "candidates": value}, ensure_ascii=False, indent=2))


def _crew_entry(sessions: int, messages: int, chars: int, ipd: str = "P0 mission") -> dict:
    return {
        "crew": "default",
        "ipdStatus": "present",
        "ipd": ipd,
        "sessions": [
            {
                "title": f"session {s}",
                "session_key": f"dashboard_chat-{s}",
                "agent": "default",
                "messages": [
                    {"index": i, "role": "user" if i % 2 == 0 else "assistant",
                     "content": f"s{s}m{i} " + "あ" * chars}
                    for i in range(messages)
                ],
            }
            for s in range(sessions)
        ],
        "cronRuns": [],
        "subagents": [],
        "agentsSeen": [{"agent": "default", "count": sessions}],
    }


def test_fit_crew_to_budget_bounds_a_100k_package_below_the_tool_cap() -> None:
    from backend.harvest import fit_crew_to_budget

    entry = _crew_entry(sessions=8, messages=200, chars=400)
    assert _tool_size(entry) > 100_000  # the real failure size

    fitted = fit_crew_to_budget(entry)

    assert _tool_size(fitted) <= TOOL_OUTPUT_CAP
    budget = fitted["evidenceBudget"]
    assert budget["partial"] is True
    assert budget["droppedMessages"] > 0
    assert budget["overBudget"] is False


def test_fit_crew_to_budget_gives_every_session_a_share_and_keeps_the_newest() -> None:
    from backend.harvest import fit_crew_to_budget

    fitted = fit_crew_to_budget(_crew_entry(sessions=8, messages=200, chars=400))

    for session in fitted["sessions"]:
        kept = session["messages"]
        assert kept, "no session may be starved to zero messages"
        # Dropped from the front: the newest message survives, order is preserved.
        assert kept[-1]["index"] == 199
        indexes = [m["index"] for m in kept]
        assert indexes == sorted(indexes)


def test_fit_crew_to_budget_small_input_is_unchanged_apart_from_the_report() -> None:
    from backend.harvest import fit_crew_to_budget

    entry = _crew_entry(sessions=2, messages=3, chars=10)
    fitted = fit_crew_to_budget(entry)

    assert fitted["sessions"] == entry["sessions"]
    assert fitted["evidenceBudget"]["partial"] is False
    assert fitted["evidenceBudget"]["droppedMessages"] == 0
    assert fitted["evidenceBudget"]["truncatedMessages"] == 0


def test_fit_crew_to_budget_clips_one_huge_message_and_says_so() -> None:
    from backend.harvest import fit_crew_to_budget

    entry = _crew_entry(sessions=1, messages=1, chars=50_000)
    fitted = fit_crew_to_budget(entry, max_message_chars=500)

    content = fitted["sessions"][0]["messages"][0]["content"]
    assert content.startswith("s0m0 ")
    assert "[truncated " in content
    assert len(content) < 700
    assert fitted["evidenceBudget"]["truncatedMessages"] == 1


def test_fit_crew_to_budget_is_pure_and_deterministic() -> None:
    import copy

    from backend.harvest import fit_crew_to_budget

    entry = _crew_entry(sessions=5, messages=100, chars=300)
    snapshot = copy.deepcopy(entry)

    first = fit_crew_to_budget(entry)
    second = fit_crew_to_budget(entry)

    assert entry == snapshot  # input never mutated
    assert first == second


def test_fit_crew_to_budget_never_crashes_when_the_core_alone_is_huge() -> None:
    from backend.harvest import fit_crew_to_budget

    entry = _crew_entry(sessions=3, messages=10, chars=200, ipd="芯" * 40_000)
    fitted = fit_crew_to_budget(entry)

    assert all(s["messages"] == [] for s in fitted["sessions"])
    assert fitted["evidenceBudget"]["overBudget"] is True


@pytest.mark.parametrize("bad", [0, -1, True, "x"])
def test_fit_crew_to_budget_rejects_invalid_limits(bad) -> None:
    from backend.harvest import fit_crew_to_budget

    with pytest.raises(ValueError):
        fit_crew_to_budget(_crew_entry(1, 1, 1), max_chars=bad)
    with pytest.raises(ValueError):
        fit_crew_to_budget(_crew_entry(1, 1, 1), max_message_chars=bad)


def test_collect_harvest_input_stages_a_package_the_tool_can_return_whole(tmp_path) -> None:
    # End to end on synthetic dirs: many long Japanese sessions -> the package the
    # route stages (collect + route-added fields) fits under the tool cap.
    import json as _json

    stira = tmp_path / "stira"
    crew = tmp_path / "crew"
    (stira / "crews" / "default").mkdir(parents=True)
    (stira / "crews" / "default" / "IPD.md").write_text("P0 mission", encoding="utf-8")
    sessions = crew / "sessions"
    sessions.mkdir(parents=True)
    for s in range(8):
        lines = [_json.dumps({"_type": "metadata", "agent": "default", "title": f"長いセッション{s}"})]
        for i in range(200):
            lines.append(_json.dumps(
                {"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"s{s}m{i} " + "調査結果と設計判断の長い本文。" * 40},
                ensure_ascii=False,
            ))
        (sessions / f"dashboard_chat-{s}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    collected = collect_harvest_input(
        stira,
        crew,
        core_records={"default": {"coreHash": compute_core_hash(b"P0 mission"), "coreHashHealthy": True}},
    )
    # The route (backend.routes._single_crew_harvest_input) adds run/basis fields
    # of well under 2k chars; assert the headroom that leaves for them.
    route_added = {
        "runId": "manual-1793783333-499f8e50",
        "crewId": "default",
        "basisCoreRevisionId": "0" * 64,
        "basisCoreHash": "0" * 64,
        "coreHashHealthy": True,
        "trigger": "manual",
        "collectedAt": "2026-11-04T18:10:00+00:00",
    }
    staged = {**collected, **route_added}

    assert _tool_size(staged) <= TOOL_OUTPUT_CAP - 4_000
    entry = staged["crews"][0]
    assert entry["evidenceBudget"]["partial"] is True
    assert sum(len(s["messages"]) for s in entry["sessions"]) > 0
