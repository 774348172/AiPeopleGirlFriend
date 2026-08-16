from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .model_gateway import WORLD_CONTINUITY_REVIEW
from .wmr08 import WMR08_MODES


SYS11_SCHEMA_VERSION = 1


def load_sys11_workload(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != SYS11_SCHEMA_VERSION:
        raise ValueError("SYS-11 workload schema_version must equal 1")
    if value.get("duration_seconds") != 3600 or value.get("turn_count") != 60:
        raise ValueError("SYS-11 workload must freeze 60 turns over one hour")
    injections = value.get("injections")
    if not isinstance(injections, Mapping):
        raise ValueError("SYS-11 workload injections are invalid")
    required = {
        "cancel_then_retry",
        "invalid_json_retry",
        "duplicate_replay",
        "request_conflict",
        "process_crash",
        "projection_rebuild",
        "second_heroine",
    }
    if set(injections) != required:
        raise ValueError("SYS-11 workload injections do not match the frozen set")
    canonical = dict(value)
    declared = canonical.pop("workload_sha256", None)
    actual = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if declared != actual:
        raise ValueError("SYS-11 workload SHA256 mismatch")
    return value


def evaluate_sys11(report: Mapping[str, Any]) -> dict[str, Any]:
    transactions = report.get("transactions", {})
    consistency = report.get("consistency", {})
    queue = report.get("queue", {})
    recovery = report.get("recovery", {})
    isolation = report.get("isolation", {})
    snapshots = report.get("snapshots", {})
    timing = report.get("timing", {})
    failures = report.get("failures", {})
    successful_modes = set(report.get("model_modes", {}))
    always_required_modes = set(WMR08_MODES) - {WORLD_CONTINUITY_REVIEW}
    conditional_modes = report.get("conditional_model_modes")
    if isinstance(conditional_modes, Mapping):
        critic = conditional_modes.get(WORLD_CONTINUITY_REVIEW, {})
        critic_triggered = int(critic.get("triggered", -1))
        critic_completed = int(critic.get("committed_successes", -1))
        critic_coverage = critic_triggered >= 0 and (
            critic_triggered == 0 or critic_completed >= critic_triggered
        )
    else:
        critic_coverage = WORLD_CONTINUITY_REVIEW in successful_modes
    checks = {
        "one_hour_elapsed": float(timing.get("wall_elapsed_seconds", 0)) >= 3600,
        "turn_count": transactions.get("planned_attempts") == 60,
        "transaction_accounting": (
            int(transactions.get("committed", -1))
            + int(transactions.get("explicit_failures", -1))
            == 60
        ),
        "no_duplicate_or_partial": all(
            int(consistency.get(name, -1)) == 0
            for name in (
                "orphan_turns",
                "orphan_events",
                "duplicate_requests",
                "event_count_mismatch",
                "dangling_model_decisions",
            )
        ),
        "queue_drained": queue.get("pending") == 0 and queue.get("running") == 0,
        "r1_memory_queue_drained": (
            queue.get("r1_memory", {}).get("pending") == 0
            and queue.get("r1_memory", {}).get("running") == 0
            and queue.get("r1_memory", {}).get("failed") == 0
        ),
        "queue_bounded": 0 <= int(queue.get("peak_pending_jobs", -1)) <= 16,
        "crash_recovered": recovery.get("process_crash_recovered") is True,
        "projection_rebuilt": recovery.get("projection_rebuild_passed") is True,
        "snapshot_frozen": snapshots.get("torn_snapshot_count") == 0,
        "next_turn_latest": snapshots.get("stale_next_turn_count") == 0,
        "isolation": isolation.get("leak_count") == 0,
        "no_unrecoverable_failure": failures.get("unrecoverable") == 0,
        "cancel_recovered": failures.get("cancel_injection_observed") is True,
        "structured_retry_recovered": failures.get("invalid_json_retry_observed") is True,
        "duplicate_replay_observed": failures.get("duplicate_replay_observed") is True,
        "request_conflict_observed": failures.get("request_conflict_observed") is True,
        "all_real_model_modes": (
            successful_modes >= always_required_modes and critic_coverage
        ),
        "resource_growth_bounded": report.get("resources", {}).get("growth_bounded") is True,
    }
    passed = all(checks.values())
    return {
        "checks": checks,
        "sys11_gate": "passed" if passed else "failed",
        "system_stability_passed": passed,
        "character_quality_gate": "deferred",
        "release_performance_gate": "deferred_to_sys12",
    }
