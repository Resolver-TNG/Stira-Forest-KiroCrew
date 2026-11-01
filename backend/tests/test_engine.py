from copy import deepcopy
import hashlib

import pytest

from backend.engine import (
    EngineValidationError,
    SchemaVersionError,
    apply_event,
    apply_mission_achievement,
    build_means_drift_request,
    check_breakers,
    compute_core_hash,
    core_hash_matches,
    decay,
    detect_matrix_drift,
    evaluate_purpose_drift,
    get_fired_breakers,
    resolve_breakers,
)


STATE = {
    "version": 1,
    "params": {
        "stability": 0.8,
        "vigilance": 0.3,
        "curiosity": 0.5,
        "satisfaction": 0.6,
        "growth": 0.2,
    },
    "xp": 10,
    "updated": "2026-01-01T00:00:00Z",
}

MATRIX = {
    "version": 1,
    "triggers": {
        "joy": {
            "curiosity": 0.7,
            "vigilance": -0.2,
            "satisfaction": 0.9,
            "stability": 0.4,
        },
        "threat": {
            "vigilance": 0.9,
            "stability": -0.4,
            "curiosity": 0.2,
            "satisfaction": -0.3,
        },
    },
    "baseline": {
        "joy": {
            "curiosity": 0.7,
            "vigilance": -0.2,
            "satisfaction": 0.9,
            "stability": 0.4,
        },
        "threat": {
            "vigilance": 0.9,
            "stability": -0.4,
            "curiosity": 0.2,
            "satisfaction": -0.3,
        },
    },
}

BREAKERS = [
    {
        "id": "stability-warn",
        "param": "stability",
        "threshold": 0.5,
        "direction": "below",
        "action": "warn",
        "message": "warn",
    },
    {
        "id": "stability-block",
        "param": "stability",
        "threshold": 0.3,
        "direction": "below",
        "action": "block",
        "message": "block",
        "resetMode": "manual",
    },
    {
        "id": "vigilance-high",
        "param": "vigilance",
        "threshold": 0.9,
        "direction": "above",
        "action": "notify",
        "message": "notify",
        "hysteresis": 0.1,
    },
]

PURPOSE_RULES = {
    "version": 1,
    "crew": "iam-guardian",
    "rules": [
        {
            "id": "approval-required-for-change",
            "severity": "block",
            "message": "Changes require approval",
            "all": [
                {
                    "field": "operation.kind",
                    "operator": "in",
                    "value": ["create", "update", "delete"],
                },
                {
                    "field": "approval.granted",
                    "operator": "not_eq",
                    "value": True,
                },
            ],
        }
    ],
}


def test_apply_event_ports_ts_math_clamping_and_immutability():
    state_before = deepcopy(STATE)
    matrix_before = deepcopy(MATRIX)
    result = apply_event(
        STATE,
        {
            "id": "evt-1",
            "timestamp": "2026-01-01T01:00:00Z",
            "category": "joy",
            "intensity": 0.5,
        },
        MATRIX,
    )
    assert result["params"]["curiosity"] == 0.85
    assert result["params"]["vigilance"] == 0.2
    assert result["params"]["satisfaction"] == 1.0
    assert STATE == state_before
    assert MATRIX == matrix_before


def test_apply_event_unknown_category_is_equal_but_new_state():
    result = apply_event(
        STATE,
        {
            "timestamp": "2026-01-01T01:00:00Z",
            "category": "unknown",
            "intensity": 1.0,
        },
        MATRIX,
    )
    assert result == STATE
    assert result is not STATE
    assert result["params"] is not STATE["params"]


def test_apply_event_rejects_wrong_state_and_matrix_versions():
    with pytest.raises(SchemaVersionError):
        apply_event(
            {**STATE, "version": 2},
            {"timestamp": "t", "category": "joy", "intensity": 1.0},
            MATRIX,
        )
    with pytest.raises(SchemaVersionError):
        apply_event(
            STATE,
            {"timestamp": "t", "category": "joy", "intensity": 1.0},
            {**MATRIX, "version": 2},
        )


