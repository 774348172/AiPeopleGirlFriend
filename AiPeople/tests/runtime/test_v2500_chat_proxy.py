from __future__ import annotations

from runtime._prompt import QIN_WEIXI_REPLY_SYSTEM
from tools.v2500_chat_proxy import inject_runtime_contract


def test_inject_runtime_contract_replaces_system_and_preserves_history() -> None:
    original = {
        "model": "qinweixi-v2500",
        "messages": [
            {"role": "system", "content": "你是一个通用助手。"},
            {"role": "user", "content": "第一句话"},
            {"role": "assistant", "content": "第一条回答"},
            {"role": "user", "content": "你是谁？"},
        ],
        "chat_template_kwargs": {"enable_thinking": True, "custom": "kept"},
        "stream": True,
    }

    updated = inject_runtime_contract(original)

    assert updated["messages"][0] == {
        "role": "system",
        "content": QIN_WEIXI_REPLY_SYSTEM,
    }
    assert [message["role"] for message in updated["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert updated["messages"][1:] == original["messages"][1:]
    assert updated["chat_template_kwargs"] == {
        "enable_thinking": False,
        "custom": "kept",
    }
    assert updated["stream"] is True
    assert original["messages"][0]["content"] == "你是一个通用助手。"


def test_inject_runtime_contract_adds_anchor_to_plain_webui_request() -> None:
    updated = inject_runtime_contract(
        {"messages": [{"role": "user", "content": "你是谁？"}]}
    )

    assert updated["messages"] == [
        {"role": "system", "content": QIN_WEIXI_REPLY_SYSTEM},
        {"role": "user", "content": "你是谁？"},
    ]
    assert updated["chat_template_kwargs"]["enable_thinking"] is False
