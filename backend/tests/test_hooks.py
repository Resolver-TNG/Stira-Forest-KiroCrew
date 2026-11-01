import asyncio
import logging

from backend.engine import compute_core_hash
from backend.hooks import (
    CREW_REGISTRY_KEY,
    ONBOARDING_KEY,
    _reset_for_tests,
    on_shutdown,
    on_startup,
)


class FakeStorage:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


class FakeHealth:
    def __init__(self):
        self.issues = []

    def mark_degraded(self, issue):
        self.issues.append(issue)


class FakeContext:
    def __init__(self, data_dir, crews, storage=None):
        self.data_dir = data_dir
        self.config = {"crews": crews}
        self.storage = FakeStorage() if storage is None else storage
        self.logger = logging.getLogger("test.stira.hooks")
        self.health = FakeHealth()


def teardown_function():
    _reset_for_tests()


def test_on_startup_keeps_approved_matching_baseline_healthy(tmp_path):
    content = b"# P0\nBuild correct software without drifting from the mission.\n"
    crew_dir = tmp_path / "crews" / "dev-builder"
    crew_dir.mkdir(parents=True)
    (crew_dir / "IPD.md").write_bytes(content)
    (crew_dir / "state.json").write_text('{"version": 1}\n', encoding="utf-8")
    expected = compute_core_hash(content)
    storage = FakeStorage()
    storage.values[CREW_REGISTRY_KEY] = {
        "version": 1,
        "crews": {"dev-builder": {"coreHash": expected}},
    }
    ctx = FakeContext(tmp_path, ["dev-builder"], storage=storage)

    asyncio.run(on_startup(ctx))

    crew = ctx.storage.values[CREW_REGISTRY_KEY]["crews"]["dev-builder"]
    assert crew["coreHash"] == expected
    assert crew["coreHashCurrent"] == expected
    assert crew["coreHashHealthy"] is True
    assert crew["onboardingRequired"] is False
    assert ctx.storage.values[ONBOARDING_KEY]["required"] is False
    assert ctx.health.issues == []


def test_on_startup_does_not_silently_rebaseline_when_core_hash_is_missing(tmp_path):
    content = b"# P0\nProtect the approved mission.\n"
    crew_dir = tmp_path / "crews" / "dev-builder"
    crew_dir.mkdir(parents=True)
    (crew_dir / "IPD.md").write_bytes(content)
    (crew_dir / "state.json").write_text('{"version": 1}\n', encoding="utf-8")
    storage = FakeStorage()
    storage.values[CREW_REGISTRY_KEY] = {
        "version": 1,
        "crews": {"dev-builder": {"name": "dev-builder"}},
    }
    ctx = FakeContext(tmp_path, ["dev-builder"], storage=storage)

    asyncio.run(on_startup(ctx))

    crew = ctx.storage.values[CREW_REGISTRY_KEY]["crews"]["dev-builder"]
    assert "coreHash" not in crew
    assert crew["coreHashCurrent"] == compute_core_hash(content)
    assert crew["coreHashHealthy"] is False
    assert crew["onboardingRequired"] is True
    assert ctx.storage.values[ONBOARDING_KEY]["required"] is True
    assert ctx.storage.values[ONBOARDING_KEY]["crews"] == ["dev-builder"]
    assert any("human approval is required" in issue for issue in ctx.health.issues)
    assert any("no approved coreHash" in issue for issue in ctx.health.issues)


def test_on_startup_flags_missing_and_empty_ipd_for_onboarding(tmp_path):
    empty_ipd = tmp_path / "crews" / "empty-crew" / "IPD.md"
    empty_ipd.parent.mkdir(parents=True)
    empty_ipd.write_text("  \n", encoding="utf-8")
    ctx = FakeContext(tmp_path, ["missing-crew", "empty-crew"])

    asyncio.run(on_startup(ctx))

    registry = ctx.storage.values[CREW_REGISTRY_KEY]["crews"]
    assert registry["missing-crew"]["onboardingRequired"] is True
    assert registry["empty-crew"]["onboardingRequired"] is True
    onboarding = ctx.storage.values[ONBOARDING_KEY]
    assert onboarding["required"] is True
    assert onboarding["question"] == "このAIの芯は何ですか？"
    assert onboarding["crews"] == ["empty-crew", "missing-crew"]


