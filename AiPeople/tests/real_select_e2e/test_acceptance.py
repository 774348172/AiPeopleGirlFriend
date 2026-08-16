from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.real_select_e2e.acceptance import AcceptanceEvidenceError, assess_product_evidence


PROFILE = Path("eval/real_select_e2e/real_acceptance_profile_v1.json")


def profile() -> dict:
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def evidence() -> dict:
    return {
        "evidence_source": "verified_real_local_assets",
        "artifact_sha256": {"bge": "a" * 64, "reranker": "b" * 64, "reply": "c" * 64},
        "case_count": 200,
        "global_pool_memory_count": 10000,
        "global_recall_at_32": 0.98,
        "natural_recall_accuracy": 0.90,
        "no_memory_false_activation_rate": 0.05,
        "hard_negative_rejection_rate": 0.90,
        "selected_memory_precision": 0.90,
        "paired_bootstrap_95ci_lower_bound": 0.001,
        "selector_total_p95_ms": 299.0,
        "reply_first_token_p95_ms": 1999.0,
        "combined_gpu_peak_mib": 5119.0,
        "continuous_run_minutes": 60,
        "continuous_gpu_growth": False,
        "network_access": "forbidden",
    }


def test_pre_registered_boundary_values_pass_without_relaxing_strict_gates() -> None:
    result = assess_product_evidence(profile(), evidence())
    assert result["status"] == "passed"
    assert result["blockers"] == []


def test_engineering_smoke_counts_cannot_be_promoted_to_product_pass() -> None:
    value = evidence()
    value["case_count"] = 8
    value["global_pool_memory_count"] = 40
    result = assess_product_evidence(profile(), value)
    assert result["status"] == "blocked"
    assert any("case_count" in blocker for blocker in result["blockers"])
    assert any("global_pool_memory_count" in blocker for blocker in result["blockers"])


def test_fake_or_unbound_evidence_is_rejected_before_metric_evaluation() -> None:
    value = evidence()
    value["evidence_source"] = "fake_backend"
    with pytest.raises(AcceptanceEvidenceError, match="verified real"):
        assess_product_evidence(profile(), value)
