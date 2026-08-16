from __future__ import annotations

from copy import deepcopy

from runtime.world_mind.sys11 import evaluate_sys11, load_sys11_workload
from tests.world_mind._helpers import ROOT


def test_sys11_workload_is_frozen_and_complete() -> None:
    workload = load_sys11_workload(
        ROOT / "eval" / "world_mind_p0" / "sys11_workload_v1.json"
    )
    assert workload["turn_count"] == 60
    assert workload["duration_seconds"] == 3600
    assert workload["injections"]["process_crash"] == 24


def test_sys11_gate_requires_every_stability_boundary() -> None:
    report = {
        "timing": {"wall_elapsed_seconds": 3600},
        "transactions": {
            "planned_attempts": 60,
            "committed": 57,
            "explicit_failures": 3,
        },
        "consistency": {
            "orphan_turns": 0,
            "orphan_events": 0,
            "duplicate_requests": 0,
            "event_count_mismatch": 0,
            "dangling_model_decisions": 0,
        },
        "queue": {
            "pending": 0,
            "running": 0,
            "peak_pending_jobs": 4,
            "r1_memory": {
                "pending": 0,
                "running": 0,
                "completed": 60,
                "failed": 0,
            },
        },
        "recovery": {
            "process_crash_recovered": True,
            "projection_rebuild_passed": True,
        },
        "snapshots": {"torn_snapshot_count": 0, "stale_next_turn_count": 0},
        "isolation": {"leak_count": 0},
        "failures": {
            "unrecoverable": 0,
            "cancel_injection_observed": True,
            "invalid_json_retry_observed": True,
            "duplicate_replay_observed": True,
            "request_conflict_observed": True,
        },
        "model_modes": {
            "MIND_PATCH_V2": 1,
            "GAME_REPLY": 1,
            "WORLD_CONTINUITY_REVIEW": 1,
            "POST_REPLY_WORLD_MIND_RECONCILE": 1,
            "FIVE_MINUTE_WORLD_MIND_RECONCILE": 1,
            "MEMORY_PROPOSE": 1,
        },
        "conditional_model_modes": {
            "WORLD_CONTINUITY_REVIEW": {
                "triggered": 1,
                "committed_successes": 1,
                "successful_backend_calls": 1,
            }
        },
        "resources": {"growth_bounded": True},
    }
    assert evaluate_sys11(report)["sys11_gate"] == "passed"
    broken = deepcopy(report)
    broken["consistency"]["orphan_events"] = 1
    assert evaluate_sys11(broken)["sys11_gate"] == "failed"
    for key in (
        "cancel_injection_observed",
        "invalid_json_retry_observed",
        "duplicate_replay_observed",
        "request_conflict_observed",
    ):
        broken = deepcopy(report)
        broken["failures"][key] = False
        assert evaluate_sys11(broken)["sys11_gate"] == "failed"
    broken = deepcopy(report)
    del broken["model_modes"]["MEMORY_PROPOSE"]
    assert evaluate_sys11(broken)["sys11_gate"] == "failed"


def test_sys11_gate_accepts_an_untriggered_conditional_critic() -> None:
    report = {
        "timing": {"wall_elapsed_seconds": 3600},
        "transactions": {
            "planned_attempts": 60,
            "committed": 60,
            "explicit_failures": 0,
        },
        "consistency": {
            "orphan_turns": 0,
            "orphan_events": 0,
            "duplicate_requests": 0,
            "event_count_mismatch": 0,
            "dangling_model_decisions": 0,
        },
        "queue": {
            "pending": 0,
            "running": 0,
            "peak_pending_jobs": 4,
            "r1_memory": {"pending": 0, "running": 0, "completed": 60, "failed": 0},
        },
        "recovery": {
            "process_crash_recovered": True,
            "projection_rebuild_passed": True,
        },
        "snapshots": {"torn_snapshot_count": 0, "stale_next_turn_count": 0},
        "isolation": {"leak_count": 0},
        "failures": {
            "unrecoverable": 0,
            "cancel_injection_observed": True,
            "invalid_json_retry_observed": True,
            "duplicate_replay_observed": True,
            "request_conflict_observed": True,
        },
        "model_modes": {
            "MIND_PATCH_V2": 60,
            "GAME_REPLY": 60,
            "POST_REPLY_WORLD_MIND_RECONCILE": 60,
            "FIVE_MINUTE_WORLD_MIND_RECONCILE": 12,
            "MEMORY_PROPOSE": 60,
        },
        "conditional_model_modes": {
            "WORLD_CONTINUITY_REVIEW": {
                "triggered": 0,
                "committed_successes": 0,
                "successful_backend_calls": 0,
            }
        },
        "resources": {"growth_bounded": True},
    }
    assert evaluate_sys11(report)["checks"]["all_real_model_modes"] is True
    broken = deepcopy(report)
    broken["conditional_model_modes"]["WORLD_CONTINUITY_REVIEW"]["triggered"] = 1
    assert evaluate_sys11(broken)["checks"]["all_real_model_modes"] is False
