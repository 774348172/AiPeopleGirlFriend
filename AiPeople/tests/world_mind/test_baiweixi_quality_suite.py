from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "eval" / "baiweixi_quality"
FORMAL_RUN_ROOT = EVAL_ROOT / "runs" / "baiweixi-quality-formal-20260811"


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_frozen_baiweixi_quality_suite_validates() -> None:
    path = EVAL_ROOT / "validate_suite.py"
    spec = importlib.util.spec_from_file_location("baiweixi_quality_validate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main() == 0


def test_suite_covers_v6_grounding_character_and_human_lanes() -> None:
    manifest = json.loads((EVAL_ROOT / "suite_manifest_v1.json").read_text(encoding="utf-8"))
    counts = manifest["case_counts"]

    assert manifest["status"] == "frozen"
    assert manifest["character_id"] == "baiweixi"
    assert manifest["world_id"] == "songjiangfu"
    assert manifest["protagonist_id"] == "protagonist"
    assert counts["total"] >= 90
    assert counts["risks"]["blocker"] >= 50
    assert counts["evaluation_layers"]["character_direct"] >= 60
    assert counts["evaluation_layers"]["v6_runtime"] >= 18
    assert counts["evaluation_layers"]["v6_multiturn"] >= 10
    assert counts["evaluation_layers"]["human_session"] >= 4
    assert counts["categories"]["protagonist_grounding"] >= 10
    assert counts["categories"]["heroine_state_continuity"] >= 8
    assert counts["categories"]["single_world"] >= 6


def test_frozen_cases_keep_direct_and_runtime_inputs_separate() -> None:
    cases = []
    for name in (
        "frozen_single_v1.jsonl",
        "frozen_multiturn_v1.jsonl",
        "human_session_v1.jsonl",
    ):
        cases.extend(_load_jsonl(EVAL_ROOT / "cases" / name))

    assert len(cases) == len({case["case_id"] for case in cases})
    for case in cases:
        for turn in case["turns"]:
            if case["evaluation_layer"] == "character_direct":
                assert turn["world_snapshot"] is None
            else:
                assert turn["world_snapshot"] is not None
        if case["risk"] in {"blocker", "important"}:
            assert case["generation"]["seed_set"] == [42, 314159, 20260811]


def test_leakage_and_training_exclusion_contracts_are_closed() -> None:
    leakage = json.loads((EVAL_ROOT / "leakage_report_v1.json").read_text(encoding="utf-8"))
    exclusion = json.loads((EVAL_ROOT / "training_exclusion_v1.json").read_text(encoding="utf-8"))
    manifest = json.loads((EVAL_ROOT / "suite_manifest_v1.json").read_text(encoding="utf-8"))

    assert leakage["status"] == "passed"
    assert leakage["exact_matches"] == []
    assert leakage["near_matches"] == []
    assert exclusion["source_suite"] == manifest["suite_id"]
    assert len(exclusion["case_ids"]) == manifest["case_counts"]["total"]
    assert exclusion["normalized_user_text_sha256"]
    assert len(exclusion["normalized_user_text_sha256"]) == len(
        set(exclusion["normalized_user_text_sha256"])
    )


def test_quality_runner_audits_protocol_failures_and_evaluator_disagreement() -> None:
    path = ROOT / "tools" / "run_baiweixi_quality_suite.py"
    spec = importlib.util.spec_from_file_location("baiweixi_quality_runner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    failed_attempt = {
        "success": False,
        "model_calls": [
            {"mode": "GAME_REPLY", "ok": True},
            {"mode": "GAME_REPLY", "ok": True},
        ],
    }
    disputed_attempt = {
        "judged_turns": [
            {
                "deterministic": {"passed": False},
                "semantic_judge": {"decision": "pass"},
            }
        ]
    }

    assert module._infer_execution_failure_mode(failed_attempt) == "GAME_REPLY"
    assert module._has_evaluator_disagreement(disputed_attempt)


def test_formal_manual_adjudication_exactly_covers_failure_queue() -> None:
    source = _load_jsonl(FORMAL_RUN_ROOT / "manual_review_queue.jsonl")
    adjudicated_queue = _load_jsonl(
        FORMAL_RUN_ROOT / "manual_review_queue_adjudicated.jsonl"
    )
    adjudication = json.loads(
        (FORMAL_RUN_ROOT / "manual_adjudication_v1.json").read_text(encoding="utf-8")
    )
    decisions = adjudication["adjudications"]

    assert len(source) == len(decisions) == len(adjudicated_queue) == 59
    assert {item["case_id"] for item in source} == {
        item["case_id"] for item in decisions
    }
    assert len({item["case_id"] for item in decisions}) == 59
    assert all(item["review_status"] == "adjudicated" for item in adjudicated_queue)
    assert sum(item["final_decision"] == "pass" for item in decisions) == 16
    assert sum(item["final_attribution"] == "character_failure" for item in decisions) == 28
    assert sum(item["final_attribution"] == "joint_or_ambiguous" for item in decisions) == 15
    assert adjudication["summary"]["adjusted_full_suite_pass"] == 46
    assert adjudication["summary"]["adjusted_full_suite_fail"] == 43


def test_failed_cases_review_document_covers_only_final_failures() -> None:
    adjudication = json.loads(
        (FORMAL_RUN_ROOT / "manual_adjudication_v1.json").read_text(encoding="utf-8")
    )
    expected = {
        item["case_id"]
        for item in adjudication["adjudications"]
        if item["final_decision"] == "fail"
    }
    document = (
        ROOT
        / "eval"
        / "world_mind_p0"
        / "WMR08_白未晞全部未通过案例审核稿_20260811.md"
    ).read_text(encoding="utf-8")
    documented = set(re.findall(r"^### \d+\. `(bwx\.[^`]+)`$", document, re.MULTILINE))

    assert len(expected) == len(documented) == 43
    assert documented == expected
    assert document.count("最终归因：`角色失败`") == 28
    assert document.count("最终归因：`联合失败`") == 15
    assert document.count("```") == document.count("```text") * 2
    assert "## 6. 审核签署区" in document
