"""生产 prompt 顺序与监督规则（§10.5 renderer 合同）。"""
from __future__ import annotations

from data_gen_v4.adapters.modes.renderers import ProductionRenderers
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.modes.structured_protocol import StructuredProtocolModeAdapter
from tests.adapters.conftest import REPLY_OK_SCRIPT
from tests.adapters.test_reply_adapter import _FakeCallExecutor, _run
from tests.core.fixtures import profile_alpha


def test_reply_system_anchor_order_identity_then_protocol(package_context):
    """身份锚 → 行为规则/输出协议（T2 锚统一：散文式，身份在前、协议在后）。"""
    anchor = ProductionRenderers.reply_system_anchor(
        package_context["profile"], package_context.get("anchor_facts") or {}
    )
    identity_pos = anchor.find("你是 fixture.alpha")
    protocol_pos = anchor.find("不要自称AI")
    assert 0 <= identity_pos < protocol_pos
    assert "emoji" in anchor
    assert "风格合同" not in anchor  # T2：风格合同不进锚（口癖只从对话数据学）


def test_reply_training_record_uses_production_anchor(reply_item, package_context):
    payload, _ = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    candidate = dict(payload, sample_id="s1", mode="REPLY")
    record = ReplyModeAdapter().render_training(candidate, package_context)
    # system anchor 生产化：身份锚 + 输出协议，不再为空
    assert record.system_anchor is not None
    assert "你是 fixture.alpha" in record.system_anchor
    assert "不要自称AI" in record.system_anchor
    # 监督全部 assistant（§10.5 静态对话）
    assert record.supervised_message_indexes == [1, 3]
    assert record.protocol_snapshot_id  # 绑定协议版本


def test_reply_training_supervised_indexes_match_assistant_positions(reply_item, package_context):
    payload, _ = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    candidate = dict(payload, sample_id="s1", mode="REPLY")
    record = ReplyModeAdapter().render_training(candidate, package_context)
    roles = [m["role"] for m in record.messages]
    assert [roles[i] for i in record.supervised_message_indexes] == ["assistant", "assistant"]


def test_protocol_training_record_order_discriminator_input_target():
    """协议训练记录顺序：human=输入，assistant=JSON target；只监督 JSON 条。"""
    adapter = StructuredProtocolModeAdapter()
    candidate = {
        "sample_id": "s1", "mode": "RECALL_PLAN",
        "input": {"mode_system_input": "mode=RECALL_PLAN", "visible_cues": [], "current_input": "q"},
        "target": {"action": "NO_OP", "reason": "r"},
        "prompt_hash": "sha256:" + "a" * 64,
    }
    record = adapter.render_training(candidate, {})
    assert record.messages[0]["role"] == "human"
    assert record.messages[1]["role"] == "assistant"
    assert record.supervised_message_indexes == [1]
    assert '"action": "NO_OP"' in record.messages[1]["content"]


def test_protocol_system_anchor_has_discriminator():
    recall = ProductionRenderers.protocol_system_anchor("RECALL_PLAN")
    assert "mode=RECALL_PLAN" in recall
    assert "不生成回忆答案" in recall
    propose = ProductionRenderers.protocol_system_anchor("MEMORY_PROPOSE")
    assert "mode=MEMORY_PROPOSE" in propose
    assert "不含计划建立" in propose


def test_protocol_anchor_missing_mode_has_generic_fallback():
    anchor = ProductionRenderers.protocol_system_anchor("UNKNOWN_MODE")
    assert "mode=UNKNOWN_MODE" in anchor