def test_decay_ports_ts_math_floor_and_requires_explicit_clock():
    config = {
        "rules": [
            {"param": "curiosity", "ratePerHour": 0.05, "floor": 0.3},
            {"param": "vigilance", "ratePerHour": 0.025, "floor": 0.1},
        ]
    }
    result = decay(STATE, 7_200_000, config, "2026-01-01T02:00:00Z")
    assert result["params"]["curiosity"] == 0.4
    assert result["params"]["vigilance"] == 0.25
    assert result["params"]["stability"] == 0.8
    with pytest.raises(EngineValidationError, match="clock"):
        decay(STATE, 1, config)


def test_decay_zero_elapsed_is_noop_without_timestamp():
    result = decay(STATE, 0, {"rules": []})
    assert result == STATE
    assert result is not STATE


def test_breakers_port_thresholds_and_fixed_core_constraint():
    low = {**STATE, "params": {**STATE["params"], "stability": 0.25}}
    assert [item["ruleId"] for item in get_fired_breakers(low, BREAKERS)] == [
        "stability-warn",
        "stability-block",
    ]
    results = check_breakers(STATE, BREAKERS)
    assert all(not item["fired"] for item in results)
    invalid = [
        {
            "id": "growth-block",
            "param": "growth",
            "threshold": 0.1,
            "direction": "below",
            "action": "block",
            "message": "invalid",
        }
    ]
    with pytest.raises(EngineValidationError, match="stability"):
        check_breakers(STATE, invalid)


def test_breaker_recovery_preserves_manual_and_unknown_rules():
    recovered = {
        **STATE,
        "params": {**STATE["params"], "stability": 0.55},
        "activeBreakers": ["stability-warn", "stability-block", "old-rule"],
    }
    assert resolve_breakers(recovered, BREAKERS) == {
        "resolved": ["stability-warn"],
        "stillActive": ["stability-block", "old-rule"],
    }


def test_detect_matrix_drift_is_deterministic_and_does_not_mutate_baseline():
    current = deepcopy(MATRIX)
    current["triggers"]["joy"]["curiosity"] = -0.3
    baseline_before = deepcopy(MATRIX)
    kwargs = {"threshold": 0.5, "observed_at": "2026-09-09T01:00:00Z"}
    first = detect_matrix_drift(current, MATRIX, **kwargs)
    second = detect_matrix_drift(current, MATRIX, **kwargs)
    assert first == second
    assert first["drifted"] is True
    assert first["signFlips"] == [{"trigger": "joy", "param": "curiosity"}]
    assert MATRIX == baseline_before


def test_purpose_drift_detects_unapproved_change_and_missing_approval():
    unapproved = {
        "version": 1,
        "operation": {"kind": "update"},
        "approval": {"granted": False},
    }
    missing = {"version": 1, "operation": {"kind": "delete"}}
    for context in (unapproved, missing):
        result = evaluate_purpose_drift(
            context, PURPOSE_RULES, evaluated_at="2026-09-09T01:00:00Z"
        )
        assert result["drifted"] is True
        assert result["violations"][0]["ruleId"] == "approval-required-for-change"


def test_purpose_drift_allows_read_or_explicitly_approved_change():
    read = {"version": 1, "operation": {"kind": "read"}}
    approved = {
        "version": 1,
        "operation": {"kind": "update"},
        "approval": {"granted": True},
    }
    for context in (read, approved):
        result = evaluate_purpose_drift(
            context, PURPOSE_RULES, evaluated_at="2026-09-09T01:00:00Z"
        )
        assert result["drifted"] is False
        assert result["violations"] == []


