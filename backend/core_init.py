"""Shared deterministic adapter for initializing a crew's IPD core.

Both the MCP tool and HTTP route call this module so core registration has one
side-effect path.  It performs local file/AppStorage I/O only and never invokes
an LLM (ADR-001).
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from .atomic_io import atomic_replace
from .engine import SCHEMA_VERSION, compute_core_hash, core_hash_matches
from .hooks import CREW_REGISTRY_KEY, ONBOARDING_KEY, ONBOARDING_QUESTION
from .locks import registry_crew_transaction

_CREW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# ── A-1 approved-core gate / pending proposal storage ──────────────────────
CORE_PROPOSAL_KEY_PREFIX = "core-proposal-"
# ── End A-1 approved-core gate / pending proposal storage ──────────────────

_DEFAULT_PARAMS = {
    "stability": 0.8,
    "vigilance": 0.3,
    "trust": 0.5,
    "curiosity": 0.5,
    "satisfaction": 0.5,
    "protectiveness": 0.5,
    "growth": 0.0,
}
_DEFAULT_TRIGGERS = {
    "achievement": {
        "stability": 0.08,
        "trust": 0.05,
        "satisfaction": 0.15,
        "growth": 0.05,
    },
    "challenge": {"stability": -0.08, "vigilance": 0.10, "curiosity": 0.05},
    "connection": {"trust": 0.10, "stability": 0.04, "satisfaction": 0.06},
    "threat": {
        "stability": -0.20,
        "vigilance": 0.25,
        "trust": -0.10,
        "protectiveness": 0.20,
    },
    "discovery": {"curiosity": 0.15, "satisfaction": 0.05, "growth": 0.03},
    "correction": {"stability": 0.03, "vigilance": 0.06, "trust": 0.03},
    "routine": {"curiosity": -0.02, "satisfaction": 0.01},
    "idle": {"curiosity": -0.03, "vigilance": -0.02},
}
_DEFAULT_CONFIG = {
    "version": SCHEMA_VERSION,
    "decay": {
        "rules": [
            {"param": "vigilance", "ratePerHour": 0.005, "floor": 0.2},
            {"param": "curiosity", "ratePerHour": 0.003, "floor": 0.3},
            {"param": "satisfaction", "ratePerHour": 0.002, "floor": 0.3},
        ]
    },
    "breakers": [
        {
            "id": "stability-warn",
            "param": "stability",
            "threshold": 0.5,
            "direction": "below",
            "action": "warn",
            "message": "Stability is below the warning threshold.",
            "resetMode": "auto",
        },
        {
            "id": "stability-block",
            "param": "stability",
            "threshold": 0.3,
            "direction": "below",
            "action": "block",
            "message": "Stability is critically low; stop self-modification.",
            "resetMode": "manual",
        },
        {
            "id": "vigilance-high",
            "param": "vigilance",
            "threshold": 0.9,
            "direction": "above",
            "action": "notify",
            "message": "Vigilance is above the notification threshold.",
            "resetMode": "auto",
        },
    ],
    "drift": {"threshold": 0.5},
    "xp": {"version": SCHEMA_VERSION, "baseXp": 10, "growthPerXp": 0.005},
}


class CoreRebaselineRequiredError(ValueError):
    """Raised when a candidate core lacks explicit human approval."""

    # ── A-1 structured rejection details ───────────────────────────────────
    def __init__(self, message: str, *, details: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.details = dict(details)
    # ── End A-1 structured rejection details ───────────────────────────────


def validate_crew_id(value: Any) -> str:
    """Return a normalized crew id or reject unsafe/path-like values."""
    if not isinstance(value, str) or not _CREW_ID_RE.fullmatch(value.strip()):
        raise ValueError(
            "crew_id is required and must match "
            "[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
        )
    return value.strip()


def _iso(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write(path: Path, content: str | bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content if isinstance(content, bytes) else content.encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary_path, mode)
        except OSError:
            pass
        atomic_replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _registry(storage: Any) -> dict[str, Any]:
    raw = storage.get(CREW_REGISTRY_KEY)
    if not isinstance(raw, Mapping) or raw.get("version") != SCHEMA_VERSION:
        return {"version": SCHEMA_VERSION, "crews": {}}
    crews = raw.get("crews")
    return {
        "version": SCHEMA_VERSION,
        "crews": dict(crews) if isinstance(crews, Mapping) else {},
    }


def _update_onboarding(storage: Any, registry: Mapping[str, Any]) -> None:
    crews = registry.get("crews", {})
    pending = sorted(
        crew_id
        for crew_id, record in crews.items()
        if isinstance(record, Mapping) and record.get("onboardingRequired") is True
    )
    storage.set(
        ONBOARDING_KEY,
        {
            "version": SCHEMA_VERSION,
            "required": bool(pending),
            "question": ONBOARDING_QUESTION,
            "crews": pending,
            "reason": None,
        },
    )


def _append_event(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _initialize_core_unlocked(
    crews_root: str | Path,
    storage: Any,
    crew_id: Any,
    content: Any,
    *,
    rebaseline: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Implement initialize_core while registry and crew locks are held."""
    normalized_id = validate_crew_id(crew_id)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("ipd_content is required and must not be empty")
    if not isinstance(rebaseline, bool):
        raise ValueError("rebaseline must be a boolean")

    crew_dir = Path(crews_root) / normalized_id
    crew_dir.mkdir(parents=True, exist_ok=True)
    ipd_path = crew_dir / "IPD.md"
    existing = ipd_path.read_bytes() if ipd_path.is_file() else None
    encoded = content.encode("utf-8")

    # ── A-1 approved-core gate / pending proposal ───────────────────────────
    candidate_hash = compute_core_hash(encoded)
    disk_hash_before = compute_core_hash(existing) if existing is not None else None
    registry = _registry(storage)
    previous = registry["crews"].get(normalized_id)
    record = dict(previous) if isinstance(previous, Mapping) else {}
    raw_approved_hash = record.get("coreHash")
    approved_hash = raw_approved_hash if isinstance(raw_approved_hash, str) else None
    candidate_matches_approved = (
        approved_hash is not None and core_hash_matches(encoded, approved_hash)
    )
    disk_matches_approved = (
        approved_hash is not None
        and existing is not None
        and core_hash_matches(existing, approved_hash)
    )
    approval_required = (
        (approved_hash is not None and not candidate_matches_approved)
        or (approved_hash is None and existing is not None)
    )
    disk_hash_mismatch_before = (
        approved_hash is not None and not disk_matches_approved
    )
    now_iso = _iso(now)

    if approval_required and not rebaseline:
        proposal_key = f"{CORE_PROPOSAL_KEY_PREFIX}{normalized_id}"
        proposed = {
            "version": SCHEMA_VERSION,
            "crewId": normalized_id,
            "baseHash": approved_hash,
            "candidateHash": candidate_hash,
            "proposedAt": now_iso,
        }
        stored_proposal = storage.get(proposal_key)
        proposal_stored = not isinstance(stored_proposal, Mapping)
        if proposal_stored:
            storage.set(proposal_key, proposed)
            stored_proposal = proposed

        record.update(
            {
                "name": normalized_id,
                "ipdPath": str(ipd_path),
                "coreHashCurrent": disk_hash_before,
                "coreHashHealthy": disk_matches_approved,
                "coreProposalPending": True,
                "onboardingRequired": bool(record.get("onboardingRequired"))
                or approved_hash is None,
            }
        )
        registry["crews"][normalized_id] = record
        storage.set(CREW_REGISTRY_KEY, registry)
        _update_onboarding(storage, registry)
        rejection = {
            "kind": "core-change-proposed",
            "timestamp": now_iso,
            "crewId": normalized_id,
            "approvedCoreHashBefore": approved_hash,
            "candidateCoreHash": candidate_hash,
            "diskCoreHashBefore": disk_hash_before,
            "diskHashMismatchBefore": disk_hash_mismatch_before,
            "proposalStored": proposal_stored,
        }
        _append_event(crew_dir / "events.jsonl", rejection)
        raise CoreRebaselineRequiredError(
            "candidate core does not match the approved coreHash; human-reviewed "
            "replacement requires rebaseline=true",
            details={
                **rejection,
                "pendingProposal": dict(stored_proposal),
            },
        )
    # ── End A-1 approved-core gate / pending proposal ───────────────────────

    _atomic_write(ipd_path, encoded)
    state_path = crew_dir / "state.json"
    matrix_path = crew_dir / "matrix.json"
    config_path = crew_dir / "config.json"
    if not state_path.exists():
        _write_json(
            state_path,
            {
                "version": SCHEMA_VERSION,
                "params": deepcopy(_DEFAULT_PARAMS),
                "xp": 0,
                "updated": now_iso,
                "activeBreakers": [],
            },
        )
    if not matrix_path.exists():
        _write_json(
            matrix_path,
            {
                "version": SCHEMA_VERSION,
                "triggers": deepcopy(_DEFAULT_TRIGGERS),
                "baseline": deepcopy(_DEFAULT_TRIGGERS),
            },
        )
    if not config_path.exists():
        _write_json(config_path, deepcopy(_DEFAULT_CONFIG))
    purpose_path = crew_dir / "purpose_rules.json"
    if not purpose_path.exists():
        _write_json(
            purpose_path,
            {"version": SCHEMA_VERSION, "crew": normalized_id, "rules": []},
        )

    # ── A-1 approved-core commit / audit result ─────────────────────────────
    core_hash = candidate_hash
    rebaselined = approval_required and rebaseline
    record.update(
        {
            "name": normalized_id,
            "ipdPath": str(ipd_path),
            "coreHash": core_hash,
            "coreHashCurrent": core_hash,
            "coreHashHealthy": True,
            "coreProposalPending": False,
            "onboardingRequired": False,
        }
    )
    registry["crews"][normalized_id] = record
    storage.set(CREW_REGISTRY_KEY, registry)
    proposal_key = f"{CORE_PROPOSAL_KEY_PREFIX}{normalized_id}"
    if isinstance(storage.get(proposal_key), Mapping):
        storage.set(proposal_key, None)
    _update_onboarding(storage, registry)
    audit = {
        "kind": "core-rebaselined" if rebaselined else "core-initialized",
        "timestamp": now_iso,
        "crewId": normalized_id,
        "coreHash": core_hash,
        "approvedCoreHashBefore": approved_hash,
        "candidateCoreHash": candidate_hash,
        "diskCoreHashBefore": disk_hash_before,
        "diskHashMismatchBefore": disk_hash_mismatch_before,
        "diskCoreUnapprovedBefore": approved_hash is None and existing is not None,
    }
    _append_event(crew_dir / "events.jsonl", audit)
    return {
        "ok": True,
        "crewId": normalized_id,
        "initialized": True,
        "created": existing is None,
        "rebaselined": rebaselined,
        "coreHash": core_hash,
        "ipdPath": str(ipd_path),
        "approvedCoreHashBefore": approved_hash,
        "diskCoreHashBefore": disk_hash_before,
        "diskHashMismatchBefore": disk_hash_mismatch_before,
        "diskCoreUnapprovedBefore": approved_hash is None and existing is not None,
    }
    # ── End A-1 approved-core commit / audit result ─────────────────────────


def initialize_core(
    crews_root: str | Path,
    storage: Any,
    crew_id: Any,
    content: Any,
    *,
    rebaseline: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Register one core atomically across shared registry and crew files."""

    normalized_id = validate_crew_id(crew_id)
    root = Path(crews_root).expanduser().resolve().parent
    with registry_crew_transaction(root, normalized_id):
        return _initialize_core_unlocked(
            crews_root,
            storage,
            normalized_id,
            content,
            rebaseline=rebaseline,
            now=now,
        )
