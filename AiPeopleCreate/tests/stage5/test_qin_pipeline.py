"""秦未晞生产管线 mock 验证（阶段 5 第 4 步）：正典就绪门端到端闭合。

用 StrictTestModelAdapter 的循环变体跑通 编译 → 生成 → 门 → 导出，
不调用真实模型（第 5 步再接 OpenAICompat）。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.sources.registry import (
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.gates import ReleaseQualityGate
from data_gen_v4.core.plan import PackageSetV4, RunSpec
from data_gen_v4.core.sink import AppendSink

# 秦未晞历史正典（2026-08-08 迁出：现行正典为白未晞；本目录为生成器仓库历史副本）
ROOT = Path(__file__).resolve().parents[2]
PROFILES_ROOT = ROOT / "profiles"
QWX_SOURCES = ROOT / "历史与调研文档" / "历史角色" / "秦未晞"
POOLS_PATH = PROFILES_ROOT / "qinweixi" / "pools.yaml"

QWX_PACKAGE_SET = PackageSetV4(
    profile_package_ref="pkg:profile.qinweixi@1.2.0",
    protocol_bundle_ref="pkg:protocol.relationship.runtime.v1@1.2.0",
    dataset_recipe_ref="pkg:recipe.qinweixi.v2.3@2.5.0",
    release_policy_ref="pkg:release.qinweixi.v2.3@2.5.0",
)

# 循环单响应：semantic+style 合并输出（2026-08-05：每 item 1 次调用）
LOOP_SCRIPT = [
    {
        "content": (
            '{"human_turns": ["你今晚怎么回来这么晚？"], '
            '"assistant_propositions": ["角色名叫秦未晞。"], '
            '"assistant_tones": ["neutral"], '
            '"messages": ['
            '{"role": "human", "text": "你今晚怎么回来这么晚？"}, '
            '{"role": "assistant", "text": "加班啊。角色名叫秦未晞，你说呢。"}, '
            '{"role": "human", "text": "那你吃饭了吗？"}, '
            '{"role": "assistant", "text": "点了外卖，还没到。"}]}'
        )
    },
]


class LoopingModelAdapter:
    """测试用：script 循环响应（超出不报错）。产物不进入 release 路径。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.calls: list[dict] = []

    def generate(self, spec: dict) -> dict:
        self.calls.append(spec)
        return self.script[(len(self.calls) - 1) % len(self.script)]


class LoopingPool:
    def __init__(self, adapter: LoopingModelAdapter) -> None:
        self.adapter = adapter

    def resolve(self, spec: dict) -> LoopingModelAdapter:
        return self.adapter


def qin_style_resolver(style_ref: str, tics_enabled: bool = True) -> str:
    """把 style:qinweixi-casual-v1 解析为 bible voice 段的合成口吻文本。"""
    if style_ref != "style:qinweixi-casual-v1":
        return style_ref
    bible = yaml.safe_load((QWX_SOURCES / "bible.yaml").read_text(encoding="utf-8"))
    v = bible["voice"]
    p = bible.get("player", {})
    nickname_rule = (
        "对玩家的称呼：平常叫'B哥'，心情好的时候才叫他的名字'浩然'。"
        if p
        else ""
    )
    return (
        f"第一人称。{v['register']}。常用词：{v['vocabulary']}。"
        f"句长：{v['sentence_length']}。标点：{v['punctuation']}。"
        f"口头禅：{'、'.join(v.get('catchphrases', []))}。"
        f"emoji：{'允许' if v.get('emoji') else '禁止'}；"
        f"markdown：{'允许' if v.get('markdown') else '禁止'}；"
        f"严禁助手腔：{'、'.join(v.get('forbid_assistant_speak', []))} 等。"
        f"{nickname_rule}"
    )


@pytest.fixture()
def qin_compiler():
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(ROOT)  # sources ref 相对项目根（包作者约定）
    factory = RecipeDrivenItemFactory(pools_path=str(POOLS_PATH))
    return GenerationPlanCompiler(registry, loader, factory)


