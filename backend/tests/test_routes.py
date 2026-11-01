from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace

try:
    from kiro_crew.apps.route_registry import AppRoute as _RuntimeAppRoute  # noqa: F401
except ModuleNotFoundError:
    # The project test interpreter does not ship KiroCrew's host package.
    # Stub only the SDK value type needed to import routes.py; a separate smoke
    # check uses KiroCrew's bundled runtime and real AppManifest/AppRoute classes.
    @dataclass
    class _TestAppRoute:
        method: str
        path: str
        handler: object

    kiro_crew_module = ModuleType("kiro_crew")
    apps_module = ModuleType("kiro_crew.apps")
    route_registry_module = ModuleType("kiro_crew.apps.route_registry")
    route_registry_module.AppRoute = _TestAppRoute
    sys.modules.setdefault("kiro_crew", kiro_crew_module)
    sys.modules.setdefault("kiro_crew.apps", apps_module)
    sys.modules["kiro_crew.apps.route_registry"] = route_registry_module

from backend.hooks import CREW_REGISTRY_KEY, ONBOARDING_KEY
# ── SEC-3 route test core fixture ───────────────────────────────────────────
from backend.engine import compute_core_hash

_HARVEST_IPD = "# P0\nBuild correct software.\n"
_HARVEST_CORE_HASH = compute_core_hash(_HARVEST_IPD.encode("utf-8"))
# ── End SEC-3 route test core fixture ───────────────────────────────────────
from backend.harvest_log import append_run, read_runs
from backend.knowledge import KnowledgeStore
from backend import routes as routes_module
from backend.routes import (
    _candidates_key,
    _dispatch_key,
    _watch_spawn_completion,
    create_crew,
    get_crew_harvest_log,
    get_crew_knowledge,
    get_agents,
    get_crews,
    harvest_crew,
    prune_leaf,
    register_routes,
    unprune_leaf,
)


class FakeStorage:
    """Stand-in for AppStorage that enforces its real key constraint.

    The host writes one file per key at ``data/kv/{key}.json``.  A key that is
    not a portable filename therefore fails at write time on the platform, not
    at validation time — which is exactly how the colon in the old
    ``harvest:dispatch:{crew}`` key reached production as OSError(22) on
    Windows.  Rejecting it here keeps that class of bug inside the test suite.
    """

    _UNSAFE = re.compile(r'[:*?"<>|/\\]')

    def __init__(self, values=None):
        self.values = values or {}
        for key in self.values:
            self._check(key)

    def _check(self, key):
        if not key or self._UNSAFE.search(key) or key[0] in ".~":
            raise ValueError(f"invalid storage key: {key!r}")

    def get(self, key):
        self._check(key)
        return self.values.get(key)

    def set(self, key, value):
        self._check(key)
        self.values[key] = value


class FakeRequest:
    def __init__(self, body=None, query=None, **match_info):
        self.match_info = match_info
        self.query = query or {}
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSpawn:
    def __init__(self, *, spawn_id="spawn-123", error=None, done=False):
        self.spawn_id = spawn_id
        self.error = error
        self.done = done
        self.calls = []

    async def run(self, task, agent="", *, silent=False, model=""):
        self.calls.append(
            {"task": task, "agent": agent, "silent": silent, "model": model}
        )
        if self.error is not None:
            raise self.error
        return self.spawn_id

    def is_done(self, spawn_id):
        assert spawn_id == self.spawn_id
        return self.done


class FakeContext:
    def __init__(self, data_dir, storage, spawn=None):
        self.name = "stira"
        self.data_dir = data_dir
        self.storage = storage
        self.spawn = spawn
        self.logger = logging.getLogger("test.stira.routes")

def payload(response):
    return json.loads(response.body.decode("utf-8"))


def test_get_agents_returns_names_from_the_host_crew_home(tmp_path, monkeypatch):
    crew_home = tmp_path / ".kiro" / "crew"
    sessions = crew_home / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "session.jsonl").write_text(
        json.dumps({"agent": "default"}) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)

    response = asyncio.run(get_agents(FakeRequest(), SimpleNamespace()))

    assert response.status == 200
    assert payload(response) == {"agents": ["default"]}


