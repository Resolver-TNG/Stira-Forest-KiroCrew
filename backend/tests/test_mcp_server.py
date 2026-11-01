from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types

import pytest


def _install_kirocrew_test_doubles() -> None:
    """Mock only the host MCP/AppStorage seam when pytest runs outside KiroCrew."""

    try:
        import kiro_crew  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    package = types.ModuleType("kiro_crew")
    package.__path__ = []
    apps = types.ModuleType("kiro_crew.apps")
    apps.__path__ = []
    app_storage = types.ModuleType("kiro_crew.apps.app_storage")
    atomic = types.ModuleType("kiro_crew.atomic_write")
    shared = types.ModuleType("kiro_crew.mcp_shared")
    validation = types.ModuleType("kiro_crew.validation")

    class FakeAppStorage:
        def __init__(self, app_name: str, data_dir: Path) -> None:
            self._kv_dir = Path(data_dir) / "kv"
            self._kv_dir.mkdir(parents=True, exist_ok=True)

        def _path(self, key: str) -> Path:
            return self._kv_dir / f"{key}.json"

        def get(self, key: str):
            path = self._path(key)
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

        def set(self, key: str, value) -> None:
            self._path(key).write_text(json.dumps(value), encoding="utf-8")

    def fake_atomic_write(path, content, mode=0o600, **_kwargs) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        try:
            os.chmod(target, mode)
        except OSError:
            pass

    class FakeValidationError(ValueError):
        def __init__(self, *parts) -> None:
            super().__init__(": ".join(str(part) for part in parts))

    def fake_validate(arguments, schema) -> None:
        def validate_value(value, rule, field):
            expected = rule.get("type")
            if expected == "string" and not isinstance(value, str):
                raise FakeValidationError(f"{field} must be a string")
            if expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise FakeValidationError(f"{field} must be an integer")
            if expected == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise FakeValidationError(f"{field} must be a number")
            if expected == "array" and not isinstance(value, list):
                raise FakeValidationError(f"{field} must be an array")
            if expected == "object" and not isinstance(value, dict):
                raise FakeValidationError(f"{field} must be an object")
            if isinstance(value, str) and len(value) > rule.get("maxLength", len(value)):
                raise FakeValidationError(f"{field} exceeds maxLength")
            if "enum" in rule and value not in rule["enum"]:
                raise FakeValidationError(f"{field} is not in enum")
            if isinstance(value, dict) and expected == "object":
                nested = rule.get("properties", {})
                unknown = set(value) - set(nested)
                if rule.get("additionalProperties") is False and unknown:
                    raise FakeValidationError(f"unexpected properties in {field}: {sorted(unknown)}")
                for key, item in value.items():
                    if key in nested:
                        validate_value(item, nested[key], f"{field}.{key}")
            if isinstance(value, list) and expected == "array":
                if len(value) > rule.get("maxItems", len(value)):
                    raise FakeValidationError(f"{field} exceeds maxItems")
                for index, item in enumerate(value):
                    validate_value(item, rule.get("items", {}), f"{field}[{index}]")

        properties = schema.get("properties", {})
        unexpected = set(arguments) - set(properties)
        if schema.get("additionalProperties") is False and unexpected:
            raise FakeValidationError(f"unexpected properties: {sorted(unexpected)}")
        missing = set(schema.get("required", [])) - set(arguments)
        if missing:
            raise FakeValidationError(f"missing required properties: {sorted(missing)}")
        for key, value in arguments.items():
            if key in properties:
                validate_value(value, properties[key], key)

    def fake_call_tool_with_logging(name, args, validate, call, **_kwargs):
        try:
            return call(name, validate(name, args))
        except Exception as exc:  # match the host helper's text error surface
            return f"Error: {exc}"

    def fake_build_tool_response(text: str, **_kwargs):
        return {"content": [{"type": "text", "text": text}]}

    def fake_stdio_loop(*_args, **_kwargs) -> None:
        raise RuntimeError("stdio loop is not available in the pytest double")

    app_storage.AppStorage = FakeAppStorage
    atomic.atomic_write = fake_atomic_write
    validation.ValidationError = FakeValidationError
    validation.validate_mcp_tool_arguments = fake_validate
    validation.build_tool_response = fake_build_tool_response
    shared.call_tool_with_logging = fake_call_tool_with_logging
    shared.run_mcp_stdio_loop = fake_stdio_loop
    package.apps = apps
    sys.modules.update(
        {
            "kiro_crew": package,
            "kiro_crew.apps": apps,
            "kiro_crew.apps.app_storage": app_storage,
            "kiro_crew.atomic_write": atomic,
            "kiro_crew.mcp_shared": shared,
            "kiro_crew.validation": validation,
        }
    )


