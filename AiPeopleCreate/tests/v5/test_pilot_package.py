from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from data_gen_v4.adapters.modes.training import TrainingRecordV4
from data_gen_v4.runtime_grounded import PilotPackageError, build_pilot_package
from data_gen_v4.runtime_grounded.pilot import _family_key

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runtime_grounded_golden_v1.yaml"


@pytest.fixture(scope="module")
def golden_scenarios() -> list[dict]:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"]


def _grounded_rows(golden_scenarios: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for task_index, task in enumerate(sorted({scenario["task_type"] for scenario in golden_scenarios})):
        base = next(scenario for scenario in golden_scenarios if scenario["task_type"] == task)
        for variant in range(30):
            scenario = copy.deepcopy(base)
            scenario["scenario_id"] = f"pilot.{task_index:02d}.{variant:02d}"
            scenario["split_anchors"] = [
                *scenario["split_anchors"],
                f"variant:{task_index:02d}:{variant:02d}",
            ]
            facts = {fact["fact_id"]: fact for fact in scenario["oracle_view"]["facts"]}
            claims = []
            for assertion in scenario["oracle_view"]["required_assertions"]:
                unknown = any(facts[ref]["status"] == "unknown" for ref in assertion["source_fact_ids"])
                claims.append(
                    {
                        "subject": assertion["subject"],
                        "predicate": assertion["predicate"],
                        "object": assertion["object"],
                        "polarity": "positive",
                        "temporal_status": "unknown" if unknown else "current",
                        "source_fact_ids": list(assertion["source_fact_ids"]),
                    }
                )
            objects = "、".join(
                assertion["object"]
                for assertion in scenario["oracle_view"]["required_assertions"]
            )
            if task == "reply_subject_attribution":
                speaker = scenario["model_view"]["recent_dialogue"][0].split("：", 1)[0]
                reply = f"是{speaker}说的，当前事实是{objects}。"
            elif any(
                assertion["predicate"] == "location"
                for assertion in scenario["oracle_view"]["required_assertions"]
            ):
                reply = f"当前在{objects}。"
            else:
                reply = f"当前事实是{objects}。"
            teacher = {
                "reply": reply,
                "declared_claims": claims,
            }
            audit = {
                "extracted_claims": copy.deepcopy(claims),
                "verdicts": {gate: True for gate in ("RG5", "RG6", "RG7", "RG8", "RG9", "RG10")},
                "decision": "approve",
                "reasons": ["fixture test row"],
            }
            rows.append(
                {
                    "sample_id": scenario["scenario_id"],
                    "scenario": scenario,
                    "teacher_target": teacher,
                    "semantic_audit": audit,
                }
            )
    return rows


def _static_rows_and_audits() -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    audits: list[dict] = []
    for index in range(160):
        sample_id = f"static.pilot.{index:03d}"
        records.append(
            {
                "sample_id": sample_id,
                "task_type": "reply_casual",
                "conversations": [
                    {"from": "system", "value": "你是白未晞。只输出自然对白。"},
                    {"from": "human", "value": f"第{index}个问题。"},
                    {"from": "gpt", "value": "我听到了。"},
                ],
            }
        )
        audits.append(
            {
                "sample_id": sample_id,
                "approved": True,
                "audit_version": "runtime-grounded-static-v1",
                "split_anchor_ids": [f"static-family:{index:03d}"],
            }
        )
    return records, audits


def _sealed_rows() -> list[dict]:
    rows: list[dict] = []
    for index in range(40):
        training = TrainingRecordV4(
            sample_id=f"sealed.pilot.{index:03d}",
            mode="RUNTIME_GROUNDED_REPLY",
            render_profile_id="runtime-grounded-reply-v1",
            messages=[
                {"role": "human", "content": f"封存问题 {index}"},
                {"role": "assistant", "content": "封存回答。"},
            ],
            supervised_message_indexes=[1],
            supervised_token_count=5,
            protocol_snapshot_id="runtime-grounded-reply-v1",
            system_anchor="系统锚",
        )
        rows.append(
            {
                "scenario_id": f"sealed.scenario.{index:03d}",
                "task_type": "reply_direct_answer",
                "split_anchors": [f"sealed-family:{index:03d}"],
                "training_record": training.to_dict(),
            }
        )
    return rows


def test_build_exact_v5_pilot_package(tmp_path: Path, golden_scenarios: list[dict]) -> None:
    report = build_pilot_package(
        grounded_candidates=_grounded_rows(golden_scenarios),
        static_records=_static_rows_and_audits()[0],
        static_reaudit=_static_rows_and_audits()[1],
        sealed_rows=_sealed_rows(),
        output_dir=tmp_path / "baiweixi_v5_pilot_v1",
    )
    assert report.total == 400
    assert report.grounded == 240
    assert report.static == 160
    assert report.sealed_total == 40
    assert report.task_counts["reply_accept_authoritative_update"] == 30
    assert report.task_counts["reply_direct_answer"] == 30
    assert report.assistant_total["runtime_grounded"] == report.supervised_message_total["runtime_grounded"] == 240
    assert report.assistant_total["static_persona"] == report.supervised_message_total["static_persona"] == 160
    assert sum(report.split_counts.values()) == 400
    assert all(
        gate["passed"] == 240 and gate["failed"] == 0
        for gate in report.rg_gate_counts.values()
    )
    assert report.rg_gate_counts["RG11"] == {"passed": 240, "failed": 0}
    out = tmp_path / "baiweixi_v5_pilot_v1"
    assert all((out / f"{name}.jsonl").exists() for name in ("train", "dev", "test", "sealed"))
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "aip.runtime_grounded_pilot_manifest.v1"
    assert manifest["model_calls_during_packaging"] == 0
    assert manifest["training_started"] is False


def test_family_key_uses_only_explicit_family_anchor() -> None:
    first = _family_key(
        ["task:reply_direct_answer", "family:item_location", "predicate:location"],
        fallback="first",
    )
    second = _family_key(
        ["task:reply_confirm_current_state", "family:item_location", "predicate:state"],
        fallback="second",
    )
    assert first == second == "family:item_location"


def test_pilot_builder_rejects_sealed_family_overlap(
    tmp_path: Path, golden_scenarios: list[dict]
) -> None:
    grounded = _grounded_rows(golden_scenarios)
    sealed = _sealed_rows()
    sealed[0]["split_anchors"] = [grounded[0]["scenario"]["split_anchors"][1]]
    with pytest.raises(PilotPackageError, match="改写家族泄漏"):
        build_pilot_package(
            grounded_candidates=grounded,
            static_records=_static_rows_and_audits()[0],
            static_reaudit=_static_rows_and_audits()[1],
            sealed_rows=sealed,
            output_dir=tmp_path / "family-overlap",
        )


@pytest.mark.parametrize("kind", ["grounded", "static", "sealed"])
def test_pilot_builder_rejects_incomplete_inputs(
    tmp_path: Path, golden_scenarios: list[dict], kind: str
) -> None:
    grounded = _grounded_rows(golden_scenarios)
    static, audits = _static_rows_and_audits()
    sealed = _sealed_rows()
    if kind == "grounded":
        grounded = grounded[:-1]
    elif kind == "static":
        static = static[:-1]
        audits = audits[:-1]
    else:
        sealed = sealed[:-1]
    with pytest.raises(PilotPackageError):
        build_pilot_package(
            grounded_candidates=grounded,
            static_records=static,
            static_reaudit=audits,
            sealed_rows=sealed,
            output_dir=tmp_path / kind,
        )


def test_pilot_builder_rejects_old_correction_and_unreviewed_static(
    tmp_path: Path, golden_scenarios: list[dict]
) -> None:
    static, audits = _static_rows_and_audits()
    static[0]["task_type"] = "reply_correction"
    with pytest.raises(PilotPackageError, match="reply_correction"):
        build_pilot_package(
            grounded_candidates=_grounded_rows(golden_scenarios),
            static_records=static,
            static_reaudit=audits,
            sealed_rows=_sealed_rows(),
            output_dir=tmp_path / "correction",
        )
    static, audits = _static_rows_and_audits()
    audits[0]["approved"] = False
    with pytest.raises(PilotPackageError, match="复审"):
        build_pilot_package(
            grounded_candidates=_grounded_rows(golden_scenarios),
            static_records=static,
            static_reaudit=audits,
            sealed_rows=_sealed_rows(),
            output_dir=tmp_path / "unreviewed",
        )


def test_pilot_builder_rejects_gold_scenario_in_grounded_input(
    tmp_path: Path, golden_scenarios: list[dict]
) -> None:
    grounded = _grounded_rows(golden_scenarios)
    grounded[0]["scenario"] = copy.deepcopy(golden_scenarios[0])
    with pytest.raises(PilotPackageError, match="黄金场景"):
        build_pilot_package(
            grounded_candidates=grounded,
            static_records=_static_rows_and_audits()[0],
            static_reaudit=_static_rows_and_audits()[1],
            sealed_rows=_sealed_rows(),
            output_dir=tmp_path / "gold",
        )
