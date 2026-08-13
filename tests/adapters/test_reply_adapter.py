"""ReplyModeAdapter：三步调用、防泄漏、结构与语义检查、训练渲染。"""
from __future__ import annotations

import pytest

from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.models.pool import StrictTestModelAdapter
from tests.adapters.conftest import REPLY_OK_SCRIPT


class _FakeCallExecutor:
    """记录调用并把调用转发给 StrictTestModelAdapter 的 call executor。"""

    def __init__(self, adapter: StrictTestModelAdapter) -> None:
        self._adapter = adapter
        self.specs: list[dict] = []

    def call(self, spec: dict, *, stage: str, attempt_no: int, candidate_no: int | None = None) -> dict:
        self.specs.append(spec)
        return self._adapter.generate(spec)

    def record_ids(self) -> list[str]:
        return [f"fake-call-{i}" for i in range(len(self.specs))]


def _run(script, item, package_context=None):
    adapter = ReplyModeAdapter()
    model = StrictTestModelAdapter(script)
    executor = _FakeCallExecutor(model)
    job = adapter.prepare(item, package_context or {})
    return adapter.generate(job, executor), executor


def test_reply_success_single_call(reply_item, package_context):
    payload, executor = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    assert "mode_failure" not in payload
    # 2026-08-05 提速：semantic+style 合并为单次调用
    assert [s["role"] for s in executor.specs] == ["semantic"]
    messages = payload["target"]["messages"]
    assert messages[0]["role"] == "human"
    assert messages[-1]["role"] == "assistant"
    assert len(messages) == 4
    assert payload["input"]["scene"] == "晚上在客厅，她在打游戏你在加班"


def test_merge_prompt_includes_contract_and_behaviors(reply_item, package_context):
    """合并 prompt 同时含风格合同与玩家视角；角色秘密仍不可见。"""
    _, executor = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "风格合同" in prompt
    assert "你们合租" in prompt


def test_style_prompt_includes_style_contract(reply_item, package_context):
    _, executor = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    style_prompt = executor.specs[0]["messages"][0]["content"]
    assert "风格合同" in style_prompt
    # 叙述视角由风格合同决定（阶段 3 模板通用化后不再硬编码第一人称）
    assert "叙述视角" in style_prompt


def test_parse_failure_returns_mode_failure(reply_item, package_context):
    script = [{"content": "不是 JSON 的输出"}]
    payload, _ = _run(script, reply_item, package_context)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "parse_error"
    assert payload["retryable"] is True


def test_non_alternating_output_fails_structure(reply_item, package_context):
    script = [
        {
            "content": (
                '{"human_turns": ["嗯"], "assistant_propositions": ["命题甲。"], "assistant_tones": ["n"], '
                '"messages": ['
                '{"role": "human", "text": "嗯"}, {"role": "human", "text": "又一条 human"}, '
                '{"role": "assistant", "text": "命题甲。"}, {"role": "assistant", "text": "命题甲。"}]}'
            )
        },
    ]
    payload, _ = _run(script, reply_item, package_context)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "style_failure"


def test_turn_count_out_of_bounds_fails(reply_item, package_context):
    reply_item["input"]["turn_bounds"] = [2, 2]
    script = REPLY_OK_SCRIPT  # 4 条消息超出 [2,2]
    payload, _ = _run(script, reply_item, package_context)
    assert payload["mode_failure"] is True
    assert "轮数" in payload["reason"]


def test_semantic_preservation_failure(reply_item, package_context):
    script = [
        {
            "content": (
                '{"human_turns": ["嗯"], "assistant_propositions": ["河畔公寓的猫叫豆包。"], "assistant_tones": ["n"], '
                '"messages": ['
                '{"role": "human", "text": "嗯"}, '
                '{"role": "assistant", "text": "完全跑题的回答，跟猫没有任何关系。"}, '
                '{"role": "human", "text": "还有呢？"}, '
                '{"role": "assistant", "text": "没别的了。"}]}'
            )
        },
    ]
    payload, _ = _run(script, reply_item, package_context)
    assert payload["mode_failure"] is True
    assert payload["error_code"] == "style_failure"
    assert "语义保持" in payload["reason"]


def test_missing_plan_input_uses_defaults(reply_item, package_context):
    item = dict(reply_item, input={})
    payload, _ = _run(REPLY_OK_SCRIPT, item, package_context)
    assert "mode_failure" not in payload
    assert payload["input"]["scene"] == "日常"


def test_render_training_supervises_all_assistant_messages(reply_item, package_context):
    payload, _ = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    adapter = ReplyModeAdapter()
    candidate = dict(payload, sample_id="s1", mode="REPLY")
    record = adapter.render_training(candidate, package_context)
    assert record.mode == "REPLY"
    assert record.supervised_message_indexes == [1, 3]
    assert record.supervised_token_count == sum(
        len(record.messages[i]["content"]) for i in [1, 3]
    )
    assert record.render_profile_id == "reply-runtime-v1"
