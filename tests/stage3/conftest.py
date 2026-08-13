"""tests/stage3 共享 fixtures：文件系统 profile 的编译/生成 helper。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.models.pool import ModelPool, StrictTestModelAdapter
from data_gen_v4.adapters.sources.registry import (
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.plan import PackageSetV4, RunSpec

PROFILES_ROOT = Path(__file__).resolve().parent / "fixtures" / "profiles"

# Alpha 脚本（4 items × 1 调用，2026-08-05 合并）；语义命题必须来自 Alpha 事实
ALPHA_SCRIPT = [
    {
        "content": (
            '{"human_turns": ["你今晚怎么回来这么晚？"], '
            '"assistant_propositions": ["角色名叫阿尔法，住在河畔公寓。"], '
            '"assistant_tones": ["neutral"], '
            '"messages": ['
            '{"role": "human", "text": "你今晚怎么回来这么晚？"}, '
            '{"role": "assistant", "text": "加班啊。角色名叫阿尔法，住在河畔公寓，你说呢。"}, '
            '{"role": "human", "text": "那你吃饭了吗？"}, '
            '{"role": "assistant", "text": "点了外卖，还没到。"}]}'
        )
    },
]

# Beta 脚本（2 items × 1 调用）；第三人称正式，语义命题来自 Beta 事实（Markdown canon）
BETA_SCRIPT = [
    {
        "content": (
            '{"human_turns": ["请问借阅参考书有什么规定？"], '
            '"assistant_propositions": ["市立图书馆收藏了十万册图书。"], '
            '"assistant_tones": ["neutral"], '
            '"messages": ['
            '{"role": "human", "text": "请问借阅参考书有什么规定？"}, '
            '{"role": "assistant", "text": "市立图书馆收藏了十万册图书，借阅规则如下。"}, '
            '{"role": "human", "text": "那闭馆日能来吗？"}, '
            '{"role": "assistant", "text": "图书馆每周二闭馆。"}]}'
        )
    },
]


def package_set_for(profile_id: str) -> PackageSetV4:
    return PackageSetV4(
        profile_package_ref=f"pkg:profile.fixture.{profile_id}@1.0.0",
        protocol_bundle_ref="pkg:protocol.fixture.rel@1.0.0",
        dataset_recipe_ref=f"pkg:recipe.fixture.{profile_id}@1.0.0",
        release_policy_ref=f"pkg:release.fixture.{profile_id}@1.0.0",
    )


def build_compiler(profile_id: str, profiles_root: Path = PROFILES_ROOT):
    registry = FilePackageRegistry(profiles_root)
    # source ref 形如 "file:sources/identity.yaml"（相对 profile 目录）
    loader = CompositeSourceLoader(profiles_root / f"fixture.{profile_id}")
    return GenerationPlanCompiler(registry, loader, RecipeDrivenItemFactory())


def run_pipeline(profile_id: str, script: list[dict], tmp_path, *, run_id: str | None = None):
    """编译 → 生成 → 返回 (result, progress, sink_path)。"""
    compiler = build_compiler(profile_id)
    run_id = run_id or f"run-{profile_id}"
    result = compiler.compile(RunSpec(run_id=run_id, seed=7), package_set_for(profile_id))
    model = StrictTestModelAdapter(script * len(result.plan.items))
    pool = ModelPool(adapters={"strict": model})
    pool.default_id = "strict"
    engine = GenerationEngineV4(
        {"REPLY": ReplyModeAdapter()}, package_context=result.context
    )
    from data_gen_v4.core.sink import AppendSink

    with AppendSink.open(tmp_path / f"{run_id}.sqlite") as sink:
        run = engine.execute(result.plan, result.lock, pool, sink)
        progress = sink.read_progress(run_id)
    return result, progress, run
