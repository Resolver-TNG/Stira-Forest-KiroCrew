from datetime import datetime, timedelta, timezone
import logging
import shutil

from backend import data_guard


def _write_crew(data_dir, crew_id="dev-builder", state='{"version": 1}\n'):
    crew = data_dir / "crews" / crew_id
    crew.mkdir(parents=True, exist_ok=True)
    (crew / "state.json").write_text(state, encoding="utf-8")
    (crew / "IPD.md").write_text("# P0\nBuild safely.\n", encoding="utf-8")
    return crew


def test_schema_marker_is_created_and_read(tmp_path):
    assert data_guard.ensure_schema_version(tmp_path) == 1
    assert (tmp_path / ".schema_version").read_text(encoding="utf-8") == "1\n"
    assert data_guard.ensure_schema_version(tmp_path) == 1


def test_backup_keeps_only_the_newest_five_generations(tmp_path):
    crew = _write_crew(tmp_path)
    start = datetime(2026, 9, 10, tzinfo=timezone.utc)

    for index in range(7):
        (crew / "state.json").write_text(str(index), encoding="utf-8")
        data_guard.create_backup(tmp_path, now=start + timedelta(seconds=index))

    generations = sorted((tmp_path / ".backups").iterdir())
    assert len(generations) == 5
    assert generations[0].name.startswith("20260910T000002")
    assert (generations[-1] / "crews" / "dev-builder" / "state.json").read_text(
        encoding="utf-8"
    ) == "6"


def test_missing_crew_is_restored_from_latest_backup_without_overwriting_live_files(
    tmp_path,
):
    crew = _write_crew(tmp_path, state="backed-up-state")
    data_guard.create_backup(
        tmp_path, now=datetime(2026, 9, 10, tzinfo=timezone.utc)
    )
    shutil.rmtree(crew)
    crew.mkdir(parents=True)
    (crew / "IPD.md").write_text("newer live IPD", encoding="utf-8")
    degraded = []

    recovery = data_guard.recover_if_needed_fail_soft(
        tmp_path,
        {"dev-builder": {}},
        logging.getLogger("test.stira.data_guard"),
        degraded.append,
    )

    assert recovery is not None
    assert recovery.restored_crews == ("dev-builder",)
    assert (crew / "state.json").read_text(encoding="utf-8") == "backed-up-state"
    assert (crew / "IPD.md").read_text(encoding="utf-8") == "newer live IPD"
    assert any("automatically restored" in issue for issue in degraded)


def test_backup_failure_is_fail_soft(tmp_path, monkeypatch, caplog):
    _write_crew(tmp_path)

    def fail_backup(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(data_guard, "create_backup", fail_backup)
    with caplog.at_level(logging.WARNING):
        result = data_guard.backup_crews_fail_soft(
            tmp_path, logging.getLogger("test.stira.data_guard")
        )

    assert result is None
    assert "backup failed" in caplog.text
    assert "locked" in caplog.text


def test_restore_failure_is_fail_soft_and_integrity_remains_visible(
    tmp_path, monkeypatch, caplog
):
    _write_crew(tmp_path)
    data_guard.create_backup(
        tmp_path, now=datetime(2026, 9, 10, tzinfo=timezone.utc)
    )
    shutil.rmtree(tmp_path / "crews")

    def fail_restore(*args, **kwargs):
        raise PermissionError("restore locked")

    monkeypatch.setattr(data_guard, "restore_latest_backup", fail_restore)
    degraded = []
    with caplog.at_level(logging.WARNING):
        recovery = data_guard.recover_if_needed_fail_soft(
            tmp_path,
            {"dev-builder": {}},
            logging.getLogger("test.stira.data_guard"),
            degraded.append,
        )
        report = data_guard.validate_integrity_fail_soft(
            tmp_path,
            {"dev-builder": {}},
            logging.getLogger("test.stira.data_guard"),
            degraded.append,
        )

    assert recovery is None
    assert report is not None
    assert report.missing_state_crews == ("dev-builder",)
    assert "startup will continue" in caplog.text
    assert any("state.json is missing" in issue for issue in degraded)


def test_integrity_detects_ipd_without_approved_core_hash(tmp_path):
    _write_crew(tmp_path)

    report = data_guard.inspect_integrity(
        tmp_path,
        {"dev-builder": {"name": "dev-builder"}},
    )

    assert report.missing_core_hash_crews == ("dev-builder",)
    assert any("no approved coreHash" in issue for issue in report.issues)

    approved = data_guard.inspect_integrity(
        tmp_path,
        {"dev-builder": {"coreHash": "sha256:approved"}},
    )
    assert approved.missing_core_hash_crews == ()


def test_backup_snapshots_crews_and_kv_but_excludes_logs(tmp_path):
    _write_crew(tmp_path)
    kv = tmp_path / "kv"
    kv.mkdir()
    (kv / "crew_registry.json").write_text(
        '{"version": 1, "crews": {"dev-builder": {"coreHash": "approved"}}}\n',
        encoding="utf-8",
    )
    (kv / "onboarding.json").write_text('{"pending": []}\n', encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "backend.log").write_text("open runtime log\n", encoding="utf-8")

    snapshot = data_guard.create_backup(
        tmp_path, now=datetime(2026, 9, 10, tzinfo=timezone.utc)
    )

    assert (snapshot / "crews" / "dev-builder" / "state.json").is_file()
    assert (snapshot / "kv" / "crew_registry.json").read_text(
        encoding="utf-8"
    ).startswith('{"version": 1')
    assert (snapshot / "kv" / "onboarding.json").is_file()
    assert not (snapshot / "logs").exists()


def test_missing_kv_is_restored_from_latest_backup(tmp_path):
    _write_crew(tmp_path)
    kv = tmp_path / "kv"
    kv.mkdir()
    registry = '{"version": 1, "crews": {"dev-builder": {"coreHash": "approved"}}}\n'
    onboarding = '{"pending": ["dev-builder"]}\n'
    (kv / "crew_registry.json").write_text(registry, encoding="utf-8")
    (kv / "onboarding.json").write_text(onboarding, encoding="utf-8")
    data_guard.create_backup(
        tmp_path, now=datetime(2026, 9, 10, tzinfo=timezone.utc)
    )
    shutil.rmtree(kv)
    degraded = []

    recovery = data_guard.recover_if_needed_fail_soft(
        tmp_path,
        {},
        logging.getLogger("test.stira.data_guard"),
        degraded.append,
    )

    assert recovery is not None
    assert (kv / "crew_registry.json").read_text(encoding="utf-8") == registry
    assert (kv / "onboarding.json").read_text(encoding="utf-8") == onboarding
    assert any("kv files" in issue for issue in degraded)


def test_kv_restore_fills_only_missing_files_without_overwriting_live_values(tmp_path):
    _write_crew(tmp_path)
    kv = tmp_path / "kv"
    kv.mkdir()
    (kv / "crew_registry.json").write_text("backed-up registry", encoding="utf-8")
    (kv / "onboarding.json").write_text("backed-up onboarding", encoding="utf-8")
    data_guard.create_backup(
        tmp_path, now=datetime(2026, 9, 10, tzinfo=timezone.utc)
    )
    (kv / "crew_registry.json").write_text("newer live registry", encoding="utf-8")
    (kv / "onboarding.json").unlink()

    recovery = data_guard.restore_latest_backup(tmp_path)

    assert recovery is not None
    assert (
        kv / "crew_registry.json"
    ).read_text(encoding="utf-8") == "newer live registry"
    assert (
        kv / "onboarding.json"
    ).read_text(encoding="utf-8") == "backed-up onboarding"
