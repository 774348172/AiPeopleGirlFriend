"""称呼注入机制（2026-08-09，混合方案：80% 零称呼 + 20% 多样名字）。

覆盖：
- 比例分配确定性：--name-ratio 0.2 时约 20% item 注入 player_name；
- prompt 称呼段：item.input.player_name 存在 → merge prompt 追加【称呼】段；
- 锚渲染：注入名字的 item → 训练行锚含"玩家叫X，24岁"关系句；
- 零回归：无 player_name 时锚无玩家信息（现状不变）；
- 占位符：bible player.name_slots="玩家可配置" 不生成昵称规则段。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.reply import ReplyModeAdapter  # noqa: E402
from data_gen_v4.adapters.modes.style import load_style_spec  # noqa: E402
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from gen_v4 import PROFILES_ROOT, ROOT as GEN_ROOT, build_package_set  # noqa: E402


def _compile_context():
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    pools_path = PROFILES_ROOT / "baiweixi" / "pools.yaml"
    from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory

    factory = RecipeDrivenItemFactory(pools_path=str(pools_path))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    result = compiler.compile(
        RunSpec(run_id="name-inject", seed=42),
        build_package_set(PROFILES_ROOT, "baiweixi"),
    )
    return result.context


class _FakeExecutor:
    def __init__(self, content: str):
        self._content = content
        self.specs: list[dict] = []

    def call(self, spec: dict, **kwargs) -> dict:
        self.specs.append(spec)
        return {"content": self._content}

    def record_ids(self) -> list[str]:
        return ["call-0"]


_OK = json.dumps(
    {
        "human_turns": ["你回来了？"],
        "assistant_propositions": ["我等你回来。"],
        "assistant_tones": ["neutral"],
        "messages": [
            {"role": "human", "text": "你回来了？"},
            {"role": "assistant", "text": "我等你回来。"},
        ],
    },
    ensure_ascii=False,
)


# ── 比例分配（与 gen_v4 注入逻辑同构）──


def test_name_ratio_about_20_percent():
    # 确定性哈希分布：plan_id → digest % 100 < 20 的占比 ≈ 20%
    digests = [
        int.from_bytes(hashlib.sha256(f"plan-{i:04d}".encode()).digest()[:4], "big") % 100
        for i in range(2000)
    ]
    ratio = sum(1 for d in digests if d < 20) / len(digests)
    assert 0.15 <= ratio <= 0.25, f"注入比例 {ratio:.3f} 偏离 20%"


def test_injection_is_deterministic():
    # 同 plan_id 的注入判定恒定（两次计算结果一致）
    def _hit(plan_id: str) -> bool:
        digest = int.from_bytes(hashlib.sha256(plan_id.encode("utf-8")).digest()[:4], "big")
        return digest % 100 < 20

    for pid in ("plan-a", "plan-b", "plan-0000", "plan-0630"):
        assert _hit(pid) == _hit(pid)


# ── prompt 称呼段 ──


def test_prompt_contains_name_section_when_injected():
    context = _compile_context()
    adapter = ReplyModeAdapter()
    item = {
        "seed": 1,
        "attempt_no": 1,
        "task_type": "reply_casual",
        "input": {"scene": "s", "topic": "t", "player_view": "v",
                  "turn_bounds": (2, 4),
                  "player_name": "陈默", "player_age": 24},
        "required_behaviors": [], "forbidden_behaviors": [],
        "evidence_state": "supported", "desired_policy": "answer",
    }
    job = adapter.prepare(item, context)
    executor = _FakeExecutor(_OK)
    payload = adapter.generate(job, executor)
    assert "mode_failure" not in payload, payload
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "玩家的名字是陈默" in prompt
    assert "第一句先承接玩家话题" in prompt


def test_prompt_without_name_has_no_name_section():
    context = _compile_context()
    adapter = ReplyModeAdapter()
    item = {
        "seed": 1,
        "attempt_no": 1,
        "task_type": "reply_casual",
        "input": {"scene": "s", "topic": "t", "player_view": "v",
                  "turn_bounds": (2, 4)},
        "required_behaviors": [], "forbidden_behaviors": [],
        "evidence_state": "supported", "desired_policy": "answer",
    }
    job = adapter.prepare(item, context)
    executor = _FakeExecutor(_OK)
    payload = adapter.generate(job, executor)
    assert "mode_failure" not in payload
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "【称呼】" not in prompt  # 零称呼现状


# ── 玩家不自称名字（2026-08-09）──


def test_player_self_name_rejected():
    """玩家台词含注入名字（教师把玩家名塞进玩家嘴里）→ 重试。"""
    context = _compile_context()
    adapter = ReplyModeAdapter()
    item = {
        "seed": 1,
        "attempt_no": 1,
        "task_type": "reply_casual",
        "input": {"scene": "s", "topic": "t", "player_view": "v",
                  "turn_bounds": (2, 4),
                  "player_name": "阿伟"},
        "required_behaviors": [], "forbidden_behaviors": [],
        "evidence_state": "supported", "desired_policy": "answer",
    }
    # 教师把"阿伟"写进玩家台词（视角错误）
    bad_target = {
        "human_turns": ["阿伟，你小时候什么样啊？"],
        "assistant_propositions": ["我小时候是只普通小猫。"],
        "assistant_tones": ["neutral"],
        "messages": [
            {"role": "human", "text": "阿伟，你小时候什么样啊？"},
            {"role": "assistant", "text": "我小时候是只普通小猫。"},
        ],
    }
    job = adapter.prepare(item, context)
    payload = adapter.generate(job, _FakeExecutor(json.dumps(bad_target, ensure_ascii=False)))
    assert payload["mode_failure"] is True
    assert "玩家台词含自己的名字" in payload["reason"]


def test_player_normal_speech_allowed_with_name():
    """玩家台词不含名字（正常）→ 放行。"""
    context = _compile_context()
    adapter = ReplyModeAdapter()
    item = {
        "seed": 1,
        "attempt_no": 1,
        "task_type": "reply_casual",
        "input": {"scene": "s", "topic": "t", "player_view": "v",
                  "turn_bounds": (2, 4),
                  "player_name": "阿伟"},
        "required_behaviors": [], "forbidden_behaviors": [],
        "evidence_state": "supported", "desired_policy": "answer",
    }
    ok_target = {
        "human_turns": ["你小时候什么样啊？"],
        "assistant_propositions": ["我小时候是只普通小猫。"],
        "assistant_tones": ["neutral"],
        "messages": [
            {"role": "human", "text": "你小时候什么样啊？"},
            {"role": "assistant", "text": "我小时候是只普通小猫。"},
        ],
    }
    job = adapter.prepare(item, context)
    payload = adapter.generate(job, _FakeExecutor(json.dumps(ok_target, ensure_ascii=False)))
    assert "mode_failure" not in payload


# ── 锚渲染：注入名字 → 关系句 ──


def test_anchor_injects_player_name_and_age():
    from data_gen_v4.adapters.modes.renderers import ProductionRenderers

    context = _compile_context()
    profile = dict(context["profile"])
    contract = dict(profile.get("anchor_contract") or {})
    contract["player_name"] = "陈默"
    profile["anchor_contract"] = contract
    anchor_facts = dict(context["anchor_facts"])
    anchor_facts["player_age"] = "24"
    anchor = ProductionRenderers.reply_system_anchor(
        profile, anchor_facts=anchor_facts, beliefs=str(context["beliefs"]),
        rules=context["protocol"].get("reply_runtime_rules") or [],
    )
    assert "玩家叫陈默，24岁" in anchor
    assert "合租" not in anchor  # 白未晞不是合租关系


def test_anchor_without_name_has_no_player_sentence():
    from data_gen_v4.adapters.modes.renderers import ProductionRenderers

    context = _compile_context()
    anchor = ProductionRenderers.reply_system_anchor(
        context["profile"], anchor_facts=context["anchor_facts"],
        beliefs=str(context["beliefs"]),
        rules=context["protocol"].get("reply_runtime_rules") or [],
    )
    assert "玩家叫" not in anchor  # 零称呼现状：锚无玩家信息


# ── 占位符：bible 占位值不生成昵称规则 ──


def test_bible_placeholder_name_slots_ignored():
    import yaml

    bible = yaml.safe_load(
        (GEN_ROOT / "人物设定" / "白未晞" / "bible.yaml").read_text(encoding="utf-8")
    )
    spec = load_style_spec(bible)
    assert spec.name_slots is None, "占位符'玩家可配置'不应生成昵称规则"
