from __future__ import annotations

from contextlib import contextmanager
import multiprocessing
from pathlib import Path
from typing import Any, Iterator

from backend import locks
from backend.harvest import write_entry_if_new as harvest_write_entry_if_new
from backend.harvest_log import append_run, read_runs
from backend.knowledge import KnowledgeStore


def _write_entry(root: str, crew_id: str, number: int) -> None:
    KnowledgeStore(root, crew_id).write_entry(
        content=f"Concurrent entry {number}",
        score=0.7,
        title=f"Concurrent {number}",
        tags=["concurrency"],
    )


def _write_fingerprint(root: str, fingerprint: str) -> bool:
    result = KnowledgeStore(root, "dev-builder").write_entry_if_new(
        fingerprint=fingerprint,
        content="Exactly-once harvested knowledge.",
        score=0.75,
        title="Exactly once",
        tags=["harvest"],
    )
    return bool(result["written"])


def _write_fingerprint_through_harvest_helper(root: str, fingerprint: str) -> None:
    harvest_write_entry_if_new(
        KnowledgeStore(root, "legacy-harvest"),
        fingerprint=fingerprint,
        content="Exactly-once legacy harvest knowledge.",
        score=0.75,
        title="Exactly once legacy",
        tags=["harvest"],
    )


def _hold_crew_lock(
    root: str,
    crew_id: str,
    acquired: Any,
    release: Any,
) -> None:
    with locks.crew_transaction(root, crew_id):
        acquired.set()
        if not release.wait(10):
            raise TimeoutError("test did not release crew transaction")


def _append_harvest_run(root: str, number: int) -> None:
    append_run(
        root,
        "dev-builder",
        {"runId": f"parallel-{number:08d}", "phase": "started"},
    )


def test_same_crew_multiprocess_writes_preserve_all_entries(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    work = [(str(tmp_path), "dev-builder", number) for number in range(20)]
    with context.Pool(processes=8) as pool:
        pool.starmap(_write_entry, work)

    entries = KnowledgeStore(tmp_path, "dev-builder").load_index()
    assert len(entries) == 20
    assert {entry["title"] for entry in entries} == {
        f"Concurrent {number}" for number in range(20)
    }


def test_different_crews_do_not_share_a_transaction_lock(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()

    with locks.crew_transaction(tmp_path, "alpha"):
        process = context.Process(
            target=_hold_crew_lock,
            args=(str(tmp_path), "beta", acquired, release),
        )
        process.start()
        assert acquired.wait(5), "beta must not wait for alpha's crew lock"
        release.set()

    process.join(10)
    assert process.exitcode == 0


def test_fingerprint_check_and_write_are_one_multiprocess_transaction(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    fingerprint = "a" * 64
    work = [(str(tmp_path), fingerprint) for _ in range(20)]
    with context.Pool(processes=8) as pool:
        written = pool.starmap(_write_fingerprint, work)

    assert written.count(True) == 1
    entries = KnowledgeStore(tmp_path, "dev-builder").load_index()
    assert len(entries) == 1
    assert f"harvest:fingerprint:{fingerprint}" in entries[0]["relatedEvents"]


def test_existing_harvest_helper_is_data_idempotent_across_processes(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    fingerprint = "b" * 64
    work = [(str(tmp_path), fingerprint) for _ in range(20)]
    with context.Pool(processes=8) as pool:
        pool.starmap(_write_fingerprint_through_harvest_helper, work)

    entries = KnowledgeStore(tmp_path, "legacy-harvest").load_index()
    assert len(entries) == 1
    assert f"harvest:fingerprint:{fingerprint}" in entries[0]["relatedEvents"]


def test_harvest_log_multiprocess_appends_preserve_all_runs(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    work = [(str(tmp_path), number) for number in range(20)]
    with context.Pool(processes=8) as pool:
        pool.starmap(_append_harvest_run, work)

    runs = read_runs(tmp_path, "dev-builder", limit=20)
    assert len(runs) == 20
    assert {run["runId"] for run in runs} == {
        f"parallel-{number:08d}" for number in range(20)
    }


def test_registry_and_crew_transaction_has_fixed_lock_order(
    tmp_path: Path, monkeypatch: Any
) -> None:
    observed: list[str] = []

    @contextmanager
    def fake_lock(path: Path) -> Iterator[None]:
        observed.append(path.name)
        yield

    monkeypatch.setattr(locks, "_exclusive_lock", fake_lock)
    with locks.registry_crew_transaction(tmp_path, "dev-builder"):
        pass

    assert observed == ["registry.lock", "crew-dev-builder.lock"]
