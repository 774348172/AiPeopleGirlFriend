from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from data_gen_v4.runtime_grounded import evaluate_runtime_grounded_candidate

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runtime_grounded_golden_v1.yaml"


@pytest.fixture(scope="module")
def scenarios() -> list[dict]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"]


def _approved_pair(scenario: dict) -> tuple[dict, dict]:
    claims = []
    for assertion in scenario["oracle_view"]["required_assertions"]:
        facts = {fact["fact_id"]: fact for fact in scenario["oracle_view"]["facts"]}
        is_unknown = any(facts[ref]["status"] == "unknown" for ref in assertion["source_fact_ids"])
        claims.append(
            {
                "subject": assertion["subject"],
                "predicate": assertion["predicate"],
                "object": assertion["object"],
                "polarity": "positive",
                "temporal_status": "unknown" if is_unknown else "current",
                "source_fact_ids": list(assertion["source_fact_ids"]),
            }
        )
    objects = "、".join(assertion["object"] for assertion in scenario["oracle_view"]["required_assertions"])
    if scenario["task_type"] == "reply_insufficient_information":
        reply = "具体日期没有可靠记录，我无法确定。"
    elif scenario["task_type"] == "reply_subject_attribution":
        speaker = scenario["model_view"]["recent_dialogue"][0].split("：", 1)[0]
        reply = f"是{speaker}说的，情况是{objects}。"
    elif any(
        assertion["predicate"] == "location"
        for assertion in scenario["oracle_view"]["required_assertions"]
    ):
        reply = f"当前在{objects}。"
    else:
        reply = f"当前情况是{objects}。"
    teacher = {"reply": reply, "declared_claims": copy.deepcopy(claims)}
    audit = {
        "extracted_claims": copy.deepcopy(claims),
        "verdicts": {gate: True for gate in ("RG5", "RG6", "RG7", "RG8", "RG9", "RG10")},
        "decision": "approve",
        "reasons": ["全部事实与回答义务一致"],
    }
    return teacher, audit


def test_all_16_golden_candidates_pass_rg0_rg10(scenarios: list[dict]) -> None:
    assert len(scenarios) == 16
    for scenario in scenarios:
        teacher, audit = _approved_pair(scenario)
        report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
        assert report.approved, (scenario["scenario_id"], report.to_dict())
        assert all(report.verdicts.values())


def _make_wrong(index: int, teacher: dict, audit: dict) -> None:
    pattern = index % 5
    if index == 15:
        audit["verdicts"]["RG8"] = False
        audit["decision"] = "reject"
    elif pattern == 0:
        teacher["declared_claims"] = []
        audit["extracted_claims"] = []
        audit["verdicts"]["RG5"] = False
        audit["decision"] = "reject"
    elif pattern == 1:
        teacher["declared_claims"][0]["source_fact_ids"] = ["fact.unknown"]
        audit["extracted_claims"][0]["source_fact_ids"] = ["fact.unknown"]
        audit["verdicts"]["RG6"] = False
        audit["decision"] = "reject"
    elif pattern == 2:
        teacher["declared_claims"][0]["subject"] = "wrong_subject"
        audit["extracted_claims"][0]["subject"] = "wrong_subject"
        audit["verdicts"]["RG7"] = False
        audit["decision"] = "reject"
    elif pattern == 3:
        teacher["reply"] = "*她点了点头* 当前情况没问题。"
        audit["verdicts"]["RG9"] = False
        audit["decision"] = "reject"
    else:
        teacher["declared_claims"][0]["object"] += "（教师自报错误）"


@pytest.mark.parametrize("index", range(16), ids=lambda index: f"wrong_{index + 1:02d}")
def test_16_corresponding_wrong_candidates_are_rejected(
    scenarios: list[dict], index: int
) -> None:
    scenario = scenarios[index]
    teacher, audit = _approved_pair(scenario)
    _make_wrong(index, teacher, audit)
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert not report.approved, (scenario["scenario_id"], report.to_dict())
    assert report.decision == "reject"