def test_get_crews_returns_tree_response_shape(tmp_path):
    proposal = {
        "version": 1,
        "crewId": "dev-builder",
        "baseHash": "a" * 64,
        "candidateHash": "b" * 64,
        "proposedAt": "2026-09-11T00:00:00Z",
    }
    storage = FakeStorage(
        {
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {
                    "dev-builder": {
                        "coreHashHealthy": True,
                        "onboardingRequired": False,
                        "drift": {
                            "purpose": {"drifted": False},
                            "means": {"drifted": True, "score": 0.6},
                        },
                    }
                },
            },
            ONBOARDING_KEY: {"required": False, "crews": []},
            f"{routes_module.CORE_PROPOSAL_KEY_PREFIX}dev-builder": proposal,
        }
    )
    # Params live in the per-crew state.json, NOT in the registry record. The
    # tree view must read them from there or the trunk/branches show a dash.
    state_path = tmp_path / "crews" / "dev-builder" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"version": 1, "params": {"stability": 0.82, "growth": 0.37}}),
        encoding="utf-8",
    )
    ctx = FakeContext(tmp_path, storage)

    response = asyncio.run(get_crews(FakeRequest(), ctx))

    assert response.status == 200
    assert payload(response) == {
        "crews": [
            {
                "name": "dev-builder",
                "stability": 0.82,
                "growth": 0.37,
                "coreHashHealthy": True,
                "onboardingRequired": False,
                "coreProposalPending": True,
                "pendingCoreProposal": proposal,
                "drift": {
                    "detected": True,
                    "core": "healthy",
                    "purpose": {"drifted": False},
                    "means": {"drifted": True, "score": 0.6},
                },
                "fallenLeaves": [],
                "activeCount": 0,
                "archivedCount": 0,
            }
        ],
        "onboarding": {"required": False, "crews": []},
        "version": routes_module._read_app_version(),
        "buildHash": routes_module._compute_build_hash(),
    }


def test_get_crews_includes_version_and_build_fingerprint(tmp_path):
    storage = FakeStorage(
        {
            CREW_REGISTRY_KEY: {"version": 1, "crews": {}},
            ONBOARDING_KEY: {"required": False, "crews": []},
        }
    )
    ctx = FakeContext(tmp_path, storage)

    body = payload(asyncio.run(get_crews(FakeRequest(), ctx)))

    repo_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((repo_root / "app.json").read_text(encoding="utf-8"))
    assert body["version"] == manifest["version"]
    assert re.fullmatch(r"[0-9a-f]{8}", body["buildHash"])
    # Deterministic: same code hashes to the same fingerprint.
    assert body["buildHash"] == routes_module._compute_build_hash()


def test_get_crews_returns_recent_fallen_leaves_and_counts(tmp_path):
    store = KnowledgeStore(tmp_path, "dev-builder")
    fallen = store.write_entry(
        content="Old operational detail.",
        score=0.8,
        title="Fallen detail",
        tags=["ops"],
        domain="operations",
        now=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    store.prune(fallen["id"])
    active = store.write_entry(
        content="Current reusable detail.",
        score=0.8,
        title="Active detail",
        tags=["dev"],
        domain="engineering",
        now=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )
    storage = FakeStorage(
        {
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {"dev-builder": {"coreHashHealthy": True}},
            }
        }
    )

    body = payload(asyncio.run(get_crews(FakeRequest(), FakeContext(tmp_path, storage))))

    crew = body["crews"][0]
    leaves = crew["fallenLeaves"]
    assert crew["activeCount"] == 1
    assert crew["archivedCount"] == 1
    assert len(leaves) == 1
    assert leaves[0]["id"] == fallen["id"]
    assert leaves[0]["archived"] is True
    assert leaves[0]["archivedReason"] == "pruned"
    assert leaves[0]["domain"] == "operations"
    assert leaves[0]["life"] == leaves[0]["effectiveScore"]
    assert active["id"] not in {leaf["id"] for leaf in leaves}


def test_get_crews_shows_dash_when_state_json_missing(tmp_path):
    # A registered crew with no readable state.json must not error -- the tree
    # renders a dash (None) for stability/growth instead.
    storage = FakeStorage(
        {
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {"dev-builder": {"coreHashHealthy": True, "onboardingRequired": False}},
            },
            ONBOARDING_KEY: {"required": False, "crews": []},
        }
    )
    ctx = FakeContext(tmp_path, storage)

    response = asyncio.run(get_crews(FakeRequest(), ctx))

    assert response.status == 200
    crew = payload(response)["crews"][0]
    assert crew["stability"] is None
    assert crew["growth"] is None


def test_get_crews_fails_closed_without_storage(tmp_path):
    ctx = FakeContext(tmp_path, None)

    response = asyncio.run(get_crews(FakeRequest(), ctx))

    assert response.status == 503
    assert payload(response) == {"error": "storage_unavailable", "crews": []}


