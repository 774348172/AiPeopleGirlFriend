from __future__ import annotations

from tools.build_v5_pilot_dataset import (
    SEALED_CONCEPTS,
    TRAIN_CONCEPTS,
    build_scenarios,
    build_sealed_rows,
)
from data_gen_v4.runtime_grounded.adapter import _surface_obligations


def test_sealed_reference_replies_preserve_surface_semantics() -> None:
    scenarios = build_scenarios(SEALED_CONCEPTS, sealed=True)
    rows = {row["scenario_id"]: row for row in build_sealed_rows(scenarios)}

    location = rows["sealed.direct.sealed.glasses.location"]["training_record"]["messages"][-1]["content"]
    subject = rows["sealed.subject.sealed.glasses.location"]["training_record"]["messages"][-1]["content"]

    assert "在窗边矮柜" in location
    assert "朋友" not in subject
    assert "赵医生" in subject
    assert "眼镜在窗边矮柜" in subject


def test_subject_location_scenario_requires_person_and_natural_location_fact() -> None:
    scenarios = build_scenarios(TRAIN_CONCEPTS, sealed=False)
    scenario = next(
        row for row in scenarios if row["scenario_id"] == "pilot.subject.charger.location"
    )

    assertion = scenario["oracle_view"]["required_assertions"][0]
    obligations = _surface_obligations(scenario)

    assert assertion["subject"] == "phone_charger"
    assert assertion["predicate"] == "location"
    assert assertion["object"] == "沙发旁插座"
    assert "程野" in obligations
    assert "手机充电器在沙发旁插座" in obligations

    state_scenario = next(
        row for row in scenarios if row["scenario_id"] == "pilot.subject.lamp.state"
    )
    assert state_scenario["oracle_view"]["required_assertions"][0]["object"] == "已关闭"
