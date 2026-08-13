"""阶段 3 C-2（安全知识进 ProtocolBundle）验收。

- 规则表从协议包读取（core 不再内置）；协议无规则 → G8 放行（与 G5 词表同语义）；
- 生产协议（relationship-runtime-v1 1.1.0）携带 10 场景规则 → G8 真实拦截。
"""
from __future__ import annotations

import yaml

from data_gen_v4.core.gates import safety_action_checker


def _protocol_with_rules() -> dict:
    return {
        "safety_action_rules": {
            "煤气": {"required": ["开窗", "关阀", "出去"], "forbidden": ["开灯", "点火"]}
        },
        "safety_topic_keys": [["煤气", "煤气"]],
        "safety_attitude_forbidden": ["没空"],
    }


def _candidate(text: str, topic: str = "家里煤气好像漏了") -> dict:
    return {
        "input": {"topic": topic},
        "target": {
            "messages": [
                {"role": "human", "content": "h"},
                {"role": "assistant", "content": text},
            ]
        },
    }


def test_g8_reads_rules_from_protocol():
    checker = safety_action_checker()
    # 正确处置（开窗+关阀，无错误动作）→ 通过
    reasons = checker(_candidate("快开窗通风，然后关掉阀门出去。"), {"protocol": _protocol_with_rules()})
    assert reasons == []
    # 缺必需动作 → 拒绝
    reasons = checker(_candidate("别担心，没事的。"), {"protocol": _protocol_with_rules()})
    assert any("safety_action_missing" in r for r in reasons)
    # 错误处置 → 拒绝
    reasons = checker(_candidate("先开灯看看，再点火试试。"), {"protocol": _protocol_with_rules()})
    assert any("safety_action_forbidden" in r for r in reasons)
    # 态度拒绝 → 拒绝
    reasons = checker(_candidate("没空，你自己去。"), {"protocol": _protocol_with_rules()})
    assert any("safety_attitude_forbidden" in r for r in reasons)


def test_g8_passes_when_protocol_has_no_rules():
    """协议未声明安全规则 → 放行（配置了才拦，与 G5 词表同语义）。"""
    checker = safety_action_checker()
    reasons = checker(_candidate("随便说点什么。"), {"protocol": {}})
    assert reasons == []


def test_g8_ignores_non_safety_topic():
    checker = safety_action_checker()
    reasons = checker(
        _candidate("今天天气不错。", topic="周末干什么"),
        {"protocol": _protocol_with_rules()},
    )
    assert reasons == []


def test_production_bundle_carries_safety_rules():
    """生产协议包必须携带安全规则（gen_qin_v4 注入时校验）。"""
    path = "data_gen_v4/packages/protocols/relationship-runtime-v1.yaml"
    doc = yaml.safe_load(open(path, encoding="utf-8"))
    assert doc["package_version"] == "1.2.0"
    rules = doc.get("safety_action_rules") or {}
    assert len(rules) >= 10, "生产协议必须携带 10 场景安全规则"
    assert all({"required", "forbidden"} <= set(v) for v in rules.values())
    assert doc.get("safety_topic_keys") and doc.get("safety_attitude_forbidden")