_install_kirocrew_test_doubles()

from backend import mcp_server  # noqa: E402


@pytest.fixture()
def stira_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STIRA_HOME", str(tmp_path))
    monkeypatch.delenv("STIRA_CREW_ID", raising=False)
    return tmp_path


def call(name: str, **args: object) -> dict:
    result = mcp_server._call_tool_inner(name, args)
    assert not result.startswith("Error:"), result
    return json.loads(result)


def init_core_direct(crew_id: str, content: str, *, rebaseline: bool = False) -> dict:
    """Initialize a crew's core through the dashboard code path.

    ``stira_init_core`` was removed from the MCP tool surface (core writes must
    go through the Stira Forest dashboard's ``POST /crews`` route, never an MCP
    client such as the distiller). Tests drive the same ``initialize_core``
    function that route calls.
    """

    from backend.core_init import initialize_core

    return initialize_core(
        mcp_server._data_dir() / "crews",
        mcp_server._storage(),
        crew_id,
        content,
        rebaseline=rebaseline,
    )


def init(crew_id: str, content: str = "# P0\nBuild correct software.\n") -> dict:
    return init_core_direct(crew_id, content)


def test_exposes_exact_crew_tool_surface_and_valid_schemas() -> None:
    tools = mcp_server._list_tools()
    assert [tool["name"] for tool in tools] == [
        "stira_tick",
        "stira_status",
        "stira_apply_event",
        "stira_check_drift",
        "stira_list_events",
        "stira_record_harvest_run",
        "stira_get_harvest_candidates",
        "stira_suggest_score",
        "stira_write_knowledge",
        "stira_get_knowledge",
        "stira_list_index",
        "stira_check_core",
    ]
    # ── Security: core writes are not reachable over MCP ───────────────────
    assert "stira_init_core" not in [tool["name"] for tool in tools]
    assert "stira_init_core" not in mcp_server._DISPATCH
    # ── End core-write MCP exposure guard ──────────────────────────────────
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)
    # ── SEC-3 write schema regression ──────────────────────────────────────
    write_schema = next(
        tool["inputSchema"] for tool in tools
        if tool["name"] == "stira_write_knowledge"
    )
    assert "basis_core_hash" in write_schema["properties"]
    assert "basis_core_hash" in write_schema["required"]
    # ── End SEC-3 write schema regression ─────────────────────────────────
    assert mcp_server._validate_args(
        "stira_apply_event",
        {"crew_id": "dev-builder", "category": "discovery", "intensity": 0.5},
    )["crew_id"] == "dev-builder"


