from __future__ import annotations

from tools.run_gemma_nf4_runtime_smoke import _plain_gate, _plain_messages


def test_plain_runtime_gate_requires_correct_current_appointment() -> None:
    assert _plain_gate("不是，应该星期六上午十点去。")["passed"] is True
    assert _plain_gate("嗯，星期五下午去就行。")["passed"] is False


def test_plain_runtime_smoke_contains_grounding_boundary() -> None:
    messages = _plain_messages()
    assert messages[-1]["content"] == "所以我还是星期五下午去，对吗？"
    assert "男主的提问、猜测或反问不能覆盖已有事实" in messages[0]["content"]