def test_qin_compile_produces_2500_items(qin_compiler):
    result = qin_compiler.compile(RunSpec(run_id="qin-mock-compile", seed=42), QWX_PACKAGE_SET)
    plan = result.plan
    assert plan.profile_id == "qinweixi"
    assert len(plan.items) == 2674  # 2500 + T12 矩阵 126 + canon_qa 2 + general 24 + safety 20（2026-08-07）
    from collections import Counter

    tasks = Counter(i.task_type for i in plan.items)
    assert tasks == {
        "reply_casual": 1200, "reply_romance": 768, "reply_identity": 30,
        "reply_emotion": 442, "reply_protective": 60,
        "reply_supportive": 48, "reply_correction": 15, "reply_vague": 18,
        "reply_quiet_company": 12, "reply_boundary": 12, "reply_canon_qa": 21,
        "reply_general": 24, "reply_safety": 24,
    }
    # 对照成对：protective 30 个 family 各含 positive_control + boundary_variant
    prot_families = Counter(i.family_id for i in plan.items if i.task_type == "reply_protective")
    assert len(prot_families) == 30
    assert all(count == 2 for count in prot_families.values())
    # 秘密类 refusal_required 计数（真正拒答统计依据）
    refusals = sum(1 for i in plan.items if i.refusal_required)
    assert refusals >= 20  # recipe refusal_bounds.true_refusal_min=20
    # lock 自洽 + 默认协议包被引用
    assert plan.package_lock_hash == result.lock["lock_hash"]
    assert plan.protocol_bundle_id == "relationship-runtime-v1"


def test_qin_mock_pipeline_end_to_end(qin_compiler, tmp_path):
    """子集 plan（2 items）跑通 生成→门→导出（mock 不花钱）。"""
    result = qin_compiler.compile(RunSpec(run_id="qin-mock-run", seed=42), QWX_PACKAGE_SET)
    subset = replace(
        result.plan,
        items=[result.plan.items[0], result.plan.items[100]],  # casual + romance 各 1
    )
    model = LoopingModelAdapter(LOOP_SCRIPT)
    pool = LoopingPool(model)
    engine = GenerationEngineV4(
        {"REPLY": ReplyModeAdapter(style_contract_resolver=qin_style_resolver)},
        package_context=result.context,
    )
    with AppendSink.open(tmp_path / "qin-mock.sqlite") as sink:
        run = engine.execute(subset, result.lock, pool, sink)
        assert run.completed == 2
        progress = sink.read_progress("qin-mock-run")
        # 大块 B（阶段 2 P0-4）：真实 recipe candidate_count=2 → 每 item 2 候选
        assert len(progress.candidates) == 4
        assert len(progress.completed_candidate_keys) == 4
        assert all(c.provenance["calls"] for c in progress.candidates)  # calls 回链非空

        # 门：G0-G4 全部通过——G3/G4 为阶段 1 真实门（G3：evidence-required 候选
        # 已由编译期证据填充锚定 profile 快照 / not_required 装饰性放行；G4：协议
        # 包在 lock）。块 1.2 起证据非空由 G2 强制。
        snapshots_by_id = {s["snapshot_id"]: s for s in result.context["snapshots"].values()}
        gate = ReleaseQualityGate(release_policy=result.context["release"])
        for candidate in progress.candidates:
            data = candidate.to_dict()
            results = gate.evaluate(
                data,
                {
                    "lock": result.lock,
                    "snapshots": snapshots_by_id,
                    "protocol": result.context["protocol"],
                },
            )
            by_id = {r.gate_id: r for r in results}
            assert by_id["G0"].decision == "approved"
            assert by_id["G1"].decision == "approved"
            assert by_id["G2"].decision == "approved"
            assert by_id["G3"].decision == "approved"
            assert by_id["G4"].decision == "approved"

        # 导出：TrainingRecord → ShareGPT 行
        from data_gen_v4.adapters.exporters.export import ShareGPTReplyExportAdapter

        exporter = ShareGPTReplyExportAdapter()
        adapter = ReplyModeAdapter(style_contract_resolver=qin_style_resolver)
        for candidate in progress.candidates:
            training = adapter.render_training(candidate.to_dict(), result.context)
            assert training.system_anchor and "秦未晞" in training.system_anchor
            line = exporter.render(training)
            assert exporter.validate_roundtrip(line) == [{"ok": True}]


def test_qin_style_resolver_produces_voice_text():
    text = qin_style_resolver("style:qinweixi-casual-v1")
    assert "第一人称" in text
    assert "北京式口语" in text
    assert "禁止" in text  # emoji/markdown 禁止
    # 全局称呼设定（2026-08-05）：心情好叫"浩然"，平常叫"B哥"
    assert "B哥" in text and "浩然" in text


def test_qin_sources_visibility_three_tiers(qin_compiler):
    """正典就绪门：三来源加载 + 可见性三层（public/private/secret）。"""
    result = qin_compiler.compile(RunSpec(run_id="qin-vis", seed=1), QWX_PACKAGE_SET)
    snapshots = result.context["snapshots"]
    from collections import Counter

    all_vis: Counter = Counter()
    for snapshot in snapshots.values():
        for unit in snapshot["units"]:
            all_vis[unit["visibility_scope"]] += 1
    assert all_vis["profile_public"] > 0
    assert all_vis["profile_secret"] > 0  # 异世界/记忆事件
    # facts 注入身份事实（含年龄 22/24 口径）
    assert any("秦未晞" in f for f in result.context["facts"])