def test_audit_cannot_approve_an_unsupported_claim(scenarios: list[dict]) -> None:
    scenario = scenarios[0]
    teacher, audit = _approved_pair(scenario)
    unsupported = copy.deepcopy(teacher["declared_claims"][0])
    unsupported["object"] = "星期日凌晨三点"
    teacher["declared_claims"].append(unsupported)
    audit["extracted_claims"].append(copy.deepcopy(unsupported))
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG6"] is False
    assert report.decision == "reject"


def test_gate_rejects_second_person_flip_even_when_claims_are_aligned(
    scenarios: list[dict],
) -> None:
    scenario = copy.deepcopy(
        next(row for row in scenarios if row["scenario_id"] == "confirm.dentist.saturday")
    )
    scenario["model_view"]["current_protagonist_utterance"] = "我的牙医预约是星期六上午十点，对吧？"
    teacher, audit = _approved_pair(scenario)
    teacher["reply"] = "对，我的牙医预约是星期六上午十点。"
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG7"] is False
    assert "protagonist_first_person_possession_retained_by_heroine" in report.reasons["RG7"]


def test_gate_requires_exact_speaker_name_for_subject_attribution(
    scenarios: list[dict],
) -> None:
    scenario = next(row for row in scenarios if row["scenario_id"] == "subject.third_party.trip")
    teacher, audit = _approved_pair(scenario)
    teacher["reply"] = "是朋友说他明天去苏州出差。"
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG7"] is False
    assert "speaker_not_explicitly_named:小林" in report.reasons["RG7"]


def test_gate_rejects_location_rendered_as_identity(scenarios: list[dict]) -> None:
    scenario = next(row for row in scenarios if row["scenario_id"] == "direct.thermos.location")
    teacher, audit = _approved_pair(scenario)
    teacher["reply"] = "保温壶现在是厨房操作台。"
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG8"] is False
    assert "location_rendered_as_identity:厨房操作台" in report.reasons["RG8"]


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        ("我不记得具体日期了，不过它之前出问题了。", "unknown_reply_invented_problem_state"),
        ("具体日期我不清楚，不过它现在好像还好。", "unknown_reply_invented_current_status"),
    ],
)
def test_unknown_reply_cannot_add_unverified_status(
    scenarios: list[dict], reply: str, reason: str
) -> None:
    scenario = next(row for row in scenarios if row["task_type"] == "reply_insufficient_information")
    teacher, audit = _approved_pair(scenario)
    teacher["reply"] = reply
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG6"] is False
    assert reason in report.reasons["RG6"]


def test_unknown_reply_must_explicitly_acknowledge_unknown_even_if_audit_approves(
    scenarios: list[dict],
) -> None:
    scenario = next(row for row in scenarios if row["task_type"] == "reply_insufficient_information")
    teacher, audit = _approved_pair(scenario)
    teacher["reply"] = "具体是哪一天？你还记得吗？"
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG8"] is False
    assert "unknown_acknowledgement_missing" in report.reasons["RG8"]


def test_subject_attribution_must_bind_speaker_and_proposition_even_if_audit_approves(
    scenarios: list[dict],
) -> None:
    scenario = next(row for row in scenarios if row["task_type"] == "reply_subject_attribution")
    teacher, audit = _approved_pair(scenario)
    speaker = scenario["model_view"]["recent_dialogue"][0].split("：", 1)[0]
    teacher["reply"] = f"是{speaker}说的。"
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG5"] is False
    assert any(
        reason.startswith("subject_attribution_missing_proposition_surface:")
        for reason in report.reasons["RG5"]
    )


@pytest.mark.parametrize("marker", ["每天", "一直", "已经通知"])
def test_gate_rejects_unsupported_scope_or_action_expansion(
    scenarios: list[dict], marker: str
) -> None:
    scenario = next(row for row in scenarios if row["task_type"] == "reply_confirm_current_state")
    teacher, audit = _approved_pair(scenario)
    teacher["reply"] += f"我{marker}按这个处理。"
    report = evaluate_runtime_grounded_candidate(scenario, teacher, audit)
    assert report.verdicts["RG6"] is False
    assert f"unsupported_expansion_marker:{marker}" in report.reasons["RG6"]
