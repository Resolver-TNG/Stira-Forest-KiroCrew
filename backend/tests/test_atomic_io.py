from __future__ import annotations

from pathlib import Path

import pytest

from backend import core_init
from backend.atomic_io import REPLACE_RETRY_DELAYS, atomic_replace


def _sharing_error(winerror: int = 5) -> PermissionError:
    error = PermissionError("sharing violation")
    error.winerror = winerror
    return error


def test_atomic_replace_retries_then_succeeds():
    calls = []
    sleeps = []

    def replace(source, destination):
        calls.append((source, destination))
        if len(calls) < 3:
            raise _sharing_error(5)

    atomic_replace("source", "destination", replace=replace, sleep=sleeps.append, platform="nt")

    assert len(calls) == 3
    assert sleeps == [0.05, 0.1]


def test_atomic_replace_exhausts_bound_and_reraises_original():
    error = _sharing_error(32)
    calls = 0
    sleeps = []

    def replace(_source, _destination):
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(PermissionError) as raised:
        atomic_replace("source", "destination", replace=replace, sleep=sleeps.append, platform="nt")

    assert raised.value is error
    assert calls == len(REPLACE_RETRY_DELAYS) + 1
    assert sleeps == list(REPLACE_RETRY_DELAYS)
    assert sum(sleeps) == pytest.approx(2.55)


def test_atomic_replace_does_not_retry_nonsharing_or_nonwindows_errors():
    for error, platform in ((PermissionError("denied"), "nt"), (_sharing_error(5), "posix")):
        calls = 0

        def replace(_source, _destination):
            nonlocal calls
            calls += 1
            raise error

        with pytest.raises(PermissionError) as raised:
            atomic_replace("source", "destination", replace=replace, sleep=lambda _: None, platform=platform)
        assert raised.value is error
        assert calls == 1


def test_core_atomic_write_cleans_temporary_file_on_replace_failure(tmp_path: Path, monkeypatch):
    destination = tmp_path / "state.json"

    def fail(_source, _destination):
        raise PermissionError("persistent lock")

    monkeypatch.setattr(core_init, "atomic_replace", fail)

    with pytest.raises(PermissionError):
        core_init._atomic_write(destination, "{}\n")

    assert not destination.exists()
    assert list(tmp_path.glob(".state.json.*")) == []
