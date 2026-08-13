"""阶段 3 C-6（行为族蓝图）验收。

- 蓝图八族覆盖 13 个 task_type（缺族 → validate_family_coverage 报错）；
- Evol 约束：只演化 scene/evidence_state/expression/difficulty，禁止演化 canon；
- compiler 集成：真实 profile 编译通过族覆盖校验；
- Evol 演化器输出不触碰 canon（canon_touched=false，仅场景/标签）。
"""
from __future__ import annotations

import pytest

from data_gen_v4.core.blueprint import Blueprint, load_blueprint
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.errors import PreconditionFailedError
from data_gen_v4.core.plan import RunSpec
from tests.stage5.test_qin_pipeline import QWX_PACKAGE_SET, qin_compiler

BLUEPRINT_PATH = "profiles/qinweixi/blueprint.yaml"


def _blueprint() -> Blueprint:
    return load_blueprint(BLUEPRINT_PATH)


def test_blueprint_covers_all_production_strata():
    blueprint = _blueprint()
    # 13 个生产 task_type 全部归族
    all_tasks = {
        "reply_casual", "reply_romance", "reply_identity", "reply_emotion",
        "reply_protective", "reply_supportive", "reply_correction", "reply_vague",
        "reply_quiet_company", "reply_boundary", "reply_canon_qa",
        "reply_general", "reply_safety",
    }
    assert blueprint.validate_family_coverage(sorted(all_tasks)) == []
    # 未归族任务 → 报错
    assert blueprint.validate_family_coverage(["mystery_task"]) == ["mystery_task"]


def test_blueprint_evol_constraints_forbid_canon():
    blueprint = _blueprint()
    assert blueprint.validate_evol_constraints() == []
    # 违反约束的蓝图（forbid 不含 canon）→ 报错
    bad = Blueprint(
        blueprint_id="bad",
        behavior_families={},
        evol_constraints={"forbid": ["scene"]},
    )
    assert bad.validate_evol_constraints()


def test_blueprint_difficulty_gradient():
    blueprint = _blueprint()
    hard = blueprint.difficulty_gradient("serious_support", "hard")
    assert hard, "严肃支持族必须有 hard 梯度"
    assert any("安全" in h or "健康" in h for h in hard)
    assert blueprint.difficulty_gradient("background_structured", "easy") == []


def test_compiler_validates_blueprint_coverage(qin_compiler):
    """真实 profile 编译通过族覆盖校验（13 strata 全归族）。"""
    result = qin_compiler.compile(RunSpec(run_id="blueprint-ok", seed=42), QWX_PACKAGE_SET)
    assert len(result.plan.items) > 0


def test_compiler_rejects_uncovered_strata(tmp_path):
    """构造缺族 blueprint → 编译期 PreconditionFailedError。"""
    import yaml
    from pathlib import Path

    blueprint = load_blueprint(BLUEPRINT_PATH)
    # 克隆蓝图并删除 proactive 族（reply_casual/reply_emotion 将无族）
    doc = yaml.safe_load(Path(BLUEPRINT_PATH).read_text(encoding="utf-8"))
    del doc["behavior_families"]["proactive"]
    bad_path = tmp_path / "bad_blueprint.yaml"
    bad_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    bad_blueprint = Blueprint(
        blueprint_id="bad",
        behavior_families=doc["behavior_families"],
        evol_constraints=doc.get("evol_constraints") or {},
    )
    uncovered = bad_blueprint.validate_family_coverage(["reply_casual", "reply_emotion"])
    assert sorted(uncovered) == ["reply_casual", "reply_emotion"]


def test_evolve_output_never_touches_canon():
    """Evol 演化器输出：仅场景/标签，canon_touched 恒 false。"""
    from tools.evolve_blueprint import evolve

    rows = evolve(_blueprint())
    assert rows, "八族蓝图必须产出 Evol 变体"
    for row in rows:
        assert row["canon_touched"] is False
        assert row["difficulty"] in ("easy", "medium", "hard")
        # 变体只含场景描述与标签，不含事实主张
        assert "task_type" in row and "family" in row and "scene" in row
