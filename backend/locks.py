"""Cross-process transaction locks for Stira's file-backed persistence.

Lock files live outside ``crews/`` so data backups never copy lock state.  Any
operation that needs both shared registry state and one crew always acquires
``registry.lock`` before ``crew-<id>.lock``; callers must not reverse that order.
"""

from __future__ import annotations

from contextlib import contextmanager
import asyncio
import os
from pathlib import Path
import re
import time
from typing import Iterator

try:
    from kiro_crew.platform_compat import file_lock as _kiro_file_lock
except ImportError:  # Standalone tests do not install the KiroCrew host package.
    _kiro_file_lock = None

_CREW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FALLBACK_LOCK_TIMEOUT_SECONDS = 10.0
_FALLBACK_LOCK_POLL_SECONDS = 0.05


@contextmanager
def _fallback_file_lock(fd: int) -> Iterator[None]:
    """Match KiroCrew's fail-closed msvcrt/fcntl lock when it is unavailable."""

    if os.name != "nt":
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        return

    import msvcrt

    def try_once() -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    try:
        asyncio.get_running_loop()
        on_event_loop = True
    except RuntimeError:
        on_event_loop = False

    acquired = try_once()
    if not acquired and not on_event_loop:
        deadline = time.monotonic() + _FALLBACK_LOCK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(_FALLBACK_LOCK_POLL_SECONDS)
            if try_once():
                acquired = True
                break
    if not acquired:
        raise OSError(
            "could not acquire exclusive file lock; refusing to proceed unserialized"
        )
    try:
        yield
    finally:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def _validated_crew_id(crew_id: str) -> str:
    if not isinstance(crew_id, str) or not _CREW_ID_RE.fullmatch(crew_id):
        raise ValueError("crew_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    return crew_id


def _lock_path(root: str | os.PathLike[str], name: str) -> Path:
    lock_dir = Path(root).expanduser().resolve() / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / name


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """Acquire one dedicated lock file, failing closed through KiroCrew core."""

    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if _kiro_file_lock is not None:
            with _kiro_file_lock(
                handle.fileno(), exclusive=True, required=True
            ):
                yield
        else:
            with _fallback_file_lock(handle.fileno()):
                yield


@contextmanager
def registry_transaction(root: str | os.PathLike[str]) -> Iterator[None]:
    """Serialize one shared registry read-modify-write transaction."""

    with _exclusive_lock(_lock_path(root, "registry.lock")):
        yield


@contextmanager
def crew_transaction(
    root: str | os.PathLike[str], crew_id: str
) -> Iterator[None]:
    """Serialize one crew's state, event, knowledge, or harvest-log mutation."""

    normalized = _validated_crew_id(crew_id)
    with _exclusive_lock(_lock_path(root, f"crew-{normalized}.lock")):
        yield


@contextmanager
def registry_crew_transaction(
    root: str | os.PathLike[str], crew_id: str
) -> Iterator[None]:
    """Acquire registry then crew lock in the only permitted combined order."""

    normalized = _validated_crew_id(crew_id)
    with _exclusive_lock(_lock_path(root, "registry.lock")):
        with _exclusive_lock(_lock_path(root, f"crew-{normalized}.lock")):
            yield
