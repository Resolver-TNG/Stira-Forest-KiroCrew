"""Fail-soft protection for Stira's persistent crew and registry data.

``data/crews`` and ``data/kv`` are snapshotted together. Runtime logs are
deliberately excluded so an open ``backend.log`` handle cannot prevent a backup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

DATA_SCHEMA_VERSION = 1
SCHEMA_VERSION_FILE = ".schema_version"
BACKUPS_DIR = ".backups"
DEFAULT_BACKUP_LIMIT = 5

_CREW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SNAPSHOT_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z(?:-\d{3})?$")


@dataclass(frozen=True)
class IntegrityReport:
    crews_empty: bool
    missing_state_crews: tuple[str, ...]
    missing_core_hash_crews: tuple[str, ...]
    invalid_registry_entries: tuple[str, ...]

    @property
    def issues(self) -> tuple[str, ...]:
        return tuple(
            [
                f"invalid crew registry entry: {entry}"
                for entry in self.invalid_registry_entries
            ]
            + [
                f"crew {crew_id} is registered but crews/{crew_id}/state.json is missing"
                for crew_id in self.missing_state_crews
            ]
            + [
                f"crew {crew_id} has IPD.md but no approved coreHash in the registry"
                for crew_id in self.missing_core_hash_crews
            ]
        )


@dataclass(frozen=True)
class RecoveryResult:
    snapshot: Path
    restored_crews: tuple[str, ...]


def ensure_schema_version(data_dir: str | Path) -> int:
    """Create/read the data schema marker and expose the migration seam."""
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / SCHEMA_VERSION_FILE
    if not marker.exists():
        _atomic_write_text(marker, f"{DATA_SCHEMA_VERSION}\n")
        return DATA_SCHEMA_VERSION

    raw = marker.read_text(encoding="utf-8").strip()
    try:
        version = int(raw)
    except ValueError as exc:
        raise ValueError(f"invalid Stira data schema version: {raw!r}") from exc
    if version == DATA_SCHEMA_VERSION:
        return version
    _migrate_schema(root, version, DATA_SCHEMA_VERSION)
    return DATA_SCHEMA_VERSION


def inspect_integrity(
    data_dir: str | Path,
    registry_crews: Mapping[str, Any],
) -> IntegrityReport:
    """Compare registry approvals and state files with per-crew data on disk."""
    crews_root = Path(data_dir) / "crews"
    crew_directories = (
        [child for child in crews_root.iterdir() if child.is_dir()]
        if crews_root.is_dir()
        else []
    )
    invalid: list[str] = []
    missing_state: list[str] = []
    valid_registry_ids: set[str] = set()
    for raw_crew_id in registry_crews:
        crew_id = _valid_crew_id(raw_crew_id)
        if crew_id is None:
            invalid.append(repr(raw_crew_id))
            continue
        valid_registry_ids.add(crew_id)
        if not (crews_root / crew_id / "state.json").is_file():
            missing_state.append(crew_id)

    missing_core_hash: list[str] = []
    for crew_dir in crew_directories:
        crew_id = _valid_crew_id(crew_dir.name)
        if crew_id is None or not (crew_dir / "IPD.md").is_file():
            continue
        record = registry_crews.get(crew_id) if crew_id in valid_registry_ids else None
        core_hash = record.get("coreHash") if isinstance(record, Mapping) else None
        if not isinstance(core_hash, str) or not core_hash.strip():
            missing_core_hash.append(crew_id)

    return IntegrityReport(
        crews_empty=not crew_directories,
        missing_state_crews=tuple(sorted(missing_state)),
        missing_core_hash_crews=tuple(sorted(missing_core_hash)),
        invalid_registry_entries=tuple(sorted(invalid)),
    )


def create_backup(
    data_dir: str | Path,
    *,
    limit: int = DEFAULT_BACKUP_LIMIT,
    now: datetime | None = None,
) -> Path:
    """Atomically snapshot ``crews/`` and ``kv/`` with bounded retention."""
    if limit < 1:
        raise ValueError("backup limit must be at least 1")
    root = Path(data_dir)
    crews_root = root / "crews"
    crews_root.mkdir(parents=True, exist_ok=True)
    kv_root = root / "kv"
    kv_root.mkdir(parents=True, exist_ok=True)
    backups_root = root / BACKUPS_DIR
    backups_root.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base_name = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    destination = _unused_snapshot_path(backups_root, base_name)
    temporary = backups_root / f".incomplete-{uuid4().hex}"
    try:
        temporary.mkdir()
        shutil.copytree(crews_root, temporary / "crews", symlinks=True)
        # A-3: AppStorage approvals and onboarding state are part of the same
        # generation as crew files. Logs remain intentionally excluded.
        shutil.copytree(kv_root, temporary / "kv", symlinks=True)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    snapshots = _backup_snapshots(root)
    for stale in snapshots[: max(0, len(snapshots) - limit)]:
        shutil.rmtree(stale)
    return destination


def restore_latest_backup(
    data_dir: str | Path,
    *,
    required_crews: tuple[str, ...] = (),
) -> RecoveryResult | None:
    """Restore missing crew and kv files from the newest usable backup.

    Existing live files are never overwritten. If one or more crews are
    required, the selected snapshot must contain ``state.json`` for all of them.
    Legacy snapshots without ``kv/`` remain usable for crew-only recovery.
    """
    root = Path(data_dir)
    required = tuple(sorted({_require_crew_id(value) for value in required_crews}))
    snapshot = _latest_usable_snapshot(root, required)
    if snapshot is None:
        return None

    source_root = snapshot / "crews"
    if required:
        crew_ids = required
    else:
        crew_ids = tuple(
            sorted(
                child.name
                for child in source_root.iterdir()
                if child.is_dir() and _valid_crew_id(child.name) is not None
            )
        )

    live_root = root / "crews"
    live_root.mkdir(parents=True, exist_ok=True)
    restored: list[str] = []
    for crew_id in crew_ids:
        source = source_root / crew_id
        target = live_root / crew_id
        _copy_missing_tree(source, target)
        if (target / "state.json").is_file():
            restored.append(crew_id)

    # A-3: Restore only missing AppStorage files. Never overwrite a newer live
    # registry/onboarding value, and tolerate pre-A-3 snapshots without kv/.
    backup_kv_root = snapshot / "kv"
    if backup_kv_root.is_dir():
        _copy_missing_tree(backup_kv_root, root / "kv")

    return RecoveryResult(snapshot=snapshot, restored_crews=tuple(restored))


def initialize_schema_fail_soft(
    data_dir: str | Path,
    logger: Any,
    mark_degraded: Callable[[str], None],
) -> int | None:
    try:
        return ensure_schema_version(data_dir)
    except Exception as exc:
        issue = f"Stira could not initialize data schema version: {exc}"
        logger.warning(issue)
        _mark_degraded_fail_soft(mark_degraded, logger, issue)
        return None


def recover_if_needed_fail_soft(
    data_dir: str | Path,
    registry_crews: Mapping[str, Any],
    logger: Any,
    mark_degraded: Callable[[str], None],
) -> RecoveryResult | None:
    """Detect data loss and restore if possible without blocking startup."""
    try:
        root = Path(data_dir)
        integrity = inspect_integrity(root, registry_crews)
        required = integrity.missing_state_crews
        latest = _latest_usable_snapshot(root, required)
        should_restore_empty = integrity.crews_empty and latest is not None

        # A-3: Missing registry/onboarding files are independently recoverable,
        # including when crews/ itself is still healthy. Compare only files
        # present in the chosen backup; live files remain authoritative.
        should_restore_kv = False
        if latest is not None and (latest / "kv").is_dir():
            live_kv_root = root / "kv"
            should_restore_kv = any(
                item.is_file()
                and not (live_kv_root / item.relative_to(latest / "kv")).exists()
                for item in (latest / "kv").rglob("*")
                if not item.is_symlink()
            )

        if not required and not should_restore_empty and not should_restore_kv:
            return None
        recovery = restore_latest_backup(root, required_crews=required)
        if recovery is None:
            logger.warning(
                "Stira detected missing persistent data but found no usable backup: %s",
                ", ".join(required) if required else "crews or kv data is missing",
            )
            return None
        restored_targets = list(recovery.restored_crews)
        if should_restore_kv:
            restored_targets.append("kv files")
        issue = (
            "Stira automatically restored persistent data from backup "
            f"{recovery.snapshot.name}: {', '.join(restored_targets)}"
        )
        logger.warning(issue)
        _mark_degraded_fail_soft(mark_degraded, logger, issue)
        return recovery
    except Exception as exc:
        logger.warning("Stira data recovery failed; startup will continue: %s", exc)
        return None


def validate_integrity_fail_soft(
    data_dir: str | Path,
    registry_crews: Mapping[str, Any],
    logger: Any,
    mark_degraded: Callable[[str], None],
) -> IntegrityReport | None:
    try:
        report = inspect_integrity(data_dir, registry_crews)
        issues = list(report.issues)
        if report.crews_empty and _latest_usable_snapshot(Path(data_dir), ()) is not None:
            issues.append("crews directory is empty despite a restorable backup")
        for detail in issues:
            issue = f"Stira data integrity check failed: {detail}"
            logger.warning(issue)
            _mark_degraded_fail_soft(mark_degraded, logger, issue)
        return report
    except Exception as exc:
        logger.warning("Stira data integrity check failed softly: %s", exc)
        return None


def backup_crews_fail_soft(
    data_dir: str | Path,
    logger: Any,
    *,
    limit: int = DEFAULT_BACKUP_LIMIT,
) -> Path | None:
    try:
        return create_backup(data_dir, limit=limit)
    except Exception as exc:
        logger.warning("Stira crew backup failed; startup will continue: %s", exc)
        return None


def _latest_usable_snapshot(root: Path, required_crews: tuple[str, ...]) -> Path | None:
    for snapshot in reversed(_backup_snapshots(root)):
        crews_root = snapshot / "crews"
        if not crews_root.is_dir():
            continue
        if required_crews:
            if all((crews_root / crew_id / "state.json").is_file() for crew_id in required_crews):
                return snapshot
        elif any(
            child.is_dir() and (child / "state.json").is_file()
            for child in crews_root.iterdir()
        ):
            return snapshot
    return None


def _backup_snapshots(root: Path) -> list[Path]:
    backups_root = root / BACKUPS_DIR
    if not backups_root.is_dir():
        return []
    return sorted(
        child
        for child in backups_root.iterdir()
        if child.is_dir() and _SNAPSHOT_RE.fullmatch(child.name)
    )


def _copy_missing_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"backup crew directory is missing: {source.name}")
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.rglob("*"), key=lambda path: path.relative_to(source).parts):
        if item.is_symlink():
            continue
        destination = target / item.relative_to(source)
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif item.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def _unused_snapshot_path(backups_root: Path, base_name: str) -> Path:
    candidate = backups_root / base_name
    if not candidate.exists():
        return candidate
    for suffix in range(1, 1000):
        candidate = backups_root / f"{base_name}-{suffix:03d}"
        if not candidate.exists():
            return candidate
    raise FileExistsError("could not allocate a unique Stira backup generation")


def _valid_crew_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    crew_id = value.strip()
    if crew_id in {"", ".", ".."} or not _CREW_ID_RE.fullmatch(crew_id):
        return None
    return crew_id


def _require_crew_id(value: Any) -> str:
    crew_id = _valid_crew_id(value)
    if crew_id is None:
        raise ValueError(f"invalid crew id: {value!r}")
    return crew_id


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _migrate_schema(root: Path, from_version: int, to_version: int) -> None:
    """Future migrations are dispatched here before updating the marker."""
    raise ValueError(
        f"unsupported Stira data schema version {from_version}; expected {to_version}"
    )


def _mark_degraded_fail_soft(
    mark_degraded: Callable[[str], None], logger: Any, issue: str
) -> None:
    try:
        mark_degraded(issue)
    except Exception as exc:
        logger.warning("Stira could not update health state: %s", exc)