def test_get_crew_knowledge_returns_leaf_projection(tmp_path):
    KnowledgeStore(tmp_path, "dev-builder").write_entry(
        content="Run targeted tests after changing behavior.",
        score=0.75,
        title="Targeted validation",
        tags=["test"],
    )
    storage = FakeStorage(
        {
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {"dev-builder": {"name": "dev-builder"}},
            }
        }
    )
    ctx = FakeContext(tmp_path, storage)

    response = asyncio.run(
        get_crew_knowledge(FakeRequest(crew_id="dev-builder"), ctx)
    )
    body = payload(response)

    assert response.status == 200
    assert body["crewId"] == "dev-builder"
    assert body["totalCount"] == 1
    leaf = body["entries"][0]
    assert leaf["title"] == "Targeted validation"
    assert leaf["content"] == "Run targeted tests after changing behavior.\n"
    assert leaf["effectiveScore"] == leaf["ui"]["leafColorIntensity"]
    assert set(leaf["ui"]) == {"leafColorIntensity", "leafOpacity", "fadedFraction"}


def test_prune_leaf_archives_inside_crew_transaction(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path, "dev-builder")
    leaf = store.write_entry(
        content="Human decides whether this remains active.",
        score=0.8,
        title="Prunable leaf",
        tags=["governance"],
    )
    storage = FakeStorage(
        {
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {"dev-builder": {"name": "dev-builder"}},
            }
        }
    )
    lock_calls = []

    @contextmanager
    def observed_transaction(root, crew_id):
        lock_calls.append((Path(root), crew_id))
        yield

    monkeypatch.setattr("backend.knowledge.crew_transaction", observed_transaction)
    ctx = FakeContext(tmp_path, storage)

    response = asyncio.run(
        prune_leaf(
            FakeRequest(crew_id="dev-builder", leaf_id=leaf["id"]),
            ctx,
        )
    )
    missing = asyncio.run(
        prune_leaf(
            FakeRequest(crew_id="dev-builder", leaf_id="missing-leaf"),
            ctx,
        )
    )

    assert response.status == 200
    assert payload(response) == {
        "ok": True,
        "leafId": leaf["id"],
        "archived": True,
        "reason": "pruned",
    }
    assert missing.status == 404
    assert payload(missing) == {"error": "leaf_not_found", "leafId": "missing-leaf"}
    archived = store.load_index()[0]
    assert archived["archived"] is True
    assert archived["archivedReason"] == "pruned"
    assert archived["archivedAt"]
    assert lock_calls == [(tmp_path.resolve(), "dev-builder")] * 2


def test_unprune_leaf_restores_pruned_and_rejects_faded_or_missing(tmp_path):
    store = KnowledgeStore(tmp_path, "dev-builder")
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pruned = store.write_entry(
        content="Bring this back.", score=0.8, title="Pruned", tags=[], now=created
    )
    faded = store.write_entry(
        content="Natural fade.", score=1.0, title="Faded", tags=[], ttl_days=1, now=created
    )
    store.prune(pruned["id"], now=created)
    store.archive_faded(now=created + timedelta(days=2))
    storage = FakeStorage(
        {CREW_REGISTRY_KEY: {"version": 1, "crews": {"dev-builder": {}}}}
    )
    ctx = FakeContext(tmp_path, storage)

    restored = asyncio.run(unprune_leaf(
        FakeRequest(crew_id="dev-builder", leaf_id=pruned["id"]), ctx
    ))
    faded_response = asyncio.run(unprune_leaf(
        FakeRequest(crew_id="dev-builder", leaf_id=faded["id"]), ctx
    ))
    missing = asyncio.run(unprune_leaf(
        FakeRequest(crew_id="dev-builder", leaf_id="missing"), ctx
    ))

    assert restored.status == 200
    assert payload(restored) == {"ok": True, "leafId": pruned["id"], "archived": False}
    assert faded_response.status == 404
    assert payload(faded_response) == {"error": "leaf_not_found", "leafId": faded["id"]}
    assert missing.status == 404
    assert payload(missing) == {"error": "leaf_not_found", "leafId": "missing"}


def test_get_crew_knowledge_rejects_unknown_crew(tmp_path):
    ctx = FakeContext(
        tmp_path,
        FakeStorage(
            {
                CREW_REGISTRY_KEY: {
                    "version": 1,
                    "crews": {"known": {"name": "known"}},
                }
            }
        ),
    )

    response = asyncio.run(get_crew_knowledge(FakeRequest(crew_id="unknown"), ctx))

    assert response.status == 404
    assert payload(response) == {"error": "crew_not_found", "crewId": "unknown"}


