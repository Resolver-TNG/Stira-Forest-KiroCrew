"""Pure deterministic Stira engine.

This module performs no file, network, clock, random, environment, or LLM I/O.
Adapters own all side effects and must pass timestamps and identifiers explicitly.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
CORE_BREAKER_PARAMS = frozenset({"stability", "vigilance"})
DEFAULT_HYSTERESIS = 0.05
_MISSING = object()
_SUPPORTED_OPERATORS = frozenset(
    {"eq", "not_eq", "in", "not_in", "exists", "truthy", "falsy"}
)


class EngineValidationError(ValueError):
    """Raised when an engine input violates its deterministic contract."""


class SchemaVersionError(EngineValidationError):
    """Raised when a versioned input does not use the supported schema."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EngineValidationError(f"{name} must be a mapping")
    return value


def _require_version(document: Mapping[str, Any], name: str) -> None:
    actual = document.get("version")
    if actual != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Version mismatch: expected {SCHEMA_VERSION}, got {actual!r} ({name})"
        )


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EngineValidationError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise EngineValidationError(f"{name} must be a finite number")
    return result


def _unit_interval(value: Any, name: str) -> float:
    result = _number(value, name)
    if result < 0.0 or result > 1.0:
        raise EngineValidationError(f"{name} must be between 0.0 and 1.0")
    return result


def _round3(value: float) -> float:
    """Match Math.round for non-negative state values at 3 decimal places."""
    return math.floor(value * 1000.0 + 0.5) / 1000.0


def _copy_state(state: Mapping[str, Any]) -> dict[str, Any]:
    _require_version(state, "state")
    params = _require_mapping(state.get("params"), "state.params")
    copied = deepcopy(dict(state))
    copied["params"] = deepcopy(dict(params))
    return copied


