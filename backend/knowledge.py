"""Deterministic, per-crew knowledge storage for Stira.

The JSON index is the machine-readable source of truth. ``index.md`` is a
human/agent-readable projection and is regenerated after every mutation.
This module performs no LLM calls.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .atomic_io import atomic_replace
from .locks import crew_transaction
from .sanitize import clean_field, redact

SCHEMA_VERSION = 1
MAX_ENTRIES = 100
MAX_ARCHIVED = 200
CORE_THRESHOLD = 0.8
KNOWLEDGE_THRESHOLD = 0.6
ARCHIVE_THRESHOLD = 0.12
CONTENT_PREVIEW_CHARS = 1500
MILLISECONDS_PER_DAY = 86_400_000

LAYER_CORE = "core"
LAYER_KNOWLEDGE = "knowledge"
LAYER_LOG = "log"
_LAYER_ORDER = (LAYER_CORE, LAYER_KNOWLEDGE, LAYER_LOG)
_CREW_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
_HARVEST_FINGERPRINT_PREFIX = "harvest:fingerprint:"
_CATEGORY_WEIGHTS = {
    "achievement": 0.15,
    "discovery": 0.15,
    "threat": 0.20,
    "challenge": 0.10,
    "connection": 0.05,
    "correction": 0.05,
    "routine": -0.10,
    "idle": -0.20,
}


class KnowledgeStoreError(RuntimeError):
    """Base exception for knowledge-store failures."""


class SchemaVersionError(KnowledgeStoreError):
    """Raised when the on-disk index has an unsupported schema version."""


@dataclass(frozen=True)
class KnowledgePaths:
    """All paths for exactly one crew's isolated knowledge space."""

    crew_id: str
    root: Path
    crew_dir: Path
    store_dir: Path
    core_dir: Path
    knowledge_dir: Path
    logs_dir: Path
    index_json: Path
    index_md: Path

    @classmethod
    def for_crew(cls, root: str | os.PathLike[str], crew_id: str) -> "KnowledgePaths":
        if not _CREW_ID_RE.fullmatch(crew_id):
            raise ValueError("crew_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
        root_path = Path(root).expanduser().resolve()
        crew_dir = root_path / "crews" / crew_id
        store_dir = crew_dir / "knowledge"
        return cls(
            crew_id=crew_id,
            root=root_path,
            crew_dir=crew_dir,
            store_dir=store_dir,
            core_dir=store_dir / LAYER_CORE,
            knowledge_dir=store_dir / LAYER_KNOWLEDGE,
            logs_dir=store_dir / "logs",
            index_json=store_dir / "index.json",
            index_md=store_dir / "index.md",
        )


def _now_utc(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _now_utc(value).isoformat().replace("+00:00", "Z")


def _round3(value: float) -> float:
    return round(value + 1e-12, 3)


def _validate_score(score: float) -> float:
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be a number in [0, 1]")
    value = float(score)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("score must be a number in [0, 1]")
    return value


def route_score(score: float) -> str:
    """Return the Amygdala Pass destination for a score."""

    value = _validate_score(score)
    if value >= CORE_THRESHOLD:
        return LAYER_CORE
    if value >= KNOWLEDGE_THRESHOLD:
        return LAYER_KNOWLEDGE
    return LAYER_LOG


def suggest_score(
    category: str,
    intensity: float,
    *,
    breaker_fired: bool = False,
    stability_drop: float = 0.0,
    params: Mapping[str, Any] | None = None,
    operator_evidence_ratio: float = 0.0,
    evidence_strength: float = 0.0,
) -> dict[str, Any]:
    """Port ``stira_suggest_score`` and include its routing decision."""

    intensity_value = _validate_score(intensity)
    operator_ratio = _validate_score(operator_evidence_ratio)
    evidence_value = _validate_score(evidence_strength)
    if isinstance(stability_drop, bool) or not isinstance(stability_drop, (int, float)):
        raise ValueError("stability_drop must be a non-negative number")
    if not math.isfinite(float(stability_drop)) or stability_drop < 0:
        raise ValueError("stability_drop must be a non-negative number")
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("params must be an object")

    def param_value(name: str, default: float) -> float:
        value = default if params is None else params.get(name, default)
        try:
            return _validate_score(value)
        except ValueError as error:
            raise ValueError(f"params.{name} must be a number in [0, 1]") from error

    trust = param_value("trust", 0.5)
    vigilance = param_value("vigilance", 0.3)
    base = intensity_value * 0.5
    intensity_bonus = intensity_value * 0.15
    stability_drop_bonus = float(stability_drop) * 0.2
    breaker_bonus = 0.3 if breaker_fired else 0.0
    category_weight = _CATEGORY_WEIGHTS.get(category, 0.0)
    trust_bonus = (trust - 0.5) * operator_ratio * 0.2 if params is not None else 0.0
    vigilance_bonus_real = vigilance * evidence_value * 0.15 if params is not None else 0.0
    score = min(1.0, max(0.0, round(
        base
        + intensity_bonus
        + stability_drop_bonus
        + breaker_bonus
        + category_weight
        + trust_bonus
        + vigilance_bonus_real,
        2,
    )))
    return {
        "ok": True,
        "score": score,
        "route": route_score(score),
        "breakdown": {
            "base": round(base, 2),
            "intensityBonus": round(intensity_bonus, 2),
            "stabilityDropBonus": round(stability_drop_bonus, 2),
            "breakerBonus": breaker_bonus,
            "categoryWeight": category_weight,
            "trustBonus": round(trust_bonus, 3),
            "vigilanceBonusReal": round(vigilance_bonus_real, 3),
        },
    }


def _age_days(entry: Mapping[str, Any], now: datetime) -> float:
    return max(0.0, (_now_utc(now) - _parse_datetime(str(entry["createdAt"]))).total_seconds() / 86_400)


def score_projection(entry: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Return decay values plus explicit leaf-visualization fields for the UI."""

    current = _now_utc(now)
    age_days = _age_days(entry, current)
    ttl_days = entry.get("ttlDays")
    if ttl_days is not None:
        decay_factor = max(0.1, 1.0 - age_days / float(ttl_days))
    else:
        decay_factor = max(0.3, 1.0 - age_days / 365)
    effective_score = _round3(float(entry["score"]) * decay_factor)
    expired = ttl_days is not None and age_days > float(ttl_days)
    return {
        "effectiveScore": effective_score,
        "decayFactor": _round3(decay_factor),
        "expired": expired,
        "ui": {
            "leafColorIntensity": effective_score,
            "leafOpacity": _round3(max(0.15, effective_score)),
            "fadedFraction": _round3(1.0 - effective_score),
        },
    }


def calc_effective_score(entry: Mapping[str, Any], now: datetime | None = None) -> float:
    """Port the TypeScript effective-score calculation exactly."""

    return float(score_projection(entry, now)["effectiveScore"])


def calc_eviction_score(entry: Mapping[str, Any], now: datetime | None = None) -> float:
    """Port effective score plus recent-access bonus used for eviction."""

    current = _now_utc(now)
    days_since_access = max(
        0.0,
        (current - _parse_datetime(str(entry["lastAccessedAt"]))).total_seconds() / 86_400,
    )
    access_bonus = 0.2 if days_since_access < 7 else 0.1 if days_since_access < 30 else 0.0
    return calc_effective_score(entry, current) + access_bonus


def _slug(value: str, limit: int = 48) -> str:
    normalized = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    normalized = re.sub(r"[-\s]+", "-", normalized).strip("-_")
    return normalized[:limit] or "entry"


def _summary(content: str, supplied: str | None) -> str:
    if supplied and supplied.strip():
        text = supplied.strip()
    else:
        nonempty = [line.strip() for line in content.splitlines() if line.strip()]
        body_lines = [line for line in nonempty if not re.match(r"^#{1,6}\s+", line)]
        text = (body_lines or nonempty or [""])[0]
        text = re.sub(r"^#{1,6}\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= 160 else text[:157].rstrip() + "..."


def _lexical_features(value: str) -> set[str]:
    """Return dependency-free Unicode tokens plus per-token character bigrams."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    features = set(tokens)
    for token in tokens:
        if len(token) == 1:
            features.add(token)
        else:
            features.update(token[index:index + 2] for index in range(len(token) - 1))
    return features


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _validate_string_list(name: str, values: Sequence[str], maximum: int | None = None) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be an array of strings")
    cleaned = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be an array of strings")
        stripped = value.strip()
        if stripped and stripped not in cleaned:
            cleaned.append(stripped)
    if maximum is not None and len(cleaned) > maximum:
        raise ValueError(f"{name} must have at most {maximum} items")
    return cleaned


class KnowledgeStore:
    """File-backed knowledge store scoped to one crew."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        crew_id: str,
        *,
        max_entries: int = MAX_ENTRIES,
        max_archived: int = MAX_ARCHIVED,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be at least 1")
        if max_archived < 0:
            raise ValueError("max_archived must be non-negative")
        self.paths = KnowledgePaths.for_crew(root, crew_id)
        self.max_entries = max_entries
        self.max_archived = max_archived

    def load_index(self) -> list[dict[str, Any]]:
        if not self.paths.index_json.exists():
            return []
        try:
            payload = json.loads(self.paths.index_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise KnowledgeStoreError(f"cannot read knowledge index: {error}") from error
        if not isinstance(payload, dict) or payload.get("schemaVersion") != SCHEMA_VERSION:
            actual = payload.get("schemaVersion") if isinstance(payload, dict) else None
            raise SchemaVersionError(
                f"knowledge index schemaVersion {actual!r} is unsupported; expected {SCHEMA_VERSION}"
            )
        entries = payload.get("entries")
        if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
            raise KnowledgeStoreError("knowledge index entries must be an array of objects")
        return entries

    def _save_index(self, entries: Sequence[Mapping[str, Any]]) -> None:
        payload = {"schemaVersion": SCHEMA_VERSION, "entries": list(entries)}
        _atomic_write(
            self.paths.index_json,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
        _atomic_write(self.paths.index_md, self._render_markdown_index(entries))

    def _render_markdown_index(self, entries: Sequence[Mapping[str, Any]]) -> str:
        labels = {
            LAYER_CORE: "Core-near knowledge (score >= 0.8)",
            LAYER_KNOWLEDGE: "Reusable knowledge (0.6 <= score < 0.8)",
            LAYER_LOG: "Logs (score < 0.6)",
        }
        lines = [
            "# Stira Knowledge Index",
            "",
            f"> Crew: `{self.paths.crew_id}` | Generated from `index.json`; do not edit manually.",
            "",
        ]
        for layer in _LAYER_ORDER:
            lines.extend((f"## {labels[layer]}", ""))
            selected = sorted(
                (
                    entry
                    for entry in entries
                    if entry.get("layer") == layer and not entry.get("archived", False)
                ),
                key=lambda entry: (-float(entry["score"]), str(entry["title"]).lower()),
            )
            if not selected:
                lines.extend(("_No entries._", ""))
                continue
            for entry in selected:
                title = clean_field(entry["title"]).replace("]", "\\]")
                summary = clean_field(entry.get("summary", ""))
                tags = ", ".join(clean_field(tag) for tag in entry.get("tags", [])) or "none"
                relative_path = str(entry["relativePath"]).replace("\\", "/")
                lines.append(
                    f"- [{title}]({relative_path}) — {summary} "
                    f"`tags: {tags}` `score: {float(entry['score']):.3f}`"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def _entry_relative_path(
        self,
        layer: str,
        title: str,
        domain: str,
        entry_id: str,
        now: datetime,
    ) -> Path:
        filename = f"{now.date().isoformat()}_{_slug(title)}_{entry_id[-8:]}.md"
        if layer == LAYER_CORE:
            return Path(LAYER_CORE) / filename
        if layer == LAYER_KNOWLEDGE:
            return Path(LAYER_KNOWLEDGE) / _slug(domain, 32) / filename
        return Path("logs") / now.date().isoformat() / filename

    @staticmethod
    def _archive_faded_entries(
        entries: Sequence[dict[str, Any]], now: datetime
    ) -> list[str]:
        """Mark faded active entries, skipping malformed legacy entries."""

        archived: list[str] = []
        archived_at = _iso(now)
        for entry in entries:
            if entry.get("archived", False):
                continue
            try:
                faded = calc_effective_score(entry, now) < ARCHIVE_THRESHOLD
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not faded:
                continue
            entry["archived"] = True
            entry["archivedAt"] = archived_at
            entry["archivedReason"] = "faded"
            archived.append(str(entry.get("id", "")))
        return archived

    @staticmethod
    def _entry_counts(entries: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        active_count = sum(not entry.get("archived", False) for entry in entries)
        return {
            "activeCount": active_count,
            "archivedCount": len(entries) - active_count,
        }

    def counts(self) -> dict[str, int]:
        """Return active and archived index counts for dashboard observability."""

        return self._entry_counts(self.load_index())

    def _purge_archived_entries(
        self, entries: list[dict[str, Any]]
    ) -> tuple[list[str], list[Path]]:
        """Remove oldest archived rows above the hard cap, deterministically."""

        archived = sorted(
            (entry for entry in entries if entry.get("archived", False)),
            key=lambda entry: (
                str(entry.get("archivedAt") or entry.get("createdAt") or ""),
                str(entry.get("createdAt") or ""),
                str(entry.get("id") or ""),
            ),
        )
        overflow = max(0, len(archived) - self.max_archived)
        victims = archived[:overflow]
        for victim in victims:
            entries.remove(victim)
        purge_paths = []
        for victim in victims:
            relative_path = victim.get("relativePath")
            if isinstance(relative_path, str) and relative_path:
                purge_paths.append(self.paths.store_dir / relative_path)
        return (
            [str(victim.get("id", "")) for victim in victims],
            purge_paths,
        )

    @staticmethod
    def _unlink_fail_soft(paths: Sequence[Path]) -> None:
        for path in paths:
            try:
                path.unlink()
            except OSError:
                pass

    def archive_faded(self, *, now: datetime | None = None) -> list[str]:
        """Fail-soft faded-leaf transition under the crew transaction lock."""

        try:
            with crew_transaction(self.paths.root, self.paths.crew_id):
                entries = self.load_index()
                archived = self._archive_faded_entries(entries, _now_utc(now))
                purged, purged_paths = self._purge_archived_entries(entries)
                if archived or purged:
                    self._save_index(entries)
                    self._unlink_fail_soft(purged_paths)
                return archived
        except (KnowledgeStoreError, OSError, ValueError, TypeError, KeyError, OverflowError):
            return []

    def prune(self, leaf_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        """Archive one leaf by explicit human pruning, or return None if absent."""

        current = _now_utc(now)
        with crew_transaction(self.paths.root, self.paths.crew_id):
            entries = self.load_index()
            target = next((entry for entry in entries if entry.get("id") == leaf_id), None)
            if target is None:
                return None
            target["archived"] = True
            target["archivedAt"] = _iso(current)
            target["archivedReason"] = "pruned"
            self._save_index(entries)
            result = dict(target)
            # Purge is automatic and fail-soft; the explicit prune remains
            # fail-closed because its state was durably saved above.
            try:
                purged, purged_paths = self._purge_archived_entries(entries)
                if purged:
                    self._save_index(entries)
                    self._unlink_fail_soft(purged_paths)
            except (KnowledgeStoreError, OSError, ValueError, TypeError, KeyError):
                pass
            return result

    def unprune(self, leaf_id: str) -> dict[str, Any] | None:
        """Restore an explicitly pruned leaf; naturally faded leaves stay archived."""

        with crew_transaction(self.paths.root, self.paths.crew_id):
            entries = self.load_index()
            target = next((entry for entry in entries if entry.get("id") == leaf_id), None)
            if (
                target is None
                or not target.get("archived", False)
                or target.get("archivedReason") != "pruned"
            ):
                return None
            target["archived"] = False
            target["archivedAt"] = None
            target["archivedReason"] = None
            self._save_index(entries)
            return dict(target)

    def list_fallen(
        self, limit: int = 20, *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Return the newest archived leaves as bounded UI projections."""

        if limit < 0:
            raise ValueError("limit must be non-negative")
        current = _now_utc(now)
        entries = [
            entry for entry in self.load_index() if entry.get("archived", False)
        ]
        entries.sort(
            key=lambda entry: str(entry.get("archivedAt") or entry.get("createdAt") or ""),
            reverse=True,
        )
        fallen: list[dict[str, Any]] = []
        for entry in entries:
            try:
                fallen.append(self._summary_payload(entry, current))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if len(fallen) >= limit:
                break
        return fallen

    def write_entry(
        self,
        *,
        content: str,
        score: float,
        title: str,
        tags: Sequence[str],
        related_events: Sequence[str] = (),
        ttl_days: float | None = None,
        domain: str | None = None,
        summary: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Write one entry under the crew's cross-process transaction lock."""

        with crew_transaction(self.paths.root, self.paths.crew_id):
            return self._write_entry_unlocked(
                content=content,
                score=score,
                title=title,
                tags=tags,
                related_events=related_events,
                ttl_days=ttl_days,
                domain=domain,
                summary=summary,
                now=now,
            )

    def _write_entry_unlocked(
        self,
        *,
        content: str,
        score: float,
        title: str,
        tags: Sequence[str],
        related_events: Sequence[str] = (),
        ttl_days: float | None = None,
        domain: str | None = None,
        summary: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Implement write_entry while the caller holds the crew transaction."""

        if not isinstance(content, str) or not content.strip():
            raise ValueError("content is required and must not be empty")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title is required and must not be empty")
        # Untrusted text from past sessions: mask credentials, keep short fields on one line.
        content = redact(content)
        title = clean_field(title)
        if isinstance(summary, str):
            summary = clean_field(summary)
        if isinstance(domain, str):
            domain = clean_field(domain)
        if isinstance(tags, (list, tuple)):
            tags = [clean_field(t) if isinstance(t, str) else t for t in tags]
        if not title:
            raise ValueError("title is required and must not be empty")
        if len(title) > 100:
            raise ValueError("title must be at most 100 characters")
        score_value = _validate_score(score)
        clean_tags = _validate_string_list("tags", tags, 10)
        clean_events = _validate_string_list("related_events", related_events)
        if ttl_days is not None:
            if isinstance(ttl_days, bool) or not isinstance(ttl_days, (int, float)):
                raise ValueError("ttl_days must be a positive number")
            if not math.isfinite(float(ttl_days)) or ttl_days <= 0:
                raise ValueError("ttl_days must be a positive number")

        current = _now_utc(now)
        entries = self.load_index()
        faded_ids = self._archive_faded_entries(entries, current)
        purged, purged_paths = self._purge_archived_entries(entries)
        automatic_dirty = bool(faded_ids or purged)

        def commit_automatic_maintenance() -> None:
            if not automatic_dirty:
                return
            try:
                self._save_index(entries)
            except (KnowledgeStoreError, OSError):
                return
            self._unlink_fail_soft(purged_paths)

        fingerprint_markers = {
            event
            for event in clean_events
            if event.startswith(_HARVEST_FINGERPRINT_PREFIX)
            and _FINGERPRINT_RE.fullmatch(
                event.removeprefix(_HARVEST_FINGERPRINT_PREFIX)
            )
        }
        for existing in entries:
            if (
                existing.get("archived", False)
                and existing.get("archivedReason") == "faded"
            ):
                continue
            existing_events = existing.get("relatedEvents", ())
            if (
                fingerprint_markers
                and isinstance(existing_events, Sequence)
                and not isinstance(existing_events, (str, bytes))
                and fingerprint_markers.intersection(existing_events)
            ):
                commit_automatic_maintenance()
                relative = str(existing["relativePath"])
                counts = self._entry_counts(entries)
                duplicate_pruned = bool(
                    existing.get("archived", False)
                    and existing.get("archivedReason") == "pruned"
                )
                return {
                    "id": existing["id"],
                    "filepath": str(self.paths.store_dir / relative),
                    "relativePath": relative,
                    "layer": existing["layer"],
                    "domain": existing.get("domain", "general"),
                    "score": existing["score"],
                    **score_projection(existing, current),
                    "totalCount": counts["activeCount"],
                    **counts,
                    "evicted": [],
                    "archivedFaded": faded_ids,
                    "purged": purged,
                    "duplicateFingerprint": True,
                    "duplicate_pruned": duplicate_pruned,
                }

        layer = route_score(score_value)
        domain_value = (domain or (clean_tags[0] if clean_tags else "general")).strip() or "general"
        entry_id = f"kn-{current.strftime('%Y%m%d%H%M%S%f')}-{uuid.uuid4().hex[:8]}"
        relative_path = self._entry_relative_path(
            layer, title.strip(), domain_value, entry_id, current
        )
        absolute_path = self.paths.store_dir / relative_path
        iso_now = _iso(current)
        index_entry = {
            "id": entry_id,
            "title": title.strip(),
            "summary": clean_field(_summary(content, summary)),
            "score": score_value,
            "tags": clean_tags,
            "createdAt": iso_now,
            "lastAccessedAt": iso_now,
            "accessCount": 0,
            "relatedEvents": clean_events,
            "ttlDays": float(ttl_days) if ttl_days is not None else None,
            "layer": layer,
            "domain": domain_value,
            "relativePath": relative_path.as_posix(),
            "archived": False,
            "archivedAt": None,
            "archivedReason": None,
        }
        projection = score_projection(index_entry, current)
        if projection["effectiveScore"] < ARCHIVE_THRESHOLD:
            commit_automatic_maintenance()
            counts = self._entry_counts(entries)
            return {
                "id": entry_id,
                "filepath": None,
                "relativePath": None,
                "layer": layer,
                "domain": domain_value,
                "score": score_value,
                **projection,
                "totalCount": counts["activeCount"],
                **counts,
                "evicted": [],
                "archivedFaded": faded_ids,
                "purged": purged,
                "born_dead": True,
            }

        _atomic_write(absolute_path, content.rstrip() + "\n")
        entries.append(index_entry)

        evicted: list[str] = []
        evicted_paths: list[Path] = []
        active_entries = [entry for entry in entries if not entry.get("archived", False)]
        while len(active_entries) > self.max_entries:
            victim = min(active_entries, key=lambda item: calc_eviction_score(item, current))
            active_entries.remove(victim)
            entries.remove(victim)
            evicted.append(str(victim["id"]))
            evicted_paths.append(self.paths.store_dir / str(victim["relativePath"]))

        # Commit the new source of truth before deleting files it no longer references.
        # A crash during cleanup can only leave an UNINDEXED orphan, never a MISSING entry.
        self._save_index(entries)
        self._unlink_fail_soft(purged_paths)
        for evicted_path in evicted_paths:
            try:
                evicted_path.unlink()
            except FileNotFoundError:
                pass
        counts = self._entry_counts(entries)
        return {
            "id": entry_id,
            "filepath": str(absolute_path),
            "relativePath": relative_path.as_posix(),
            "layer": layer,
            "domain": domain_value,
            "score": score_value,
            "archived": False,
            "archivedAt": None,
            "archivedReason": None,
            **projection,
            "totalCount": counts["activeCount"],
            **counts,
            "evicted": evicted,
            "archivedFaded": faded_ids,
            "purged": purged,
            "born_dead": False,
        }

    def write_entry_if_new(
        self,
        *,
        fingerprint: str,
        related_events: Sequence[str] = (),
        **entry: Any,
    ) -> dict[str, Any]:
        """Atomically skip an entry whose harvest fingerprint already exists."""

        if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise ValueError("fingerprint must be a lowercase SHA-256 digest")
        marker = f"{_HARVEST_FINGERPRINT_PREFIX}{fingerprint}"
        events = _validate_string_list("related_events", related_events)
        if marker not in events:
            events.append(marker)

        with crew_transaction(self.paths.root, self.paths.crew_id):
            for existing in self.load_index():
                if (
                    existing.get("archived", False)
                    and existing.get("archivedReason") == "faded"
                ):
                    continue
                existing_events = existing.get("relatedEvents", ())
                if (
                    isinstance(existing_events, Sequence)
                    and not isinstance(existing_events, (str, bytes))
                    and marker in existing_events
                ):
                    duplicate_pruned = bool(
                        existing.get("archived", False)
                        and existing.get("archivedReason") == "pruned"
                    )
                    return {
                        "written": False,
                        "duplicate": dict(existing),
                        "fingerprint": fingerprint,
                        "duplicate_pruned": duplicate_pruned,
                    }
            result = self._write_entry_unlocked(related_events=events, **entry)
            return {
                "written": not result.get("born_dead", False),
                "result": result,
                "fingerprint": fingerprint,
                "born_dead": bool(result.get("born_dead", False)),
            }

    def get_top(self, n: int = 3, *, now: datetime | None = None) -> list[dict[str, Any]]:
        if n < 0:
            raise ValueError("n must be non-negative")
        current = _now_utc(now)
        summaries = [
            self._summary_payload(entry, current)
            for entry in self.load_index()
            if not entry.get("archived", False)
        ]
        summaries.sort(key=lambda item: item["effectiveScore"], reverse=True)
        return summaries[:n]

    def _content_preview(self, entry: Mapping[str, Any]) -> tuple[str, int]:
        """Read a bounded Markdown preview and full length within the crew store."""

        summary = entry.get("summary", "")
        fallback = summary if isinstance(summary, str) else ""
        relative_path = entry.get("relativePath")
        if not isinstance(relative_path, str) or not relative_path:
            return fallback, 0

        try:
            store_dir = self.paths.store_dir.resolve()
            content_path = (store_dir / relative_path).resolve()
            if (
                not content_path.is_relative_to(store_dir)
                or content_path.suffix.lower() != ".md"
            ):
                return fallback, 0
            with content_path.open("r", encoding="utf-8") as handle:
                content = handle.read()
            return content[:CONTENT_PREVIEW_CHARS], len(content)
        except (OSError, UnicodeError, ValueError):
            return fallback, 0

    def _summary_payload(self, entry: Mapping[str, Any], now: datetime) -> dict[str, Any]:
        projection = score_projection(entry, now)
        content, content_length = self._content_preview(entry)
        return {
            "id": entry["id"],
            "title": entry["title"],
            "summary": entry.get("summary", ""),
            "content": content,
            "contentLength": content_length,
            "score": entry["score"],
            "tags": list(entry.get("tags", [])),
            "createdAt": entry["createdAt"],
            "layer": entry["layer"],
            "domain": entry.get("domain", "general"),
            "relativePath": entry.get("relativePath"),
            "archived": bool(entry.get("archived", False)),
            "archivedAt": entry.get("archivedAt"),
            "archivedReason": entry.get("archivedReason"),
            "life": projection["effectiveScore"],
            **projection,
        }

    def list_index(
        self,
        layers: Iterable[str] | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Return a body-free table of contents ordered by effective score."""

        if not isinstance(include_archived, bool):
            raise ValueError("include_archived must be a boolean")
        requested_layers = set(layers) if layers is not None else None
        if requested_layers is not None and not requested_layers <= set(_LAYER_ORDER):
            raise ValueError("layers contains an unknown layer")

        current = _now_utc()
        rows: list[tuple[dict[str, Any], float]] = []
        for entry in self.load_index():
            if not include_archived and entry.get("archived", False):
                continue
            if requested_layers is not None and entry.get("layer") not in requested_layers:
                continue
            projection = score_projection(entry, current)
            effective = projection["effectiveScore"]
            rows.append(
                (
                    {
                        "id": entry["id"],
                        "title": entry["title"],
                        "tags": list(entry.get("tags", [])),
                        "domain": entry.get("domain", "general"),
                        "score": entry["score"],
                        "effectiveScore": effective,
                        "layer": entry["layer"],
                        "createdAt": entry["createdAt"],
                        "life": effective,
                    },
                    effective,
                )
            )
        rows.sort(key=lambda pair: pair[1], reverse=True)
        return {"entries": [row for row, _ in rows], "totalCount": len(rows)}

    def search_relevant(
        self,
        context: str,
        limit: int = 5,
        layers: Iterable[str] | None = None,
        track_access: bool = False,
    ) -> dict[str, Any]:
        """Rank active knowledge by weighted lexical overlap and freshness."""

        if not isinstance(context, str):
            raise ValueError("context must be a string")
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if not isinstance(track_access, bool):
            raise ValueError("track_access must be a boolean")
        requested_layers = set(layers) if layers is not None else None
        if requested_layers is not None and not requested_layers <= set(_LAYER_ORDER):
            raise ValueError("layers contains an unknown layer")

        query_features = _lexical_features(context)
        if not query_features or limit == 0:
            return {"entries": [], "totalCount": 0}

        field_weights = {
            "title": 4.0,
            "tags": 3.0,
            "domain": 3.0,
            "summary": 2.0,
            "content": 1.0,
        }
        current = _now_utc()
        index = self.load_index()
        ranked: list[tuple[dict[str, Any], float, list[str], list[str]]] = []
        for entry in index:
            if entry.get("archived", False):
                continue
            if requested_layers is not None and entry.get("layer") not in requested_layers:
                continue
            content, _ = self._content_preview(entry)
            fields = {
                "title": str(entry.get("title", "")),
                "tags": " ".join(map(str, entry.get("tags", []))),
                "domain": str(entry.get("domain", "general")),
                "summary": str(entry.get("summary", "")),
                "content": content,
            }
            weighted_overlap = 0.0
            matched_fields: list[str] = []
            matched_terms: set[str] = set()
            for field, weight in field_weights.items():
                field_features = _lexical_features(fields[field])
                common = query_features.intersection(field_features)
                if not common:
                    continue
                matched_fields.append(field)
                matched_terms.update(common)
                denominator = min(len(query_features), len(field_features))
                weighted_overlap += weight * (len(common) / denominator)
            if not matched_fields:
                continue
            lexical_relevance = min(1.0, weighted_overlap / sum(field_weights.values()))
            effective_score = float(score_projection(entry, current)["effectiveScore"])
            relevance = _round3(0.75 * lexical_relevance + 0.25 * effective_score)
            ranked.append((entry, relevance, matched_fields, sorted(matched_terms)))

        ranked.sort(
            key=lambda item: (
                item[1],
                calc_effective_score(item[0], current),
                str(item[0].get("createdAt", "")),
            ),
            reverse=True,
        )
        selected = ranked[:limit]
        result_entries = [
            {
                **self._summary_payload(entry, current),
                "relevanceScore": relevance,
                "matchedFields": matched_fields,
                "matchedTerms": matched_terms,
            }
            for entry, relevance, matched_fields, matched_terms in selected
        ]

        if track_access and selected:
            accessed_ids = {str(entry["id"]) for entry, *_ in selected}
            now_iso = _iso(current)
            with crew_transaction(self.paths.root, self.paths.crew_id):
                latest = self.load_index()
                for entry in latest:
                    if str(entry["id"]) in accessed_ids:
                        entry["lastAccessedAt"] = now_iso
                        entry["accessCount"] = int(entry.get("accessCount", 0)) + 1
                self._save_index(latest)

        return {"entries": result_entries, "totalCount": len(ranked)}

    def query(
        self,
        *,
        query: str | None = None,
        tags: Sequence[str] | None = None,
        min_score: float | None = None,
        limit: int = 5,
        sort: str = "score_desc",
        layers: Iterable[str] | None = None,
        now: datetime | None = None,
        track_access: bool = False,
    ) -> dict[str, Any]:
        """Query knowledge; access tracking is an explicit write transaction."""

        if not isinstance(track_access, bool):
            raise ValueError("track_access must be a boolean")
        kwargs = {
            "query": query,
            "tags": tags,
            "min_score": min_score,
            "limit": limit,
            "sort": sort,
            "layers": layers,
            "now": now,
            "track_access": track_access,
        }
        if track_access:
            with crew_transaction(self.paths.root, self.paths.crew_id):
                return self._query_unlocked(**kwargs)
        return self._query_unlocked(**kwargs)

    def _query_unlocked(
        self,
        *,
        query: str | None,
        tags: Sequence[str] | None,
        min_score: float | None,
        limit: int,
        sort: str,
        layers: Iterable[str] | None,
        now: datetime | None,
        track_access: bool,
    ) -> dict[str, Any]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if min_score is not None:
            _validate_score(min_score)
        requested_tags = {tag.lower() for tag in (tags or [])}
        requested_layers = set(layers) if layers is not None else None
        if requested_layers is not None and not requested_layers <= set(_LAYER_ORDER):
            raise ValueError("layers contains an unknown layer")

        current = _now_utc(now)
        index = self.load_index()
        filtered: list[tuple[dict[str, Any], dict[str, Any]]] = []
        needle = query.lower() if query else None
        for entry in index:
            if entry.get("archived", False):
                continue
            projected = score_projection(entry, current)
            searchable = " ".join(
                (str(entry["title"]), str(entry.get("summary", "")), *map(str, entry.get("tags", [])))
            ).lower()
            if needle and needle not in searchable:
                continue
            entry_tags = {str(tag).lower() for tag in entry.get("tags", [])}
            if requested_tags and not requested_tags.intersection(entry_tags):
                continue
            if min_score is not None and projected["effectiveScore"] < min_score:
                continue
            if requested_layers is not None and entry.get("layer") not in requested_layers:
                continue
            filtered.append((entry, projected))

        if sort == "score_desc":
            filtered.sort(key=lambda pair: pair[1]["effectiveScore"], reverse=True)
        elif sort == "date_desc":
            filtered.sort(key=lambda pair: _parse_datetime(str(pair[0]["createdAt"])), reverse=True)
        elif sort == "date_asc":
            filtered.sort(key=lambda pair: _parse_datetime(str(pair[0]["createdAt"])))
        else:
            raise ValueError("sort must be score_desc, date_desc, or date_asc")

        selected = filtered[:limit]
        accessed_ids = {str(entry["id"]) for entry, _ in selected}
        result_entries = []
        for entry, projected in selected:
            result_entries.append({
                **self._summary_payload(entry, current),
                **projected,
                "lastAccessedAt": entry["lastAccessedAt"],
                "accessCount": entry["accessCount"],
                "relatedEvents": list(entry.get("relatedEvents", [])),
            })

        if track_access and accessed_ids:
            now_iso = _iso(current)
            for entry in index:
                if str(entry["id"]) in accessed_ids:
                    entry["lastAccessedAt"] = now_iso
                    entry["accessCount"] = int(entry.get("accessCount", 0)) + 1
            self._save_index(index)

        return {"entries": result_entries, "totalCount": len(filtered)}

    def crawl_consistency(self) -> dict[str, Any]:
        """Detect MISSING index targets and UNINDEXED markdown files."""

        entries = self.load_index()
        indexed = {str(entry["relativePath"]).replace("\\", "/"): entry for entry in entries}
        actual: set[str] = set()
        for base in (self.paths.core_dir, self.paths.knowledge_dir, self.paths.logs_dir):
            if base.exists():
                actual.update(
                    path.relative_to(self.paths.store_dir).as_posix()
                    for path in base.rglob("*.md")
                    if path.is_file()
                )
        missing = [
            {"id": entry.get("id"), "relativePath": relative_path}
            for relative_path, entry in sorted(indexed.items())
            if relative_path not in actual
        ]
        unindexed = sorted(actual - set(indexed))
        return {
            "ok": not missing and not unindexed,
            "crewId": self.paths.crew_id,
            "missing": missing,
            "unindexed": unindexed,
            "indexedCount": len(indexed),
            "fileCount": len(actual),
        }


# Function-level API for MCP adapters and cron scripts.
def write_knowledge_entry(root: str | os.PathLike[str], crew_id: str, **entry: Any) -> dict[str, Any]:
    return KnowledgeStore(root, crew_id).write_entry(**entry)


def query_knowledge(root: str | os.PathLike[str], crew_id: str, **query: Any) -> dict[str, Any]:
    return KnowledgeStore(root, crew_id).query(**query)


def get_top_knowledge(
    root: str | os.PathLike[str], crew_id: str, n: int = 3, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    return KnowledgeStore(root, crew_id).get_top(n, now=now)


def crawl_consistency(root: str | os.PathLike[str], crew_id: str) -> dict[str, Any]:
    return KnowledgeStore(root, crew_id).crawl_consistency()


# Explicit migration aliases for existing TypeScript naming in the MCP adapter.
calcEffectiveScore = calc_effective_score
calcEvictionScore = calc_eviction_score
writeKnowledgeEntry = write_knowledge_entry
queryKnowledge = query_knowledge
getTopKnowledge = get_top_knowledge