def test_create_crew_registers_ipd_runtime_and_tree(tmp_path):
    storage = FakeStorage()
    ctx = FakeContext(tmp_path, storage)

    response = asyncio.run(
        create_crew(
            FakeRequest(
                body={
                    "crew_id": "dev-builder",
                    "ipd_content": "# P0\nBuild correct software.\n",
                }
            ),
            ctx,
        )
    )
    body = payload(response)

    assert response.status == 201
    assert body["crewId"] == "dev-builder"
    assert body["created"] is True
    assert body["coreHash"]
    crew_dir = tmp_path / "crews" / "dev-builder"
    assert (crew_dir / "IPD.md").read_text(encoding="utf-8").startswith("# P0")
    for filename in ("state.json", "matrix.json", "config.json", "purpose_rules.json"):
        assert (crew_dir / filename).is_file()
    assert storage.values[CREW_REGISTRY_KEY]["crews"]["dev-builder"][
        "coreHash"
    ] == body["coreHash"]
    assert storage.values[ONBOARDING_KEY]["required"] is False

    forest = payload(asyncio.run(get_crews(FakeRequest(), ctx)))
    assert [crew["name"] for crew in forest["crews"]] == ["dev-builder"]


def test_create_crew_requires_explicit_rebaseline_for_changed_ipd(tmp_path):
    storage = FakeStorage()
    ctx = FakeContext(tmp_path, storage)
    approved_content = "Protect least privilege.\n"
    candidate_content = "Grant broad access.\n"
    first = FakeRequest(
        body={"crew_id": "iam-guardian", "ipd_content": approved_content}
    )
    initial = payload(asyncio.run(create_crew(first, ctx)))

    # ── A-1 B-1: disk tamper followed by identical init must not self-approve ──
    crew_dir = tmp_path / "crews" / "iam-guardian"
    ipd_path = crew_dir / "IPD.md"
    ipd_path.write_text(candidate_content, encoding="utf-8")
    candidate_hash = compute_core_hash(candidate_content)
    conflict = asyncio.run(
        create_crew(
            FakeRequest(
                body={"crew_id": "iam-guardian", "ipd_content": candidate_content}
            ),
            ctx,
        )
    )
    conflict_body = payload(conflict)
    assert conflict.status == 409
    assert conflict_body["error"] == "rebaseline_required"
    assert conflict_body["approvedCoreHashBefore"] == initial["coreHash"]
    assert conflict_body["candidateCoreHash"] == candidate_hash
    assert conflict_body["diskHashMismatchBefore"] is True
    assert ipd_path.read_text(encoding="utf-8") == candidate_content

    proposal_key = f"{routes_module.CORE_PROPOSAL_KEY_PREFIX}iam-guardian"
    proposal = storage.values[proposal_key]
    assert proposal["baseHash"] == initial["coreHash"]
    assert proposal["candidateHash"] == candidate_hash
    proposal_snapshot = dict(proposal)
    repeated_conflict = asyncio.run(
        create_crew(
            FakeRequest(
                body={"crew_id": "iam-guardian", "ipd_content": candidate_content}
            ),
            ctx,
        )
    )
    assert repeated_conflict.status == 409
    assert storage.values[proposal_key] == proposal_snapshot
    forest_crew = payload(asyncio.run(get_crews(FakeRequest(), ctx)))["crews"][0]
    assert forest_crew["coreProposalPending"] is True
    assert forest_crew["pendingCoreProposal"] == proposal
    assert storage.values[CREW_REGISTRY_KEY]["crews"]["iam-guardian"][
        "coreHash"
    ] == initial["coreHash"]

    accepted = asyncio.run(
        create_crew(
            FakeRequest(
                body={
                    "crew_id": "iam-guardian",
                    "ipd_content": candidate_content,
                    "rebaseline": True,
                }
            ),
            ctx,
        )
    )
    accepted_body = payload(accepted)
    assert accepted.status == 200
    assert accepted_body["rebaselined"] is True
    assert accepted_body["diskHashMismatchBefore"] is True
    assert storage.values[proposal_key] is None

    events = [
        json.loads(line)
        for line in (crew_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-2]["kind"] == "core-change-proposed"
    assert events[-2]["diskHashMismatchBefore"] is True
    assert events[-1]["kind"] == "core-rebaselined"
    assert events[-1]["diskHashMismatchBefore"] is True
    # ── End A-1 B-1 ─────────────────────────────────────────────────────────


def test_create_crew_rejects_invalid_payload(tmp_path):
    ctx = FakeContext(tmp_path, FakeStorage())

    invalid_id = asyncio.run(
        create_crew(
            FakeRequest(body={"crew_id": "../escape", "ipd_content": "mission"}),
            ctx,
        )
    )
    empty_ipd = asyncio.run(
        create_crew(
            FakeRequest(body={"crew_id": "safe-crew", "ipd_content": "  "}),
            ctx,
        )
    )

    assert invalid_id.status == 400
    assert payload(invalid_id)["error"] == "invalid_request"
    assert empty_ipd.status == 400


def _harvest_storage(crew_id="dev-builder"):
    return FakeStorage(
        {
            CREW_REGISTRY_KEY: {
                "version": 1,
                "crews": {
                    crew_id: {
                        "name": crew_id,
                        # ── SEC-3 approved core fixture ─────────────────────
                        "coreHash": _HARVEST_CORE_HASH,
                        "coreHashHealthy": True,
                        # ── End SEC-3 approved core fixture ─────────────────
                    }
                },
            }
        }
    )


def _write_ipd(data_dir, crew_id="dev-builder"):
    path = data_dir / "crews" / crew_id / "IPD.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_HARVEST_IPD.encode("utf-8"))


def test_harvest_crew_collects_then_dispatches_restricted_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)
    _write_ipd(tmp_path)
    spawn = FakeSpawn()
    storage = _harvest_storage()
    ctx = FakeContext(tmp_path, storage, spawn)

    response = asyncio.run(harvest_crew(FakeRequest(crew_id="dev-builder"), ctx))

    assert response.status == 200
    body = payload(response)
    assert body["crewId"] == "dev-builder"
    assert body["status"] == "dispatching"
    assert body["spawnId"] == "spawn-123"
    assert body["runId"].startswith("manual-")
    assert len(spawn.calls) == 1
    call = spawn.calls[0]
    assert call["agent"] == "stira-distiller"
    assert call["model"] == "auto"
    assert call["silent"] is True
    assert 'crewId "dev-builder"' in call["task"]
    assert f'runId "{body["runId"]}"' in call["task"]
    assert "stira_get_harvest_candidates" in call["task"]
    assert 'crew_id="dev-builder"' in call["task"]
    assert "fs_read" not in call["task"]
    assert str(tmp_path) not in call["task"]
    assert "Do not read candidate files" in call["task"]

    candidate = storage.values["harvest-candidates-dev-builder"]
    assert candidate["schema"] == "stira.harvest.input.v1"
    assert candidate["runId"] == body["runId"]
    assert candidate["crewId"] == "dev-builder"
    # ── SEC-3 staged package basis regression ──────────────────────────────
    assert candidate["coreHashHealthy"] is True
    assert candidate["basisCoreRevisionId"] == _HARVEST_CORE_HASH
    assert candidate["basisCoreHash"] == _HARVEST_CORE_HASH
    # ── End SEC-3 staged package basis regression ──────────────────────────
    assert [crew["crew"] for crew in candidate["crews"]] == ["dev-builder"]
    dispatch = storage.values["harvest-dispatch-dev-builder"]
    assert dispatch["runId"] == body["runId"]
    assert dispatch["spawnId"] == "spawn-123"
    assert dispatch["status"] == "dispatching"