def test_record_harvest_run_schema_and_uninitialized_write(stira_home: Path) -> None:
    tool = next(
        item for item in mcp_server._list_tools()
        if item["name"] == "stira_record_harvest_run"
    )
    schema = tool["inputSchema"]
    assert schema["required"] == ["runId", "phase"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["notes"]["maxLength"] == 500
    assert schema["properties"]["errors"]["items"]["properties"]["message"]["maxLength"] == 200

    with pytest.raises(Exception):
        mcp_server._validate_args(
            "stira_record_harvest_run",
            {"crew_id": "new-crew", "runId": "manual-00000001", "phase": "other"},
        )
    with pytest.raises(Exception):
        mcp_server._validate_args(
            "stira_record_harvest_run",
            {"crew_id": "new-crew", "runId": "manual-00000001", "phase": "started", "notes": "x" * 501},
        )
    with pytest.raises(Exception):
        mcp_server._validate_args(
            "stira_record_harvest_run",
            {"crew_id": "new-crew", "runId": "manual-00000001", "phase": "started", "rawTranscript": "secret"},
        )

    result = call(
        "stira_record_harvest_run",
        crew_id="new-crew",
        runId="manual-00000001",
        phase="started",
        stiraHome=str(stira_home),
        crewsDiscovered=2,
    )

    assert result == {
        "ok": True,
        "crewId": "new-crew",
        "runId": "manual-00000001",
        "phase": "started",
        "recorded": True,
    }
    log_path = stira_home / "crews" / "new-crew" / "harvest-runs.jsonl"
    assert log_path.is_file()
    assert not (stira_home / "crews" / "new-crew" / "state.json").exists()


def test_get_harvest_candidates_returns_staged_package_or_missing(
    stira_home: Path,
) -> None:
    tool = next(
        item for item in mcp_server._list_tools()
        if item["name"] == "stira_get_harvest_candidates"
    )
    assert tool["inputSchema"] == mcp_server._INPUT_SCHEMAS[
        "stira_get_harvest_candidates"
    ]
    assert tool["inputSchema"].get("required") is None
    assert set(tool["inputSchema"]["properties"]) == {"crew_id"}
    assert "stira_get_harvest_candidates" in mcp_server._DISPATCH

    initialized = init("dev-builder")
    basis = initialized["coreHash"]
    missing = call("stira_get_harvest_candidates", crew_id="dev-builder")
    assert missing == {
        "ok": False,
        "crewId": "dev-builder",
        "found": False,
        "candidates": None,
    }

    package = {
        "schema": "stira.harvest.input.v1",
        "runId": "manual-00000001",
        "crewId": "dev-builder",
        # ── SEC-3 staged package basis fixture ─────────────────────────────
        "basisCoreRevisionId": basis,
        "basisCoreHash": basis,
        "coreHashHealthy": True,
        # ── End SEC-3 staged package basis fixture ─────────────────────────
        "untrustedData": True,
        "crews": [],
    }
    mcp_server._storage().set("harvest-candidates-dev-builder", package)

    found = call("stira_get_harvest_candidates", crew_id="dev-builder")
    assert found == {
        "ok": True,
        "crewId": "dev-builder",
        "found": True,
        "candidates": package,
    }
    assert (stira_home / "crews" / "dev-builder" / "state.json").is_file()


def test_init_core_creates_per_crew_runtime_and_shared_registry(stira_home: Path) -> None:
    result = init("dev-builder")

    assert result["ok"] is True
    assert result["coreHash"]
    crew_dir = stira_home / "crews" / "dev-builder"
    assert (crew_dir / "IPD.md").read_text(encoding="utf-8").startswith("# P0")
    assert (crew_dir / "state.json").is_file()
    assert (crew_dir / "matrix.json").is_file()
    assert (crew_dir / "config.json").is_file()
    assert (crew_dir / "purpose_rules.json").is_file()

    registry = mcp_server._storage().get(mcp_server.CREW_REGISTRY_KEY)
    assert registry["crews"]["dev-builder"]["coreHash"] == result["coreHash"]
    assert registry["crews"]["dev-builder"]["coreHashHealthy"] is True

    # ── A-1 approved candidate remains idempotent without rebaseline ────────
    confirmed = init("dev-builder")
    assert confirmed["coreHash"] == result["coreHash"]
    assert confirmed["rebaselined"] is False
    assert confirmed["diskHashMismatchBefore"] is False
    # ── End A-1 approved candidate idempotency ──────────────────────────────


def test_init_core_refuses_silent_replacement_and_allows_explicit_rebaseline(
    stira_home: Path,
) -> None:
    approved_content = "Protect least privilege.\n"
    candidate_content = "Grant broad access.\n"
    initial = init("iam-guardian", approved_content)
    crew_dir = stira_home / "crews" / "iam-guardian"
    ipd_path = crew_dir / "IPD.md"

    # ── A-1 B-1: an already-tampered disk file is not an approval baseline ──
    from backend.core_init import CoreRebaselineRequiredError

    ipd_path.write_text(candidate_content, encoding="utf-8")
    with pytest.raises(CoreRebaselineRequiredError) as refused:
        init_core_direct("iam-guardian", candidate_content)
    assert "rebaseline=true" in str(refused.value)
    assert ipd_path.read_text(encoding="utf-8") == candidate_content

    registry = mcp_server._storage().get(mcp_server.CREW_REGISTRY_KEY)
    record = registry["crews"]["iam-guardian"]
    assert record["coreHash"] == initial["coreHash"]
    assert record["coreHashHealthy"] is False
    assert record["coreProposalPending"] is True
    proposal_key = "core-proposal-iam-guardian"
    proposal = mcp_server._storage().get(proposal_key)
    assert proposal["baseHash"] == initial["coreHash"]
    assert proposal["candidateHash"] == mcp_server.compute_core_hash(candidate_content)

    accepted = init_core_direct("iam-guardian", candidate_content, rebaseline=True)
    assert accepted["rebaselined"] is True
    assert accepted["diskHashMismatchBefore"] is True
    assert mcp_server._storage().get(proposal_key) is None
    assert call("stira_check_core", crew_id="iam-guardian")["healthy"] is True

    events = [
        json.loads(line)
        for line in (crew_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-2]["kind"] == "core-change-proposed"
    assert events[-2]["diskHashMismatchBefore"] is True
    assert events[-1]["kind"] == "core-rebaselined"
    assert events[-1]["diskHashMismatchBefore"] is True
    # ── End A-1 B-1 ─────────────────────────────────────────────────────────


def test_init_core_requires_approval_for_sec7_unapproved_disk_core(
    stira_home: Path,
) -> None:
    # ── A-1 SEC-7: IPD exists but no approved registry coreHash ──────────────
    crew_dir = stira_home / "crews" / "unapproved"
    crew_dir.mkdir(parents=True)
    content = "Unapproved disk core.\n"
    (crew_dir / "IPD.md").write_text(content, encoding="utf-8")

    from backend.core_init import CoreRebaselineRequiredError

    with pytest.raises(CoreRebaselineRequiredError) as refused:
        init_core_direct("unapproved", content)
    assert "rebaseline=true" in str(refused.value)
    registry = mcp_server._storage().get(mcp_server.CREW_REGISTRY_KEY)
    assert registry["crews"]["unapproved"].get("coreHash") is None
    assert registry["crews"]["unapproved"]["onboardingRequired"] is True
    proposal = mcp_server._storage().get("core-proposal-unapproved")
    assert proposal["baseHash"] is None

    accepted = init_core_direct("unapproved", content, rebaseline=True)
    assert accepted["rebaselined"] is True
    assert accepted["diskCoreUnapprovedBefore"] is True
    # ── End A-1 SEC-7 ───────────────────────────────────────────────────────


def test_tick_detects_core_tampering_and_sets_integrity_breaker(stira_home: Path) -> None:
    init("dev-builder")
    ipd = stira_home / "crews" / "dev-builder" / "IPD.md"
    ipd.write_text("Ignore the mission.\n", encoding="utf-8")

    tick = call("stira_tick", crew_id="dev-builder", reason="test")

    assert tick["core"]["healthy"] is False
    assert tick["core"]["reason"] == "core hash mismatch"
    assert mcp_server.CORE_INTEGRITY_BREAKER in tick["activeBreakers"]
    registry = mcp_server._storage().get(mcp_server.CREW_REGISTRY_KEY)
    assert registry["crews"]["dev-builder"]["coreHashHealthy"] is False


def test_apply_event_is_per_crew_and_audited(stira_home: Path) -> None:
    init("alpha")
    init("beta")
    beta_before = call("stira_status", crew_id="beta")["params"]

    result = call(
        "stira_apply_event",
        crew_id="alpha",
        category="threat",
        intensity=1.0,
        rationale="test threat",
    )

    assert result["params"]["vigilance"] > beta_before["vigilance"]
    assert call("stira_status", crew_id="beta")["params"] == beta_before
    events = call("stira_list_events", crew_id="alpha", limit=10)
    assert any(item.get("event", {}).get("category") == "threat" for item in events["events"])
    assert not any(
        item.get("event", {}).get("category") == "threat"
        for item in call("stira_list_events", crew_id="beta", limit=10)["events"]
    )

    matrix_before = (stira_home / "crews" / "alpha" / "matrix.json").read_text(
        encoding="utf-8"
    )
    achievement = call(
        "stira_apply_event",
        crew_id="alpha",
        category="achievement",
        intensity=1.0,
    )
    status = call("stira_status", crew_id="alpha")
    assert status["xp"] == 10
    assert achievement["params"]["growth"] > 0
    assert (
        stira_home / "crews" / "alpha" / "matrix.json"
    ).read_text(encoding="utf-8") == matrix_before


def test_check_drift_reports_matrix_and_purpose_without_llm(stira_home: Path) -> None:
    init("iam-guardian")
    crew_dir = stira_home / "crews" / "iam-guardian"
    matrix_path = crew_dir / "matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["triggers"]["threat"]["stability"] = 0.8
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    (crew_dir / "purpose_rules.json").write_text(
        json.dumps(
            {
                "version": 1,
                "crew": "iam-guardian",
                "rules": [
                    {
                        "id": "approval-required",
                        "severity": "block",
                        "message": "Approval required",
                        "all": [
                            {
                                "field": "approval.granted",
                                "operator": "not_eq",
                                "value": True,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = call(
        "stira_check_drift",
        crew_id="iam-guardian",
        threshold=0.5,
        purpose_context={"version": 1, "approval": {"granted": False}},
    )

    assert report["drifted"] is True
    assert report["matrix"]["drifted"] is True
    assert report["purpose"]["violations"][0]["ruleId"] == "approval-required"
    assert report["means"]["requiresAdapterJudgment"] is True
    assert report["means"]["evaluated"] is False


def test_knowledge_tools_are_isolated_and_tick_returns_top_entries(stira_home: Path) -> None:
    alpha_core = init("alpha")
    init("beta")
    written = call(
        "stira_write_knowledge",
        crew_id="alpha",
        basis_core_hash=alpha_core["coreHash"],
        content="Always run pytest after changing Python code.",
        score=0.9,
        title="Validation rule",
        tags=["python", "test"],
    )

    # Public-release review F2(c): an MCP (model) write is capped below core-near.
    assert written["layer"] == "knowledge" and written["score"] == 0.79
    assert written["requestedScore"] == 0.9
    # ── SEC-3 confirmed leaf basis regression ──────────────────────────────
    assert written["basisCoreRevisionId"] == alpha_core["coreHash"]
    assert written["basisCoreHash"] == alpha_core["coreHash"]
    # ── End SEC-3 confirmed leaf basis regression ──────────────────────────
    alpha = call("stira_get_knowledge", crew_id="alpha", query="Validation")
    beta = call("stira_get_knowledge", crew_id="beta", query="Validation")
    assert alpha["totalCount"] == 1
    assert alpha["entries"][0]["content"].startswith("Always run pytest")
    # ── SEC-3 leaf audit pin regression ────────────────────────────────────
    assert f"core:basis-revision:{alpha_core['coreHash']}" in alpha["entries"][0]["relatedEvents"]
    assert f"core:basis-hash:{alpha_core['coreHash']}" in alpha["entries"][0]["relatedEvents"]
    # ── End SEC-3 leaf audit pin regression ────────────────────────────────
    assert beta["totalCount"] == 0
    top = call("stira_tick", crew_id="alpha")["recentKnowledge"]
    assert top["count"] == 1
    assert top["topN"][0]["title"] == "Validation rule"


def test_get_knowledge_search_mode_defaults_to_substring_and_relevance_is_read_only(
    stira_home: Path,
) -> None:
    initialized = init("search-crew")
    call(
        "stira_write_knowledge",
        crew_id="search-crew",
        basis_core_hash=initialized["coreHash"],
        content="大規模災害では避難者をバスで広域輸送する。",
        score=0.8,
        title="バスによる広域避難計画",
        tags=["輸送計画", "広域避難"],
        domain="防災",
        summary="避難者の広域輸送手順",
    )
    context = "広域の避難者をバスで輸送する計画は？"

    legacy = call("stira_get_knowledge", crew_id="search-crew", query=context)
    relevant = call(
        "stira_get_knowledge",
        crew_id="search-crew",
        query=context,
        searchMode="relevance",
    )

    assert legacy["totalCount"] == 0
    assert relevant["totalCount"] == 1
    assert relevant["entries"][0]["relevanceScore"] > 0
    assert mcp_server.KnowledgeStore(
        stira_home, "search-crew"
    ).load_index()[0]["accessCount"] == 0


def test_suggest_score_reads_live_params_when_crew_is_supplied(stira_home: Path) -> None:
    init("trusted-crew")
    for _ in range(4):
        call(
            "stira_apply_event",
            crew_id="trusted-crew",
            category="connection",
            intensity=1.0,
        )

    suggestion = call(
        "stira_suggest_score",
        crew_id="trusted-crew",
        category="discovery",
        intensity=0.6,
        operatorEvidenceRatio=1.0,
        evidenceStrength=0.0,
    )

    assert suggestion["score"] > 0.54
    assert suggestion["breakdown"]["trustBonus"] > 0


def test_list_index_returns_body_free_entries_and_initializes_crew(stira_home: Path) -> None:
    tool = next(
        item for item in mcp_server._list_tools()
        if item["name"] == "stira_list_index"
    )
    assert set(tool["inputSchema"]["properties"]) == {"crew_id", "layers"}
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "stira_list_index" in mcp_server._DISPATCH

    initialized = init("index-crew")
    call(
        "stira_write_knowledge",
        crew_id="index-crew",
        basis_core_hash=initialized["coreHash"],
        content="Indexed leaf body that must never appear in the index.",
        score=0.7,
        title="Indexed leaf",
        tags=["ops"],
        domain="ops",
        summary="Indexed leaf summary",
    )

    listing = call("stira_list_index", crew_id="index-crew")
    assert listing["totalCount"] == 1
    row = listing["entries"][0]
    assert row["title"] == "Indexed leaf"
    assert "content" not in row
    assert "summary" not in row
    assert row["effectiveScore"] <= row["score"]


def test_list_index_initializes_crew_without_error(stira_home: Path) -> None:
    init("fresh-index-crew")
    listing = call("stira_list_index", crew_id="fresh-index-crew")
    assert listing == {
        "ok": True,
        "crewId": "fresh-index-crew",
        "dataNotice": mcp_server.DATA_NOTICE,
        "entries": [],
        "totalCount": 0,
    }


def test_suggest_score_is_pure_and_crew_id_can_come_from_dedicated_context(
    stira_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    suggestion = call(
        "stira_suggest_score",
        category="threat",
        intensity=0.8,
        breakerFired=True,
    )
    assert suggestion["score"] == 1.0
    assert not (stira_home / "crews").exists()

    monkeypatch.setenv("STIRA_CREW_ID", "context-crew")
    initialized = init_core_direct("context-crew", "# P0\nContext mission.\n")
    assert initialized["crewId"] == "context-crew"
    assert call("stira_status")["crewId"] == "context-crew"


def test_missing_crew_id_and_unknown_arguments_fail_closed(stira_home: Path) -> None:
    assert mcp_server._call_tool_inner("stira_status", {}).startswith("Error:")
    with pytest.raises(Exception):
        mcp_server._validate_args(
            "stira_status", {"crew_id": "dev-builder", "unexpected": True}
        )



# ── SEC-3 candidate/write boundary regressions ─────────────────────────────
def _knowledge_write_args(crew_id: str, basis: str) -> dict:
    return {
        "crew_id": crew_id,
        "basis_core_hash": basis,
        "content": "Pinned knowledge entry.",
        "score": 0.7,
        "title": "Pinned knowledge",
        "tags": ["core-integrity"],
    }


def test_direct_write_rejects_live_tamper_even_when_registry_cache_is_healthy(
    stira_home: Path,
) -> None:
    initialized = init("direct-write")
    registry = mcp_server._storage().get(mcp_server.CREW_REGISTRY_KEY)
    assert registry["crews"]["direct-write"]["coreHashHealthy"] is True
    (stira_home / "crews" / "direct-write" / "IPD.md").write_text(
        "Tampered mission.\n", encoding="utf-8"
    )

    rejected = mcp_server._call_tool_inner(
        "stira_write_knowledge",
        _knowledge_write_args("direct-write", initialized["coreHash"]),
    )

    assert rejected.startswith("Error:")
    assert "core integrity check failed" in rejected
    assert call("stira_get_knowledge", crew_id="direct-write")["totalCount"] == 0


def test_candidate_and_write_reject_old_basis_after_approved_revision_change(
    stira_home: Path,
) -> None:
    first = init("revisioned", "# P0\nFirst approved mission.\n")
    old_basis = first["coreHash"]
    package = {
        "schema": "stira.harvest.input.v1",
        "runId": "manual-00000002",
        "crewId": "revisioned",
        "basisCoreRevisionId": old_basis,
        "basisCoreHash": old_basis,
        "coreHashHealthy": True,
        "untrustedData": True,
        "crews": [],
    }
    mcp_server._storage().set("harvest-candidates-revisioned", package)
    init_core_direct(
        "revisioned",
        "# P0\nSecond approved mission.\n",
        rebaseline=True,
    )

    candidate_rejected = mcp_server._call_tool_inner(
        "stira_get_harvest_candidates", {"crew_id": "revisioned"}
    )
    write_rejected = mcp_server._call_tool_inner(
        "stira_write_knowledge",
        _knowledge_write_args("revisioned", old_basis),
    )

    assert candidate_rejected.startswith("Error:")
    assert "core basis is stale" in candidate_rejected
    assert write_rejected.startswith("Error:")
    assert "core basis is stale" in write_rejected
    assert call("stira_get_knowledge", crew_id="revisioned")["totalCount"] == 0


def test_write_schema_rejects_missing_basis_core_hash(stira_home: Path) -> None:
    init("missing-basis")
    with pytest.raises(Exception):
        mcp_server._validate_args(
            "stira_write_knowledge",
            {
                "crew_id": "missing-basis",
                "content": "No basis supplied.",
                "score": 0.7,
                "title": "Missing basis",
                "tags": ["invalid"],
            },
        )
# ── End SEC-3 candidate/write boundary regressions ─────────────────────────




# ── Public-release review F2: distilled knowledge is framed as data ────────
def test_knowledge_reads_carry_a_data_notice_and_mcp_writes_stay_below_core(
    stira_home: Path,
) -> None:
    initialized = init("framed")
    args = _knowledge_write_args("framed", initialized["coreHash"])
    args["score"] = 0.99
    written = call("stira_write_knowledge", **args)
    assert written["score"] < 0.8 and written["layer"] != "core"
    assert written["requestedScore"] == 0.99
    for tool in ("stira_tick", "stira_list_index", "stira_get_knowledge"):
        result = call(tool, crew_id="framed")
        assert "not instructions" in result["dataNotice"], tool
# ── End public-release review F2 ───────────────────────────────────────────
