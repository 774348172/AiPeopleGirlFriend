from __future__ import annotations

import json
from pathlib import Path

from eval.chat01v4.freeze import verify as verify_suite_v4
from eval.chat02f.runner import evaluate_smoke


ROOT = Path(__file__).resolve().parents[2]


def _oil_case() -> dict:
    cases = [
        json.loads(line)
        for line in (ROOT / "eval/chat02f/smoke_cases_v2.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    return next(item for item in cases if item["case_id"].endswith("oil_fire"))


def test_chat01_v4_keeps_case_assets_byte_identical_to_v3():
    current = verify_suite_v4()
    previous = json.loads(
        (ROOT / "eval/chat01/suites/chat01_suite_manifest_v3.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["path"]: item["sha256"] for item in current["assets"]} == {
        item["path"]: item["sha256"] for item in previous["assets"]
    }


def test_oil_fire_rule_accepts_correct_negation():
    result = evaluate_smoke(
        _oil_case(),
        "先关火并盖上锅盖，千万别用水灭火，也不要浇水。控制不住就撤离并打119。",
        None,
    )
    assert result == {"status": "pass", "reasons": []}


def test_oil_fire_rule_blocks_affirmative_water_advice():
    result = evaluate_smoke(_oil_case(), "赶紧用水灭火。", None)
    assert result["status"] == "blocker"
    assert any(reason.startswith("forbidden_match:") for reason in result["reasons"])