def test_on_startup_preserves_baseline_and_detects_changed_core(tmp_path):
    ipd = tmp_path / "crews" / "iam-guardian" / "IPD.md"
    ipd.parent.mkdir(parents=True)
    original = b"Protect least privilege.\n"
    changed = b"Grant broad access by default.\n"
    ipd.write_bytes(changed)
    baseline = compute_core_hash(original)
    storage = FakeStorage()
    storage.values[CREW_REGISTRY_KEY] = {
        "version": 1,
        "crews": {"iam-guardian": {"coreHash": baseline}},
    }
    ctx = FakeContext(tmp_path, ["iam-guardian"], storage=storage)

    asyncio.run(on_startup(ctx))

    crew = ctx.storage.values[CREW_REGISTRY_KEY]["crews"]["iam-guardian"]
    assert crew["coreHash"] == baseline
    assert crew["coreHashCurrent"] == compute_core_hash(changed)
    assert crew["coreHashHealthy"] is False


def test_storage_permission_absence_is_safe_and_shutdown_releases_state(tmp_path):
    ctx = FakeContext(tmp_path, ["dev-builder"], storage=False)
    ctx.storage = None

    asyncio.run(on_startup(ctx))
    asyncio.run(on_shutdown(ctx))

    assert ctx.health.issues



def test_on_startup_does_not_register_harvest_through_private_cron_service(tmp_path):
    class FailingCron:
        @property
        def _cron(self):
            raise AssertionError("startup must not access private CronService")

    ctx = FakeContext(tmp_path, [])
    ctx.cron = FailingCron()

    asyncio.run(on_startup(ctx))

    assert ctx.health.issues == []


def test_on_startup_restores_missing_registered_crew_and_creates_new_backup(tmp_path):
    from datetime import datetime, timezone
    import shutil

    from backend.data_guard import create_backup

    crew = tmp_path / "crews" / "dev-builder"
    crew.mkdir(parents=True)
    (crew / "IPD.md").write_text("Protect the mission.\n", encoding="utf-8")
    (crew / "state.json").write_text('{"version": 1}\n', encoding="utf-8")
    create_backup(tmp_path, now=datetime(2026, 9, 10, tzinfo=timezone.utc))
    shutil.rmtree(crew)

    storage = FakeStorage()
    storage.values[CREW_REGISTRY_KEY] = {
        "version": 1,
        "crews": {"dev-builder": {"name": "dev-builder"}},
    }
    ctx = FakeContext(tmp_path, [], storage=storage)

    asyncio.run(on_startup(ctx))

    assert (crew / "state.json").is_file()
    assert (tmp_path / ".schema_version").read_text(encoding="utf-8") == "1\n"
    generations = sorted((tmp_path / ".backups").iterdir())
    assert len(generations) == 2
    assert any("automatically restored" in issue for issue in ctx.health.issues)
    assert not any("state.json is missing" in issue for issue in ctx.health.issues)


def test_on_startup_sweeps_faded_leaves_for_configured_crews(tmp_path):
    # A crew whose harvest has stopped still ages out faded leaves at startup:
    # the write-driven fade path never fires without a write.
    from datetime import datetime, timedelta, timezone

    from backend.knowledge import KnowledgeStore

    content = b"# P0\nBuild correct software.\n"
    crew_dir = tmp_path / "crews" / "dev-builder"
    crew_dir.mkdir(parents=True)
    (crew_dir / "IPD.md").write_bytes(content)
    (crew_dir / "state.json").write_text('{"version": 1}\n', encoding="utf-8")

    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store = KnowledgeStore(tmp_path, "dev-builder")
    written = store.write_entry(
        content="Time-limited operational note.",
        score=1.0,
        title="Expiring note",
        tags=["ops"],
        ttl_days=10,
        now=created,
    )
    # Still active right after writing; only decay past its ttl fades it.
    assert store.load_index()[0].get("archived", False) is False

    storage = FakeStorage()
    storage.values[CREW_REGISTRY_KEY] = {
        "version": 1,
        "crews": {"dev-builder": {"coreHash": compute_core_hash(content)}},
    }
    ctx = FakeContext(tmp_path, ["dev-builder"], storage=storage)

    asyncio.run(on_startup(ctx))

    entry = store.load_index()[0]
    assert entry["id"] == written["id"]
    assert entry["archived"] is True
    assert entry["archivedReason"] == "faded"


def test_on_startup_faded_sweep_survives_without_storage(tmp_path):
    # No storage permission: startup returns early but must not crash on the
    # sweep wiring, and must not have swept anything.
    ctx = FakeContext(tmp_path, ["dev-builder"], storage=None)
    ctx.storage = None

    asyncio.run(on_startup(ctx))

    assert ctx.health.issues  # degraded notice recorded, no exception raised
