from __future__ import annotations

from typing import Any


class AcceptanceEvidenceError(RuntimeError):
    pass


def assess_product_evidence(
    profile: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    if profile.get("profile_id") != "real-select-e2e-acceptance-v1":
        raise AcceptanceEvidenceError("unexpected REAL acceptance profile")
    if evidence.get("evidence_source") != "verified_real_local_assets":
        raise AcceptanceEvidenceError("product evidence must come from verified real local assets")
    required_hashes = evidence.get("artifact_sha256")
    if not isinstance(required_hashes, dict) or set(required_hashes) != {
        "bge",
        "reranker",
        "reply",
    }:
        raise AcceptanceEvidenceError("real evidence must bind all three artifact SHA256 values")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
        for value in required_hashes.values()
    ):
        raise AcceptanceEvidenceError("artifact SHA256 values are invalid")
    quality = profile["pre_registered_quality_gates"]
    performance = profile["performance_gates"]
    dataset = profile["product_dataset_requirements"]
    blockers: list[str] = []
    _minimum(blockers, evidence, "case_count", dataset["minimum_case_count"])
    _minimum(
        blockers,
        evidence,
        "global_pool_memory_count",
        dataset["minimum_global_pool_memory_count"],
    )
    _minimum(blockers, evidence, "global_recall_at_32", quality["global_recall_at_32_min"])
    _minimum(
        blockers,
        evidence,
        "natural_recall_accuracy",
        quality["natural_recall_accuracy_min"],
    )
    _maximum(
        blockers,
        evidence,
        "no_memory_false_activation_rate",
        quality["no_memory_false_activation_rate_max"],
    )
    _minimum(
        blockers,
        evidence,
        "hard_negative_rejection_rate",
        quality["hard_negative_rejection_rate_min"],
    )
    _minimum(
        blockers,
        evidence,
        "selected_memory_precision",
        quality["selected_memory_precision_min"],
    )
    _strict_minimum(
        blockers,
        evidence,
        "paired_bootstrap_95ci_lower_bound",
        quality["paired_bootstrap_95ci_lower_bound_min_exclusive"],
    )
    _strict_maximum(
        blockers,
        evidence,
        "selector_total_p95_ms",
        performance["selector_total_p95_ms_max_exclusive"],
    )
    _strict_maximum(
        blockers,
        evidence,
        "reply_first_token_p95_ms",
        performance["reply_first_token_p95_ms_max_exclusive"],
    )
    _strict_maximum(
        blockers,
        evidence,
        "combined_gpu_peak_mib",
        performance["combined_gpu_peak_mib_max_exclusive"],
    )
    _minimum(
        blockers,
        evidence,
        "continuous_run_minutes",
        performance["continuous_run_minutes_min"],
    )
    if evidence.get("continuous_gpu_growth") is not False:
        blockers.append("continuous_gpu_growth must be false")
    if evidence.get("network_access") != "forbidden":
        blockers.append("network_access must be forbidden")
    return {
        "schema_version": 1,
        "profile_id": profile["profile_id"],
        "status": "passed" if not blockers else "blocked",
        "blockers": blockers,
        "artifact_sha256": required_hashes,
    }


def _number(evidence: dict[str, Any], field: str) -> float | None:
    value = evidence.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _minimum(blockers: list[str], evidence: dict[str, Any], field: str, threshold: float) -> None:
    value = _number(evidence, field)
    if value is None or value < threshold:
        blockers.append(f"{field} must be >= {threshold}")


def _maximum(blockers: list[str], evidence: dict[str, Any], field: str, threshold: float) -> None:
    value = _number(evidence, field)
    if value is None or value > threshold:
        blockers.append(f"{field} must be <= {threshold}")


def _strict_minimum(
    blockers: list[str], evidence: dict[str, Any], field: str, threshold: float
) -> None:
    value = _number(evidence, field)
    if value is None or value <= threshold:
        blockers.append(f"{field} must be > {threshold}")


def _strict_maximum(
    blockers: list[str], evidence: dict[str, Any], field: str, threshold: float
) -> None:
    value = _number(evidence, field)
    if value is None or value >= threshold:
        blockers.append(f"{field} must be < {threshold}")