def test_harvest_crew_rejects_fresh_in_progress_dispatch(tmp_path):
    run_id = "manual-existing01"
    storage = _harvest_storage()
    storage.set(
        "harvest-dispatch-dev-builder",
        {
            "runId": run_id,
            "status": "dispatching",
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    spawn = FakeSpawn()

    response = asyncio.run(
        harvest_crew(
            FakeRequest(crew_id="dev-builder"),
            FakeContext(tmp_path, storage, spawn),
        )
    )

    assert response.status == 409
    assert payload(response) == {
        "error": "harvest_in_progress",
        "crewId": "dev-builder",
        "runId": run_id,
    }
    assert spawn.calls == []


def test_harvest_crew_allows_stale_or_terminal_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)
    _write_ipd(tmp_path)
    old_dispatches = (
        {
            "runId": "manual-stale0001",
            "status": "dispatching",
            "requestedAt": (
                datetime.now(timezone.utc) - timedelta(minutes=11)
            ).isoformat(),
        },
        {
            "runId": "manual-completed01",
            "status": "completed",
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    for old_dispatch in old_dispatches:
        storage = _harvest_storage()
        storage.set("harvest-dispatch-dev-builder", old_dispatch)
        spawn = FakeSpawn()

        response = asyncio.run(
            harvest_crew(
                FakeRequest(crew_id="dev-builder"),
                FakeContext(tmp_path, storage, spawn),
            )
        )

        assert response.status == 200
        assert len(spawn.calls) == 1
        assert storage.values["harvest-dispatch-dev-builder"]["status"] == "dispatching"


def test_harvest_crew_rejects_unknown_crew_without_spawning(tmp_path):
    spawn = FakeSpawn()
    ctx = FakeContext(tmp_path, _harvest_storage("known"), spawn)

    response = asyncio.run(harvest_crew(FakeRequest(crew_id="unknown"), ctx))

    assert response.status == 404
    assert payload(response) == {"error": "crew_not_found", "crewId": "unknown"}
    assert spawn.calls == []


def test_harvest_crew_reports_explicit_unavailable_without_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)
    _write_ipd(tmp_path)
    ctx = FakeContext(tmp_path, _harvest_storage())

    response = asyncio.run(harvest_crew(FakeRequest(crew_id="dev-builder"), ctx))

    assert response.status == 503
    body = payload(response)
    assert body["error"] == "harvest_unavailable"
    assert body["crewId"] == "dev-builder"
    assert body["detail"] == (
        "RuntimeError: spawn capability unavailable (declare permissions.spawn)"
    )
    assert "harvest-candidates-dev-builder" in ctx.storage.values
    dispatch = ctx.storage.values["harvest-dispatch-dev-builder"]
    assert dispatch["status"] == "failed"
    assert dispatch["spawnId"] is None


def test_harvest_crew_records_failed_dispatch_when_spawn_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)
    _write_ipd(tmp_path)
    storage = _harvest_storage()
    spawn = FakeSpawn(error=RuntimeError("host declined"))

    response = asyncio.run(
        harvest_crew(
            FakeRequest(crew_id="dev-builder"),
            FakeContext(tmp_path, storage, spawn),
        )
    )

    assert response.status == 503
    dispatch = storage.values["harvest-dispatch-dev-builder"]
    assert dispatch["status"] == "failed"
    assert dispatch["spawnId"] is None
    assert dispatch["detail"] == "RuntimeError: host declined"


def test_harvest_crew_retries_transient_app_spawn_queue_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)
    original_sleep = asyncio.sleep

    async def skip_retry_delay(seconds):
        if seconds == 2.1:
            return
        await original_sleep(seconds)

    monkeypatch.setattr("backend.routes.asyncio.sleep", skip_retry_delay)
    _write_ipd(tmp_path)

    class QueueOnceSpawn(FakeSpawn):
        async def run(self, task, agent="", *, silent=False, model=""):
            if not self.calls:
                self.calls.append(
                    {"task": task, "agent": agent, "silent": silent, "model": model}
                )
                raise RuntimeError(
                    "spawn queue is at capacity; the app spawn was not queued "
                    "to avoid a stale ownership check — retry to revalidate and spawn"
                )
            return await super().run(task, agent, silent=silent, model=model)

    storage = _harvest_storage()
    spawn = QueueOnceSpawn()
    response = asyncio.run(
        harvest_crew(
            FakeRequest(crew_id="dev-builder"),
            FakeContext(tmp_path, storage, spawn),
        )
    )

    assert response.status == 200
    assert len(spawn.calls) == 2
    dispatch = storage.values["harvest-dispatch-dev-builder"]
    assert dispatch["status"] == "dispatching"
    assert dispatch["spawnId"] == "spawn-123"


