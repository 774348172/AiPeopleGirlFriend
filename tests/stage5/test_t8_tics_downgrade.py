"""T8：口癖降频（P1-1，D3=30%，2026-08-06）。

覆盖：
- _tics_enabled 确定性：~30% 条目注入；insufficient/false_premise/conflicted 强制克制；
- 克制版风格文本不含 tics/catchphrases 清单、含"口吻克制"要求；
- 集成：克制 item 的生成 prompt 不含口头禅清单。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.adapters.modes.reply import _tics_enabled  # noqa: E402
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from gen_qin_v4 import (  # noqa: E402
    PROFILES_ROOT,
    QWX_PACKAGE_SET,
    ROOT as GEN_ROOT,
    qin_style_resolver,
)
from tests.adapters.conftest import REPLY_OK_SCRIPT  # noqa: E402
from tests.adapters.test_reply_adapter import _run  # noqa: E402


@pytest.fixture(scope="module")
def qin_plan_items():
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    factory = RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "qinweixi" / "pools.yaml"))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    plan = compiler.compile(RunSpec(run_id="t8-check", seed=42), QWX_PACKAGE_SET).plan
    return [item.to_dict() for item in plan.items]


def test_tics_injection_ratio_about_30_percent(qin_plan_items):
    # 统计口径：分母 = 可注入样本（restrained evidence 强制不注入，不计入分母）
    eligible = [
        item for item in qin_plan_items
        if item.get("evidence_state") not in ("insufficient", "false_premise", "conflicted")
    ]
    enabled = sum(1 for item in eligible if _tics_enabled(item))
    ratio = enabled / len(eligible)
    assert 0.25 <= ratio <= 0.35, f"注入比例 {ratio:.3f} 偏离 30%"


def test_tics_injection_is_deterministic(qin_plan_items):
    assert [_tics_enabled(i) for i in qin_plan_items] == [_tics_enabled(i) for i in qin_plan_items]


def test_restrained_evidence_states_force_no_tics(qin_plan_items):
    for item in qin_plan_items:
        if item.get("evidence_state") in ("insufficient", "false_premise", "conflicted"):
            assert _tics_enabled(item) is False


def test_resolver_restrained_version_has_no_catchphrases():
    restrained = qin_style_resolver("style:qinweixi-casual-v1", tics_enabled=False)
    assert "口吻克制" in restrained
    assert "谁稀罕" not in restrained
    assert "B哥你够了" not in restrained
    assert "口头禅：" not in restrained
    full = qin_style_resolver("style:qinweixi-casual-v1", tics_enabled=True)
    assert "口头禅：" in full
    assert "谁稀罕" in full


def test_integration_restrained_prompt_via_style_contract(package_context):
    from data_gen_v4.adapters.modes.reply import _style_contract

    profile = dict(package_context["profile"], style_contract="style:qinweixi-casual-v1")
    restrained = _style_contract(profile, qin_style_resolver, tics_enabled=False)
    assert "口吻克制" in restrained
    assert "谁稀罕" not in restrained
    assert "口头禅：" not in restrained
    full = _style_contract(profile, qin_style_resolver, tics_enabled=True)
    assert "口头禅：" in full
