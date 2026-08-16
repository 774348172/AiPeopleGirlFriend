from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ROOT = ROOT / "eval" / "cross_model_internal_40"


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_internal_cross_model_selection_is_stratified_and_oracle_free() -> None:
    manifest = json.loads(
        (EXPERIMENT_ROOT / "selection_manifest_v1.json").read_text(encoding="utf-8")
    )
    inputs = _load_jsonl(EXPERIMENT_ROOT / "candidate_inputs_v1.jsonl")

    assert manifest["experiment_id"] == "baiweixi-vs-gpt56sol-internal40-v1"
    assert manifest["status"] == "completed"
    assert sum(manifest["selection"]["category_quotas"].values()) == 40
    assert len(inputs) == len({item["case_id"] for item in inputs}) == 40
    assert [item["ordinal"] for item in inputs] == list(range(1, 41))
    assert all("oracle" not in item for item in inputs)
    assert all(set(item) == {
        "ordinal",
        "case_id",
        "category",
        "risk",
        "user_text",
        "max_new_tokens",
    } for item in inputs)


def test_internal_cross_model_outputs_and_adjudication_cover_same_cases() -> None:
    inputs = _load_jsonl(EXPERIMENT_ROOT / "candidate_inputs_v1.jsonl")
    local = _load_jsonl(EXPERIMENT_ROOT / "local_baiweixi_seed42.jsonl")
    gpt = _load_jsonl(EXPERIMENT_ROOT / "gpt5_6_sol_internal.jsonl")
    adjudication = json.loads(
        (EXPERIMENT_ROOT / "manual_adjudication_v1.json").read_text(encoding="utf-8")
    )
    expected = {str(item["case_id"]) for item in inputs}

    assert {str(item["case_id"]) for item in local} == expected
    assert {str(item["case_id"]) for item in gpt} == expected
    assert {str(item["case_id"]) for item in adjudication["decisions"]} == expected
    assert all(item["model"] == "ollama/baiweixi:latest" for item in local)
    assert all(item["model"] == "gpt-5.6-sol" for item in gpt)
    assert all(str(item["response"]).strip() for item in local + gpt)

    local_decisions = Counter(
        str(item["local_decision"]) for item in adjudication["decisions"]
    )
    gpt_decisions = Counter(
        str(item["gpt_decision"]) for item in adjudication["decisions"]
    )
    winners = Counter(str(item["winner"]) for item in adjudication["decisions"])
    assert local_decisions == {"pass": 26, "fail": 14}
    assert gpt_decisions == {"pass": 39, "fail": 1}
    assert winners == {"gpt": 34, "local": 2, "tie": 4}


def test_internal_cross_model_report_preserves_experiment_boundary() -> None:
    report = (EXPERIMENT_ROOT / "report.md").read_text(encoding="utf-8")

    assert "本地白未晞 | GPT-5.6 Sol" in report
    assert "逐题语义通过 | 26/40 | 39/40" in report
    assert "不是 OpenAI API 或 V6 五模式验收" in report
    assert "不包含 V6 世界状态链、结构化五模式、长期记忆或多轮轨迹" in report