def test_purpose_drift_rejects_version_mismatch_unknown_operator_and_duplicates():
    with pytest.raises(SchemaVersionError):
        evaluate_purpose_drift(
            {"version": 2}, PURPOSE_RULES, evaluated_at="2026-09-09T01:00:00Z"
        )
    bad_operator = deepcopy(PURPOSE_RULES)
    bad_operator["rules"][0]["all"][0]["operator"] = "exec"
    with pytest.raises(EngineValidationError, match="unsupported"):
        evaluate_purpose_drift(
            {"version": 1}, bad_operator, evaluated_at="2026-09-09T01:00:00Z"
        )
    duplicate = deepcopy(PURPOSE_RULES)
    duplicate["rules"].append(deepcopy(duplicate["rules"][0]))
    with pytest.raises(EngineValidationError, match="duplicate"):
        evaluate_purpose_drift(
            {"version": 1}, duplicate, evaluated_at="2026-09-09T01:00:00Z"
        )


def test_mission_achievement_increases_xp_and_growth_without_matrix_access():
    state_before = deepcopy(STATE)
    result = apply_mission_achievement(
        STATE,
        {
            "version": 1,
            "achieved": True,
            "impact": 0.8,
            "timestamp": "2026-09-09T02:00:00Z",
        },
        {"version": 1, "baseXp": 100, "growthPerXp": 0.001},
    )
    assert result["xp"] == 90
    assert result["params"]["growth"] == 0.28
    assert result["updated"] == "2026-09-09T02:00:00Z"
    assert STATE == state_before


def test_unachieved_mission_is_noop_and_growth_is_clamped():
    no_op = apply_mission_achievement(
        STATE,
        {"version": 1, "achieved": False},
        {"version": 1, "baseXp": 100, "growthPerXp": 0.01},
    )
    assert no_op == STATE
    high = {**STATE, "params": {**STATE["params"], "growth": 0.95}}
    capped = apply_mission_achievement(
        high,
        {
            "version": 1,
            "achieved": True,
            "impact": 1.0,
            "timestamp": "2026-09-09T02:00:00Z",
        },
        {"version": 1, "baseXp": 100, "growthPerXp": 0.01},
    )
    assert capped["params"]["growth"] == 1.0


def test_xp_policy_version_mismatch_is_rejected():
    with pytest.raises(SchemaVersionError):
        apply_mission_achievement(
            STATE,
            {"version": 1, "achieved": False},
            {"version": 2, "baseXp": 100, "growthPerXp": 0.01},
        )


def test_means_drift_hook_only_builds_adapter_request_and_is_deterministic():
    context = {
        "version": 1,
        "goal": "protect egress",
        "proposedMeans": "introduce proxy",
    }
    kwargs = {
        "request_id": "means-1",
        "requested_at": "2026-09-09T03:00:00Z",
        "growth": 0.8,
    }
    first = build_means_drift_request(context, **kwargs)
    second = build_means_drift_request(context, **kwargs)
    assert first == second
    assert first["requiresAdapterJudgment"] is True
    assert first["habituationReviewRequired"] is True
    assert "judgment" not in first


def test_core_hash_uses_exact_bytes_and_validates_digest():
    content = "# P0\nMission\n"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert compute_core_hash(content) == expected
    assert compute_core_hash(content.encode("utf-8")) == expected
    assert core_hash_matches(content, expected.upper()) is True
    assert compute_core_hash(content.rstrip()) != expected
    with pytest.raises(EngineValidationError):
        core_hash_matches(content, "not-a-hash")


def test_same_inputs_produce_same_outputs_across_public_state_transforms():
    event = {
        "timestamp": "2026-09-09T04:00:00Z",
        "category": "threat",
        "intensity": 0.4,
    }
    assert apply_event(STATE, event, MATRIX) == apply_event(STATE, event, MATRIX)
    config = {"rules": [{"param": "curiosity", "ratePerHour": 0.1, "floor": 0.0}]}
    assert decay(STATE, 1000, config, "2026-09-09T04:00:01Z") == decay(
        STATE, 1000, config, "2026-09-09T04:00:01Z"
    )