def apply_event(
    state: Mapping[str, Any],
    event: Mapping[str, Any],
    matrix: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply ``weight * intensity`` deltas, clamped to [0, 1]."""
    result = _copy_state(state)
    _require_version(_require_mapping(matrix, "matrix"), "matrix")
    intensity = _unit_interval(event.get("intensity"), "event.intensity")
    category = event.get("category")
    if not isinstance(category, str) or not category:
        raise EngineValidationError("event.category must be a non-empty string")
    timestamp = event.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        raise EngineValidationError("event.timestamp must be a non-empty string")

    triggers = _require_mapping(matrix.get("triggers"), "matrix.triggers")
    weights = triggers.get(category)
    if weights is None:
        return result
    weights = _require_mapping(weights, f"matrix.triggers.{category}")

    for param, raw_weight in weights.items():
        if param not in result["params"]:
            continue
        current = _number(result["params"][param], f"state.params.{param}")
        weight = _number(raw_weight, f"matrix.triggers.{category}.{param}")
        changed = min(1.0, max(0.0, current + weight * intensity))
        result["params"][param] = _round3(changed)
    result["updated"] = timestamp
    return result


def decay(
    state: Mapping[str, Any],
    elapsed_ms: float,
    config: Mapping[str, Any],
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Apply linear hourly decay; adapters must supply ``updated_at``."""
    result = _copy_state(state)
    elapsed = _number(elapsed_ms, "elapsed_ms")
    if elapsed <= 0:
        return result
    if not isinstance(updated_at, str) or not updated_at:
        raise EngineValidationError(
            "updated_at is required when elapsed_ms > 0; engine cannot read the clock"
        )

    rules = config.get("rules")
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        raise EngineValidationError("config.rules must be a sequence")
    elapsed_hours = elapsed / 3_600_000.0
    for index, raw_rule in enumerate(rules):
        rule = _require_mapping(raw_rule, f"config.rules[{index}]")
        param = rule.get("param")
        if not isinstance(param, str):
            raise EngineValidationError(f"config.rules[{index}].param must be a string")
        if param not in result["params"]:
            continue
        current = _number(result["params"][param], f"state.params.{param}")
        rate = _number(rule.get("ratePerHour"), f"config.rules[{index}].ratePerHour")
        floor = _number(rule.get("floor"), f"config.rules[{index}].floor")
        result["params"][param] = _round3(
            max(floor, current - rate * elapsed_hours)
        )
    result["updated"] = updated_at
    return result


def _validate_breaker_rule(rule: Mapping[str, Any], index: int) -> None:
    param = rule.get("param")
    if param not in CORE_BREAKER_PARAMS:
        raise EngineValidationError(
            f"rules[{index}].param must be one of {sorted(CORE_BREAKER_PARAMS)}"
        )
    if rule.get("direction") not in {"below", "above"}:
        raise EngineValidationError(f"rules[{index}].direction is invalid")
    if rule.get("action") not in {"warn", "block", "notify"}:
        raise EngineValidationError(f"rules[{index}].action is invalid")
    _number(rule.get("threshold"), f"rules[{index}].threshold")


def check_breakers(
    state: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Evaluate fixed-core circuit breakers without executing actions."""
    checked_state = _copy_state(state)
    results: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        rule = _require_mapping(rule, f"rules[{index}]")
        _validate_breaker_rule(rule, index)
        param = rule["param"]
        current = _number(checked_state["params"].get(param, 1.0), f"state.params.{param}")
        threshold = _number(rule["threshold"], f"rules[{index}].threshold")
        fired = current < threshold if rule["direction"] == "below" else current > threshold
        results.append(
            {
                "ruleId": rule.get("id"),
                "fired": fired,
                "param": param,
                "currentValue": current,
                "threshold": threshold,
                "action": rule.get("action"),
                "message": rule.get("message"),
            }
        )
    return results


def get_fired_breakers(
    state: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    return [result for result in check_breakers(state, rules) if result["fired"]]


def resolve_breakers(
    state: Mapping[str, Any], rules: Sequence[Mapping[str, Any]]
) -> dict[str, list[str]]:
    """Resolve only auto-reset breakers after their hysteresis margin."""
    checked_state = _copy_state(state)
    active = checked_state.get("activeBreakers", [])
    if not isinstance(active, Sequence) or isinstance(active, (str, bytes)):
        raise EngineValidationError("state.activeBreakers must be a sequence")

    rule_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_rule in enumerate(rules):
        rule = _require_mapping(raw_rule, f"rules[{index}]")
        _validate_breaker_rule(rule, index)
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise EngineValidationError(f"rules[{index}].id must be a non-empty string")
        rule_by_id[rule_id] = rule

    resolved: list[str] = []
    still_active: list[str] = []
    for rule_id in active:
        if not isinstance(rule_id, str):
            raise EngineValidationError("state.activeBreakers entries must be strings")
        rule = rule_by_id.get(rule_id)
        if rule is None or rule.get("resetMode", "auto") == "manual":
            still_active.append(rule_id)
            continue
        hysteresis = _number(rule.get("hysteresis", DEFAULT_HYSTERESIS), "hysteresis")
        current = _number(
            checked_state["params"].get(rule["param"], 1.0),
            f"state.params.{rule['param']}",
        )
        threshold = _number(rule["threshold"], "threshold")
        recovered = (
            current >= threshold + hysteresis
            if rule["direction"] == "below"
            else current <= threshold - hysteresis
        )
        (resolved if recovered else still_active).append(rule_id)
    return {"resolved": resolved, "stillActive": still_active}


def detect_matrix_drift(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    threshold: float = 0.5,
    observed_at: str,
) -> dict[str, Any]:
    """Compatibility check for matrix drift; never modifies either matrix."""
    _require_version(_require_mapping(current, "current matrix"), "current matrix")
    _require_version(_require_mapping(baseline, "baseline matrix"), "baseline matrix")
    limit = _number(threshold, "threshold")
    if limit < 0:
        raise EngineValidationError("threshold must not be negative")
    if not isinstance(observed_at, str) or not observed_at:
        raise EngineValidationError("observed_at must be a non-empty string")

    current_triggers = _require_mapping(current.get("triggers"), "current.triggers")
    baseline_triggers = _require_mapping(baseline.get("triggers"), "baseline.triggers")
    details: list[dict[str, Any]] = []
    sign_flips: list[dict[str, str]] = []
    for trigger in sorted(baseline_triggers):
        base_weights = _require_mapping(
            baseline_triggers[trigger], f"baseline.triggers.{trigger}"
        )
        current_weights = current_triggers.get(trigger)
        if not isinstance(current_weights, Mapping):
            continue
        for param in sorted(base_weights):
            base_value = _number(base_weights[param], f"baseline.{trigger}.{param}")
            current_value = _number(current_weights.get(param, 0.0), f"current.{trigger}.{param}")
            delta = abs(current_value - base_value)
            if delta >= limit:
                details.append(
                    {
                        "trigger": trigger,
                        "param": param,
                        "baseline": base_value,
                        "current": current_value,
                        "delta": delta,
                    }
                )
            if base_value != 0 and current_value != 0 and (
                (base_value > 0) != (current_value > 0)
            ):
                sign_flips.append({"trigger": trigger, "param": param})
    return {
        "drifted": bool(details or sign_flips),
        "timestamp": observed_at,
        "details": details,
        "signFlips": sign_flips,
    }


def _lookup_field(context: Mapping[str, Any], dotted_field: str) -> Any:
    current: Any = context
    for part in dotted_field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _condition_matches(context: Mapping[str, Any], condition: Mapping[str, Any]) -> bool:
    field = condition.get("field")
    operator = condition.get("operator")
    if not isinstance(field, str) or not field:
        raise EngineValidationError("purpose drift condition.field must be a non-empty string")
    if operator not in _SUPPORTED_OPERATORS:
        raise EngineValidationError(f"unsupported purpose drift operator: {operator!r}")
    actual = _lookup_field(context, field)
    expected = condition.get("value")
    if operator == "exists":
        return (actual is not _MISSING) == bool(expected)
    if operator == "truthy":
        return actual is not _MISSING and bool(actual)
    if operator == "falsy":
        return actual is _MISSING or not bool(actual)
    if operator == "eq":
        return actual is not _MISSING and actual == expected
    if operator == "not_eq":
        return actual is _MISSING or actual != expected
    if not isinstance(expected, Sequence) or isinstance(expected, (str, bytes)):
        raise EngineValidationError(f"condition.value for {operator} must be a sequence")
    contained = actual is not _MISSING and actual in expected
    return contained if operator == "in" else not contained


def evaluate_purpose_drift(
    context: Mapping[str, Any],
    rule_set: Mapping[str, Any],
    *,
    evaluated_at: str,
) -> dict[str, Any]:
    """Evaluate per-crew declarative core-deviation rules.

    A rule matches when every ``all`` condition and at least one non-empty ``any``
    condition matches. Matching rules are violations; no expression evaluation or
    executable callbacks are accepted.
    """
    context = _require_mapping(context, "context")
    rule_set = _require_mapping(rule_set, "rule_set")
    _require_version(context, "purpose drift context")
    _require_version(rule_set, "purpose drift rule_set")
    if not isinstance(evaluated_at, str) or not evaluated_at:
        raise EngineValidationError("evaluated_at must be a non-empty string")
    rules = rule_set.get("rules")
    if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
        raise EngineValidationError("rule_set.rules must be a sequence")

    seen_ids: set[str] = set()
    evaluated_ids: list[str] = []
    violations: list[dict[str, Any]] = []
    for index, raw_rule in enumerate(rules):
        rule = _require_mapping(raw_rule, f"rule_set.rules[{index}]")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise EngineValidationError(f"rule_set.rules[{index}].id must be non-empty")
        if rule_id in seen_ids:
            raise EngineValidationError(f"duplicate purpose drift rule id: {rule_id}")
        seen_ids.add(rule_id)
        evaluated_ids.append(rule_id)
        all_conditions = rule.get("all", [])
        any_conditions = rule.get("any", [])
        for label, conditions in (("all", all_conditions), ("any", any_conditions)):
            if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes)):
                raise EngineValidationError(f"rule {rule_id}.{label} must be a sequence")
        all_match = all(
            _condition_matches(context, _require_mapping(item, f"rule {rule_id}.all"))
            for item in all_conditions
        )
        any_match = not any_conditions or any(
            _condition_matches(context, _require_mapping(item, f"rule {rule_id}.any"))
            for item in any_conditions
        )
        if all_match and any_match:
            violations.append(
                {
                    "ruleId": rule_id,
                    "severity": rule.get("severity", "block"),
                    "message": rule.get("message", "Purpose drift rule matched"),
                }
            )
    return {
        "version": SCHEMA_VERSION,
        "drifted": bool(violations),
        "evaluatedAt": evaluated_at,
        "evaluatedRuleIds": evaluated_ids,
        "violations": violations,
    }


def apply_mission_achievement(
    state: Mapping[str, Any],
    achievement: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Increase XP and growth after a mission achievement without touching baseline."""
    result = _copy_state(state)
    achievement = _require_mapping(achievement, "achievement")
    policy = _require_mapping(policy, "policy")
    _require_version(achievement, "achievement")
    _require_version(policy, "XP policy")
    achieved = achievement.get("achieved")
    if not isinstance(achieved, bool):
        raise EngineValidationError("achievement.achieved must be a boolean")
    if not achieved:
        return result
    timestamp = achievement.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        raise EngineValidationError("achievement.timestamp must be a non-empty string")
    impact = _unit_interval(achievement.get("impact", 1.0), "achievement.impact")
    base_xp = _number(policy.get("baseXp"), "policy.baseXp")
    growth_per_xp = _number(policy.get("growthPerXp"), "policy.growthPerXp")
    if base_xp < 0 or growth_per_xp < 0:
        raise EngineValidationError("XP policy values must not be negative")
    award = math.floor(base_xp * impact + 0.5)
    current_xp = _number(result.get("xp", 0), "state.xp")
    current_growth = _unit_interval(result["params"].get("growth", 0.0), "state.params.growth")
    result["xp"] = int(current_xp + award)
    result["params"]["growth"] = _round3(
        min(1.0, current_growth + award * growth_per_xp)
    )
    result["updated"] = timestamp
    return result


def build_means_drift_request(
    context: Mapping[str, Any],
    *,
    request_id: str,
    requested_at: str,
    growth: float,
    reflection_threshold: float = 0.7,
) -> dict[str, Any]:
    """Build a deterministic adapter request; this function performs no judgment."""
    context = _require_mapping(context, "context")
    _require_version(context, "means drift context")
    if not isinstance(request_id, str) or not request_id:
        raise EngineValidationError("request_id must be a non-empty string")
    if not isinstance(requested_at, str) or not requested_at:
        raise EngineValidationError("requested_at must be a non-empty string")
    growth_value = _unit_interval(growth, "growth")
    threshold = _unit_interval(reflection_threshold, "reflection_threshold")
    return {
        "version": SCHEMA_VERSION,
        "kind": "means-drift-reflection",
        "requestId": request_id,
        "requestedAt": requested_at,
        "requiresAdapterJudgment": True,
        "habituationReviewRequired": growth_value >= threshold,
        "growth": growth_value,
        "context": deepcopy(dict(context)),
    }


def compute_core_hash(core_content: str | bytes) -> str:
    """Return SHA-256 for exact IPD bytes; file reading belongs to adapters."""
    if isinstance(core_content, str):
        payload = core_content.encode("utf-8")
    elif isinstance(core_content, bytes):
        payload = core_content
    else:
        raise EngineValidationError("core_content must be str or bytes")
    return hashlib.sha256(payload).hexdigest()


def core_hash_matches(core_content: str | bytes, expected_hash: str) -> bool:
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise EngineValidationError("expected_hash must be a 64-character SHA-256 hex digest")
    try:
        bytes.fromhex(expected_hash)
    except ValueError as exc:
        raise EngineValidationError("expected_hash must be hexadecimal") from exc
    return compute_core_hash(core_content) == expected_hash.lower()