def test_spawn_watcher_settles_dispatch_from_terminal_harvest_phase(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.routes._SPAWN_PROBE_INTERVAL_SECONDS", 0)
    run_id = "manual-watch0001"
    storage = _harvest_storage()
    storage.set(
        "harvest-dispatch-dev-builder",
        {
            "runId": run_id,
            "spawnId": "spawn-123",
            "status": "dispatching",
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        },
    )
    append_run(
        tmp_path,
        "dev-builder",
        {"runId": run_id, "phase": "completed", "writes": {"written": 1}},
    )
    ctx = FakeContext(tmp_path, storage, FakeSpawn(done=True))

    asyncio.run(_watch_spawn_completion(ctx, "dev-builder", run_id, "spawn-123"))

    dispatch = storage.values["harvest-dispatch-dev-builder"]
    assert dispatch["status"] == "completed"
    assert dispatch["finishedAt"].endswith("Z")

def test_get_crew_harvest_log_returns_folded_runs_and_dispatch(tmp_path):
    append_run(tmp_path, "dev-builder", {
        "runId": "manual-00000001", "phase": "started", "stiraHome": str(tmp_path),
    })
    append_run(tmp_path, "dev-builder", {
        "runId": "manual-00000001", "phase": "completed", "writes": {"written": 0},
    })
    dispatch = {"runId": "manual-00000001", "status": "returned"}
    storage = FakeStorage({
        CREW_REGISTRY_KEY: {"version": 1, "crews": {"dev-builder": {}}},
        "harvest-dispatch-dev-builder": dispatch,
    })

    response = asyncio.run(get_crew_harvest_log(
        FakeRequest(crew_id="dev-builder", query={"limit": "1"}),
        FakeContext(tmp_path, storage),
    ))
    body = payload(response)

    assert response.status == 200
    assert body["dispatch"] == dispatch
    assert len(body["runs"]) == 1
    assert body["runs"][0]["phase"] == "completed"
    assert body["runs"][0]["stiraHome"] == str(tmp_path)
    assert body["runs"][0]["writes"]["written"] == 0


def test_append_run_drops_top_level_unknown_fields(tmp_path):
    # The real distilled-phase bug: agent put candidates/distilled/accepted/
    # rejected at the TOP level instead of under `counts`. Lenient schema must
    # drop the unknowns and keep the record instead of raising 'unsupported
    # harvest record fields', which failed the whole harvest.
    append_run(tmp_path, "default", {
        "runId": "manual-topcounts", "phase": "distilled",
        "candidates": 5, "distilled": 3, "accepted": 2, "rejected": 1,
        "counts": {"candidates": 5, "distilled": 3},  # correct nested form survives
    })
    runs = {r["runId"]: r for r in read_runs(tmp_path, "default")}
    rec = runs["manual-topcounts"]
    assert "candidates" not in rec and "accepted" not in rec  # top-level dropped
    assert rec["counts"] == {"candidates": 5, "distilled": 3}  # nested kept


def test_append_run_normalizes_agents_seen(tmp_path):
    # The distill agent crashed the main/collected phase by adding an extra
    # field (sessionKey) to an agentsSeen item; the record must survive and
    # normalize instead of raising 'unsupported fields'.
    append_run(tmp_path, "default", {
        "runId": "manual-agents", "phase": "collected",
        "agentsSeen": [
            {"agent": "default", "count": 3, "sessionKey": "dashboard_chat-1"},
            {"agent": "iam-guardian", "count": "2"},
            {"count": 5},
            "garbage",
        ],
    })
    runs = {r["runId"]: r for r in read_runs(tmp_path, "default")}
    seen = {a["agent"]: a["count"] for a in runs["manual-agents"]["agentsSeen"]}
    assert seen == {"default": 3, "iam-guardian": 2}


def test_append_run_coerces_loosely_typed_crews_discovered(tmp_path):
    # A loosely-typed distill agent used to crash the setup/started phase by
    # passing crewsDiscovered as a numeric string; the record must survive and
    # normalize instead of raising (regression for the harvest-log ValueError).
    append_run(tmp_path, "dev-builder", {
        "runId": "manual-str", "phase": "started", "crewsDiscovered": "2",
    })
    append_run(tmp_path, "dev-builder", {
        "runId": "manual-float", "phase": "started", "crewsDiscovered": 3.0,
    })
    # Uncoercible values are dropped, not fatal.
    append_run(tmp_path, "dev-builder", {
        "runId": "manual-bad", "phase": "started", "crewsDiscovered": "many",
    })

    runs = {r["runId"]: r for r in read_runs(tmp_path, "dev-builder")}
    assert runs["manual-str"]["crewsDiscovered"] == 2
    assert runs["manual-float"]["crewsDiscovered"] == 3
    assert "crewsDiscovered" not in runs["manual-bad"]


def test_get_crew_harvest_log_missing_file_is_empty_200(tmp_path):
    storage = FakeStorage({
        CREW_REGISTRY_KEY: {"version": 1, "crews": {"dev-builder": {}}},
    })

    response = asyncio.run(get_crew_harvest_log(
        FakeRequest(crew_id="dev-builder"), FakeContext(tmp_path, storage),
    ))

    assert response.status == 200
    assert payload(response) == {"crewId": "dev-builder", "dispatch": None, "runs": []}


def test_get_crew_harvest_log_rejects_unknown_crew_and_missing_storage(tmp_path):
    unknown = asyncio.run(get_crew_harvest_log(
        FakeRequest(crew_id="unknown"),
        FakeContext(tmp_path, FakeStorage({CREW_REGISTRY_KEY: {"version": 1, "crews": {}}})),
    ))
    unavailable = asyncio.run(get_crew_harvest_log(
        FakeRequest(crew_id="dev-builder"), FakeContext(tmp_path, None),
    ))

    assert unknown.status == 404
    assert payload(unknown)["error"] == "crew_not_found"
    assert unavailable.status == 503
    assert payload(unavailable) == {"error": "storage_unavailable", "runs": []}


def test_dispatch_key_is_a_writable_filename_on_this_platform(tmp_path):
    """AppStorage writes data/kv/{key}.json, so the key must be a legal filename.

    This is the regression guard for the 503: the previous
    ``harvest:dispatch:{crew}`` key passed AppStorage's traversal check and then
    failed inside atomic_write with OSError(22) on Windows, because a colon is
    an alternate-data-stream separator there.  Asserting against the real
    filesystem keeps the check honest on every platform CI runs on.
    """
    dispatch_key = _dispatch_key("infra-operator")
    candidates_key = _candidates_key("infra-operator")

    assert dispatch_key == "harvest-dispatch-infra-operator"
    assert candidates_key == "harvest-candidates-infra-operator"
    for key in (dispatch_key, candidates_key):
        path = tmp_path / f"{key}.json"
        path.write_text("{}", encoding="utf-8")
        assert path.is_file()


def test_dispatch_key_rejects_a_non_portable_crew_id():
    for crew_id in ("bad:crew", "bad/crew", "bad\\crew", 'bad"crew', "bad|crew"):
        try:
            _dispatch_key(crew_id)
        except ValueError:
            continue
        raise AssertionError(f"expected {crew_id!r} to be rejected")


def test_register_routes_declares_forest_endpoints():
    routes = register_routes(SimpleNamespace())

    assert [(route.method, route.path) for route in routes] == [
        ("GET", "/agents"),
        ("GET", "/crews"),
        ("POST", "/crews"),
        ("GET", "/crews/{crew_id}/knowledge"),
        ("POST", "/crews/{crew_id}/leaves/{leaf_id}/prune"),
        ("POST", "/crews/{crew_id}/leaves/{leaf_id}/unprune"),
        ("GET", "/crews/{crew_id}/harvest-log"),
        ("POST", "/crews/{crew_id}/harvest"),
        ("GET", "/schedule"),
        ("POST", "/schedule/{cron_name}"),
    ]


def test_manifest_declares_spawn_and_restricted_distiller_agent():
    repo_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((repo_root / "app.json").read_text(encoding="utf-8"))
    assert manifest["permissions"]["spawn"] is True
    assert manifest["permissions"]["mcpTools"] == ["send_message"]
    assert manifest["agents"] == ["agents/stira-distiller.json"]

    agent = json.loads(
        (repo_root / "agents" / "stira-distiller.json").read_text(encoding="utf-8")
    )
    assert agent["name"] == "stira-distiller"
    assert agent["model"] == "auto"
    # The distiller is bound to the five-tool distiller endpoint allowlist, not
    # the full operator server.
    assert agent["tools"] == [
        "@stira:distiller/stira_get_harvest_candidates",
        "@stira:distiller/stira_get_knowledge",
        "@stira:distiller/stira_record_harvest_run",
        "@stira:distiller/stira_suggest_score",
        "@stira:distiller/stira_write_knowledge",
    ]
    assert {
        "spawn_run",
        "cron_add",
        "cron_remove",
        "cron_update",
        "cron_remove_all",
        "task_run",
    }.issubset(agent["managedToolPolicy"]["exclude"])


# ── SEC-3 harvest boundary regressions ─────────────────────────────────────
def test_harvest_crew_skips_before_collection_when_live_core_is_tampered(
    tmp_path, monkeypatch
):
    _write_ipd(tmp_path)
    (tmp_path / "crews" / "dev-builder" / "IPD.md").write_text(
        "Tampered mission.\n", encoding="utf-8"
    )
    storage = _harvest_storage()
    spawn = FakeSpawn()

    def collection_must_not_run(*_args, **_kwargs):
        raise AssertionError("collection ran for a tampered core")

    monkeypatch.setattr(routes_module, "collect_harvest_input", collection_must_not_run)
    response = asyncio.run(
        harvest_crew(
            FakeRequest(crew_id="dev-builder"),
            FakeContext(tmp_path, storage, spawn),
        )
    )

    assert response.status == 200
    assert payload(response)["skip"] == "core_integrity"
    assert payload(response)["status"] == "skipped"
    assert spawn.calls == []
    assert "harvest-candidates-dev-builder" not in storage.values
    assert "harvest-dispatch-dev-builder" not in storage.values


def test_harvest_crew_rechecks_live_core_immediately_before_spawn(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("backend.routes.Path.home", lambda: tmp_path)
    _write_ipd(tmp_path)
    storage = _harvest_storage()
    spawn = FakeSpawn()
    original_collect = routes_module.collect_harvest_input

    def collect_then_tamper(*args, **kwargs):
        result = original_collect(*args, **kwargs)
        (tmp_path / "crews" / "dev-builder" / "IPD.md").write_text(
            "Changed after collection.\n", encoding="utf-8"
        )
        return result

    monkeypatch.setattr(routes_module, "collect_harvest_input", collect_then_tamper)
    response = asyncio.run(
        harvest_crew(
            FakeRequest(crew_id="dev-builder"),
            FakeContext(tmp_path, storage, spawn),
        )
    )

    assert response.status == 200
    assert payload(response)["skip"] == "core_integrity"
    assert spawn.calls == []
    candidate = storage.values["harvest-candidates-dev-builder"]
    assert candidate["basisCoreHash"] == _HARVEST_CORE_HASH
    assert "harvest-dispatch-dev-builder" not in storage.values
# ── End SEC-3 harvest boundary regressions ─────────────────────────────────
