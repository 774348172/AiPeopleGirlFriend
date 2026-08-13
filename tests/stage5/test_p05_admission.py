"""P0-5 FREEZE-02 admission（块 1.3）验收：未冻结 mode 被 AdmissionBlocked。

G1 验收用例之一（施工总计划_v2 §8.1）："冻结未过被 AdmissionBlocked"——
DatasetGenerator Seam 在生成前按冻结合同检查 mode；未冻结 mode（MEMORY_PROPOSE
等 background_structured / memory_reranker family）不产出数据。
"""
from __future__ import annotations

import pytest

from data_gen_v4.core.admission import AdmissionBlocked, Freeze02Admission
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.dataset_generator import AdmissionBlockedError, DatasetGenerator
from data_gen_v4.core.plan import RunSpec
from tests.core.fixtures import (
    FakeItemFactory,
    FakeSourceLoader,
    alpha_snapshots,
    default_registry,
    package_set,
)

FREEZE02_FIXTURE = {
    "contract_id": "freeze02-training-boundary-v1",
    "status": "frozen",
    "dataset_families": [
        {
            "family": "visible_reply",
            "allowed_modes": ["REPLY", "PROACTIVE_REPLY"],
            "first_candidate_modes": ["REPLY"],
            "data_admission": "allowed_after_dataset_freeze",
        },
        {
            "family": "background_structured",
            "allowed_modes": [
                "MEMORY_PROPOSE",
                "SELF_TIMELINE_PROPOSE",
                "OFFSCREEN_UPDATE",
                "MOTIVE_EVALUATE",
            ],
            "first_candidate_modes": [],
            "data_admission": "blocked_until_mode_schemas_frozen",
        },
        {
            "family": "memory_reranker",
            "allowed_modes": ["MEMORY_RERANK"],
            "first_candidate_modes": [],
            "data_admission": "blocked_until_select01",
        },
    ],
    "next_checkpoint": "FREEZE-03",
}


def _admission() -> Freeze02Admission:
    return Freeze02Admission(contract=FREEZE02_FIXTURE)


def _compiler() -> GenerationPlanCompiler:
    return GenerationPlanCompiler(
        default_registry(), FakeSourceLoader(alpha_snapshots()), FakeItemFactory()
    )


# ───────────────────────── admission 单元（G1 验收） ─────────────────────────

def test_admission_allows_reply_first_batch():
    assert _admission().admit("REPLY") is None


def test_admission_blocks_memory_propose():
    blocked = _admission().admit("MEMORY_PROPOSE")
    assert blocked is not None
    assert isinstance(blocked, AdmissionBlocked)
    assert "blocked:blocked_until_mode_schemas_frozen" in blocked.reason_codes
    assert blocked.contract_id == "freeze02-training-boundary-v1"
    assert blocked.next_checkpoint == "FREEZE-03"
    assert blocked.contract_ref == "<inline>"


def test_admission_blocks_proactive_reply_in_first_batch():
    blocked = _admission().admit("PROACTIVE_REPLY", first_candidate_batch=True)
    assert blocked is not None
    assert "not_first_candidate_mode" in blocked.reason_codes
    # 首个 ~1000 条工程候选之后（数据集冻结后可生产）
    assert _admission().admit("PROACTIVE_REPLY", first_candidate_batch=False) is None


def test_admission_blocks_unknown_mode():
    blocked = _admission().admit("MYSTERY_MODE")
    assert blocked is not None
    assert "mode_not_in_any_family" in blocked.reason_codes


# ───────────────────────── DatasetGenerator Seam（build 预检） ─────────────────────────

def test_build_passes_when_all_modes_allowed():
    generator = DatasetGenerator(_compiler(), admission=_admission())
    result = generator.build(RunSpec(run_id="p05-ok", seed=42), package_set())
    assert len(result.plan.items) == 3


class _AlwaysBlockingAdmission:
    def admit(self, mode, **kwargs):
        return AdmissionBlocked(
            mode=mode,
            reason_codes=["test_blocked"],
            contract_id="c",
            contract_ref="<test>",
            next_checkpoint="FREEZE-03",
        )


def test_build_blocks_unfrozen_mode():
    generator = DatasetGenerator(_compiler(), admission=_AlwaysBlockingAdmission())
    with pytest.raises(AdmissionBlockedError) as exc_info:
        generator.build(RunSpec(run_id="p05-blocked", seed=42), package_set())
    assert exc_info.value.blocked
    assert exc_info.value.blocked[0].mode == "REPLY"
    assert "FREEZE-02" in str(exc_info.value)
    assert exc_info.value.to_records()[0]["next_checkpoint"] == "FREEZE-03"


def test_build_admits_only_selected_items():
    generator = DatasetGenerator(_compiler(), admission=_AlwaysBlockingAdmission())
    result = generator.build(
        RunSpec(run_id="p05-empty-selection", seed=42),
        package_set(),
        item_selector=lambda plan: [],
    )
    assert result.plan.items == []


def test_generator_requires_contract_or_path():
    with pytest.raises(ValueError):
        Freeze02Admission()


def test_real_freeze02_contract_allows_reply():
    """真实 FREEZE-02 v2 契约（AI 程序侧）加载并验证准入（契约缺失时跳过）。"""
    from pathlib import Path

    contract_path = Path(r"F:\AiPeople\eval\training_contract\freeze02_contract_v2.json")
    if not contract_path.exists():
        pytest.skip("AI 程序侧契约不存在（独立环境）")
    admission = Freeze02Admission(contract_path=str(contract_path))
    assert admission.admit("REPLY") is None
    blocked = admission.admit("MEMORY_PROPOSE")
    assert blocked is not None
    assert "blocked:blocked_until_mode_schemas_frozen" in blocked.reason_codes
    assert blocked.next_checkpoint == "FREEZE-03"
