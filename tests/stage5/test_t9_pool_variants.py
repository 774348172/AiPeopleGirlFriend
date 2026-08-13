"""T9：打破池循环复用（P1-2，2026-08-06）。

覆盖：
- 同条目第 2+ 次复用时 player_view 注入情境变体（输入分化）；
- 未配置 pool_variants 的 profile 退化为原行为（不注入）；
- 变体注入确定性（同 seed 可复现）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from gen_qin_v4 import PROFILES_ROOT, QWX_PACKAGE_SET, ROOT as GEN_ROOT  # noqa: E402


def _compile():
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    factory = RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "qinweixi" / "pools.yaml"))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    return compiler.compile(RunSpec(run_id="t9-check", seed=42), QWX_PACKAGE_SET).plan


def test_pool_reuse_injects_variant_into_player_view():
    plan = _compile()
    items = [i for i in plan.items if i.task_type == "reply_casual"]
    # 同一 topic 的首次与第二次复用：player_view 应不同（第二次带变体）
    topic0 = items[0].input["topic"]
    first_view = items[0].input["player_view"]
    second = next(
        (i for i in items[1:] if i.input["topic"] == topic0), None
    )
    assert second is not None
    assert first_view != second.input["player_view"]
    assert second.input["player_view"].startswith(first_view.rstrip("。"))
    # 变体来自 profile.pool_variants
    registry = FilePackageRegistry(PROFILES_ROOT)
    profile = registry.get("profile.qinweixi")
    variants = profile["pool_variants"]
    assert any(v in second.input["player_view"] for v in variants)


def test_reuse_variants_cycle_deterministically():
    plan1 = _compile()
    plan2 = _compile()
    views1 = [i.input["player_view"] for i in plan1.items]
    views2 = [i.input["player_view"] for i in plan2.items]
    assert views1 == views2


def test_no_variants_profile_falls_back_to_original_behavior():
    """未配置 pool_variants 时：循环复用不注入（通用机制退化兼容）。"""
    from tests.core.fixtures import profile_alpha, package_set
    from tests.core.fixtures import FakeItemFactory  # noqa: F401  (编译走 fake 工厂)

    # 直接构造一个无 variants 的简易池编译
    factory = RecipeDrivenItemFactory()  # 无 pools_path
    recipe = {
        "strata": [
            {
                "task_type": "casual",
                "mode_id": "REPLY",
                "target_count": 5,
                "evidence_state_distribution": {"supported": 1.0},
                "policy_distribution": {"answer": 1.0},
            }
        ],
        "topic_pools": {
            "casual": [
                {"topic": "t1", "scene": "客厅", "player_view": "你在家"},
            ]
        },
    }
    items = factory.build_items(
        profile={"profile_id": "no-variants", "style_contract": "plain"},
        recipe=recipe,
        protocol={},
        seed=42,
    )
    views = [i.input["player_view"] for i in items]
    assert views == ["你在家"] * 5  # 无变体池 → 完全复用
